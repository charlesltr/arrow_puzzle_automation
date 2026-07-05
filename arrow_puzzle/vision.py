from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class Cell:
    index: int
    x: float
    y: float
    radius: float
    value: int
    confidence: float


@dataclass(frozen=True)
class RecognizedBoard:
    cells: list[Cell]
    image_width: int
    image_height: int
    roi_origin: tuple[int, int] = (0, 0)

    @property
    def centers(self) -> list[tuple[float, float]]:
        return [(cell.x, cell.y) for cell in self.cells]

    @property
    def values(self) -> list[int]:
        return [cell.value for cell in self.cells]

    @property
    def absolute_centers(self) -> list[tuple[float, float]]:
        ox, oy = self.roi_origin
        return [(cell.x + ox, cell.y + oy) for cell in self.cells]


def load_image(path: str | Path) -> np.ndarray:
    import cv2

    data = np.fromfile(str(path), dtype=np.uint8)
    image = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"failed to read image: {path}")
    return image


def recognize_board(
    image: np.ndarray,
    *,
    expected_cells: int = 37,
    roi_origin: tuple[int, int] = (0, 0),
    manual_values: str | None = None,
) -> RecognizedBoard:
    circles = detect_circles(image, expected_cells=expected_cells)
    recognizer = DigitRecognizer()

    values_override = _parse_manual_values(manual_values, len(circles)) if manual_values else None
    cells: list[Cell] = []
    for idx, (x, y, radius) in enumerate(circles):
        if values_override is None:
            value, confidence = recognizer.classify(_crop_digit(image, x, y, radius))
        else:
            value, confidence = values_override[idx], 1.0
        cells.append(Cell(idx, x, y, radius, value, confidence))

    return RecognizedBoard(
        cells=cells,
        image_width=int(image.shape[1]),
        image_height=int(image.shape[0]),
        roi_origin=roi_origin,
    )


def detect_circles(image: np.ndarray, *, expected_cells: int = 37) -> list[tuple[float, float, float]]:
    import cv2

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (7, 7), 1.5)
    h, w = gray.shape[:2]
    min_dim = min(w, h)
    min_radius = max(10, int(min_dim / 26))
    max_radius = max(min_radius + 5, int(min_dim / 10))
    min_dist = max(20, int(min_dim / 13))

    candidates: list[tuple[float, float, float]] = []
    for param2 in (18, 22, 15, 26, 12):
        circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=min_dist,
            param1=80,
            param2=param2,
            minRadius=min_radius,
            maxRadius=max_radius,
        )
        if circles is not None:
            candidates.extend((float(x), float(y), float(r)) for x, y, r in circles[0])
        merged = _dedupe_circles(candidates)
        if len(merged) >= expected_cells:
            return _select_board_circles(merged, expected_cells)

    contours = _contour_circles(gray, min_radius, max_radius)
    candidates.extend(contours)
    merged = _dedupe_circles(candidates)
    if len(merged) < expected_cells:
        raise ValueError(
            f"only detected {len(merged)} cells; try selecting a tighter ROI around the board"
        )
    return _select_board_circles(merged, expected_cells)


class DigitRecognizer:
    def __init__(self) -> None:
        self.templates = _build_digit_templates()

    def classify(self, crop: np.ndarray) -> tuple[int, float]:
        sample = _normalize_digit(crop)
        best_value = 1
        best_score = -1.0
        for value, templates in self.templates.items():
            for template in templates:
                score = _binary_similarity(sample, template)
                if score > best_score:
                    best_value = value
                    best_score = score
        return best_value, float(best_score)


def board_text(board: RecognizedBoard) -> str:
    rows = _rows_from_cells(board.cells)
    lines: list[str] = []
    for row in rows:
        lines.append(" ".join(str(cell.value) for cell in row))
    return "\n".join(lines)


def overlay_solution(
    image: np.ndarray,
    board: RecognizedBoard,
    taps: Iterable[int],
    output_path: str | Path,
) -> None:
    import cv2

    annotated = image.copy()
    for cell, tap_count in zip(board.cells, taps):
        color = (70, 210, 70) if tap_count else (130, 130, 130)
        cv2.circle(annotated, (round(cell.x), round(cell.y)), round(cell.radius), color, 2)
        label = str(tap_count)
        cv2.putText(
            annotated,
            label,
            (round(cell.x - cell.radius * 0.25), round(cell.y + cell.radius * 0.22)),
            cv2.FONT_HERSHEY_SIMPLEX,
            max(0.45, cell.radius / 32),
            (30, 240, 30) if tap_count else (170, 170, 170),
            2,
            cv2.LINE_AA,
        )
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cv2.imencode(output.suffix or ".png", annotated)[1].tofile(str(output))


def _parse_manual_values(text: str, expected: int) -> list[int]:
    values = [int(ch) for ch in text if ch in "123456"]
    if len(values) != expected:
        raise ValueError(f"manual values contain {len(values)} digits; expected {expected}")
    return values


def _dedupe_circles(circles: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    circles = sorted(circles, key=lambda c: c[2], reverse=True)
    merged: list[tuple[float, float, float]] = []
    for circle in circles:
        x, y, r = circle
        if any(((x - mx) ** 2 + (y - my) ** 2) ** 0.5 < max(8, min(r, mr) * 0.5) for mx, my, mr in merged):
            continue
        merged.append(circle)
    return merged


def _select_board_circles(
    circles: list[tuple[float, float, float]], expected: int
) -> list[tuple[float, float, float]]:
    if len(circles) == expected:
        selected = circles
    else:
        radii = np.array([c[2] for c in circles], dtype=float)
        median_r = float(np.median(radii))
        filtered = [c for c in circles if median_r * 0.65 <= c[2] <= median_r * 1.35]
        if len(filtered) >= expected:
            circles = filtered

        cx = float(np.median([c[0] for c in circles]))
        cy = float(np.median([c[1] for c in circles]))
        selected = sorted(circles, key=lambda c: (c[0] - cx) ** 2 + (c[1] - cy) ** 2)[:expected]
    return _sort_spatial(selected)


def _sort_spatial(circles: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    rows: list[list[tuple[float, float, float]]] = []
    median_r = float(np.median([c[2] for c in circles]))
    row_threshold = max(12.0, median_r * 0.85)
    for circle in sorted(circles, key=lambda c: c[1]):
        for row in rows:
            if abs(float(np.mean([c[1] for c in row])) - circle[1]) <= row_threshold:
                row.append(circle)
                break
        else:
            rows.append([circle])
    for row in rows:
        row.sort(key=lambda c: c[0])
    rows.sort(key=lambda row: float(np.mean([c[1] for c in row])))
    return [circle for row in rows for circle in row]


def _contour_circles(
    gray: np.ndarray, min_radius: int, max_radius: int
) -> list[tuple[float, float, float]]:
    import cv2

    edges = cv2.Canny(gray, 50, 140)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out: list[tuple[float, float, float]] = []
    for contour in contours:
        (x, y), radius = cv2.minEnclosingCircle(contour)
        if min_radius <= radius <= max_radius:
            area = cv2.contourArea(contour)
            circle_area = np.pi * radius * radius
            if circle_area and area / circle_area > 0.35:
                out.append((float(x), float(y), float(radius)))
    return out


def _crop_digit(image: np.ndarray, x: float, y: float, radius: float) -> np.ndarray:
    h, w = image.shape[:2]
    half = max(6, int(radius * 0.42))
    left = max(0, int(x) - half)
    right = min(w, int(x) + half)
    top = max(0, int(y) - half)
    bottom = min(h, int(y) + half)
    return image[top:bottom, left:right]


def _normalize_digit(crop: np.ndarray) -> np.ndarray:
    import cv2

    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY) if crop.ndim == 3 else crop
    if gray.size == 0:
        return np.zeros((32, 32), dtype=np.uint8)

    threshold = max(115, int(np.percentile(gray, 88)))
    binary = (gray >= threshold).astype(np.uint8)
    ys, xs = np.where(binary > 0)
    if len(xs) == 0:
        return np.zeros((32, 32), dtype=np.uint8)

    pad = 2
    x0 = max(0, int(xs.min()) - pad)
    x1 = min(binary.shape[1], int(xs.max()) + pad + 1)
    y0 = max(0, int(ys.min()) - pad)
    y1 = min(binary.shape[0], int(ys.max()) + pad + 1)
    digit = binary[y0:y1, x0:x1] * 255

    scale = min(24 / max(1, digit.shape[1]), 26 / max(1, digit.shape[0]))
    new_w = max(1, int(round(digit.shape[1] * scale)))
    new_h = max(1, int(round(digit.shape[0] * scale)))
    resized = cv2.resize(digit, (new_w, new_h), interpolation=cv2.INTER_NEAREST)
    canvas = np.zeros((32, 32), dtype=np.uint8)
    ox = (32 - new_w) // 2
    oy = (32 - new_h) // 2
    canvas[oy : oy + new_h, ox : ox + new_w] = resized
    return (canvas > 0).astype(np.uint8)


def _build_digit_templates() -> dict[int, list[np.ndarray]]:
    templates: dict[int, list[np.ndarray]] = {value: [] for value in range(1, 7)}
    font_paths = _candidate_font_paths()
    for value in range(1, 7):
        for size in (22, 24, 26, 28, 30):
            for font_path in font_paths:
                try:
                    font = ImageFont.truetype(str(font_path), size)
                except OSError:
                    continue
                templates[value].append(_render_digit_template(str(value), font))
        if not templates[value]:
            font = ImageFont.load_default()
            templates[value].append(_render_digit_template(str(value), font))
    return templates


def _candidate_font_paths() -> list[Path]:
    win_fonts = Path("C:/Windows/Fonts")
    return [
        win_fonts / "times.ttf",
        win_fonts / "timesbd.ttf",
        win_fonts / "georgia.ttf",
        win_fonts / "cambria.ttc",
        win_fonts / "simsun.ttc",
        win_fonts / "arial.ttf",
    ]


def _render_digit_template(text: str, font: ImageFont.ImageFont) -> np.ndarray:
    image = Image.new("L", (48, 48), 0)
    draw = ImageDraw.Draw(image)
    bbox = draw.textbbox((0, 0), text, font=font)
    x = (48 - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (48 - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((x, y), text, fill=255, font=font)
    arr = np.array(image)
    return _normalize_digit(arr)


def _binary_similarity(a: np.ndarray, b: np.ndarray) -> float:
    a_bool = a.astype(bool)
    b_bool = b.astype(bool)
    union = np.logical_or(a_bool, b_bool).sum()
    if union == 0:
        return 0.0
    iou = np.logical_and(a_bool, b_bool).sum() / union
    pixel_match = (a_bool == b_bool).mean()
    return float(iou * 0.75 + pixel_match * 0.25)


def _rows_from_cells(cells: list[Cell]) -> list[list[Cell]]:
    rows: list[list[Cell]] = []
    if not cells:
        return rows
    median_r = float(np.median([cell.radius for cell in cells]))
    threshold = max(12.0, median_r * 0.85)
    for cell in sorted(cells, key=lambda c: c.y):
        for row in rows:
            if abs(float(np.mean([c.y for c in row])) - cell.y) <= threshold:
                row.append(cell)
                break
        else:
            rows.append([cell])
    for row in rows:
        row.sort(key=lambda c: c.x)
    rows.sort(key=lambda row: float(np.mean([c.y for c in row])))
    return rows
