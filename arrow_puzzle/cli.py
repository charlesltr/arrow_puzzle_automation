from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import numpy as np

from .solver import click_matrix_from_centers, solve_board
from .vision import board_text, load_image, overlay_solution, recognize_board


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arrow-puzzle", description="Recognize and solve the arrow puzzle.")
    sub = parser.add_subparsers(dest="command", required=True)

    devices = sub.add_parser("devices", help="List connected ADB devices.")
    devices.set_defaults(func=cmd_devices)

    connect = sub.add_parser("connect-emulators", help="Try common local Android emulator ADB ports.")
    connect.set_defaults(func=cmd_connect_emulators)

    solve = sub.add_parser("solve", help="Recognize a board, solve it, and optionally click it.")
    source = solve.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="Read a screenshot/image file.")
    source.add_argument("--screen", action="store_true", help="Select a screen region with the mouse.")
    source.add_argument("--adb-screenshot", action="store_true", help="Capture screenshot from an Android device.")
    solve.add_argument("--roi", help="Crop ROI as x,y,w,h after loading image or adb screenshot.")
    solve.add_argument("--device", default="auto", help="ADB serial, or auto for interactive selection.")
    solve.add_argument("--backend", choices=["dry-run", "mouse", "adb", "maatouch"], default="dry-run")
    solve.add_argument("--expected-cells", type=int, default=37)
    solve.add_argument("--manual-values", help="Override OCR with digits in detected spatial order.")
    solve.add_argument("--review", action="store_true", help="Print OCR result and allow manual correction.")
    solve.add_argument("--annotate", default="debug/solution.png", help="Write a solution overlay image.")
    solve.add_argument("--delay", type=float, default=0.04, help="Delay between physical taps.")
    solve.add_argument("--maatouch-bin", help="Path to MaaTouch binary. If omitted, try latest GitHub release.")
    solve.add_argument("--maatouch-port", type=int, default=11180)
    solve.set_defaults(func=cmd_solve)

    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_devices(_: argparse.Namespace) -> int:
    from .android import adb_available, list_devices

    if not adb_available():
        print("adb was not found on PATH")
        return 1
    for device in list_devices():
        print(
            f"{device.serial}\t{device.state}\t{device.emulator}\t"
            f"{device.manufacturer} {device.model}\tABI={device.abi}"
        )
    return 0


def cmd_connect_emulators(_: argparse.Namespace) -> int:
    from .android import adb_available, connect_known_emulators

    if not adb_available():
        print("adb was not found on PATH")
        return 1
    successes = connect_known_emulators()
    if successes:
        print("Connected or already connected:")
        for serial in successes:
            print(f"  {serial}")
    else:
        print("No common local emulator ports responded.")
    return 0


def cmd_solve(args: argparse.Namespace) -> int:
    image, origin, device_serial = _load_source(args)
    image, origin = _apply_roi(image, origin, args.roi)

    board = recognize_board(
        image,
        expected_cells=args.expected_cells,
        roi_origin=origin,
        manual_values=args.manual_values,
    )
    if args.review:
        print("Recognized board:")
        print(board_text(board))
        correction = input("Press Enter to accept, or paste corrected digits 1..6: ").strip()
        if correction:
            board = recognize_board(
                image,
                expected_cells=args.expected_cells,
                roi_origin=origin,
                manual_values=correction,
            )

    matrix = click_matrix_from_centers(board.centers)
    solution = solve_board(board.values, matrix)

    print("Board:")
    print(board_text(board))
    print(f"Detected cells: {len(board.cells)}")
    print(f"Total taps: {solution.total_taps}")
    for cell, tap_count in zip(board.cells, solution.taps):
        if tap_count:
            ax, ay = board.absolute_centers[cell.index]
            print(f"  cell {cell.index:02d}: tap {tap_count}x at ({round(ax)}, {round(ay)}) value={cell.value}")

    overlay_solution(image, board, solution.taps, args.annotate)
    print(f"Overlay written to {Path(args.annotate).resolve()}")

    if args.backend == "dry-run":
        return 0

    _execute_solution(args, board.absolute_centers, solution.taps, device_serial)
    return 0


def _load_source(args: argparse.Namespace) -> tuple[np.ndarray, tuple[int, int], str | None]:
    if args.image:
        return load_image(args.image), (0, 0), None
    if args.screen:
        from .capture import select_screen_roi

        image, origin = select_screen_roi()
        return image, origin, None
    if args.adb_screenshot:
        from .android import choose_device, screencap

        device = choose_device(args.device)
        path = Path("captures") / f"{device.serial.replace(':', '_')}.png"
        screencap(device.serial, path)
        return load_image(path), (0, 0), device.serial
    raise AssertionError("unreachable source state")


def _apply_roi(
    image: np.ndarray, origin: tuple[int, int], roi_text: str | None
) -> tuple[np.ndarray, tuple[int, int]]:
    if not roi_text:
        return image, origin
    parts = [int(part.strip()) for part in roi_text.split(",")]
    if len(parts) != 4:
        raise ValueError("--roi must be x,y,w,h")
    x, y, w, h = parts
    if w <= 0 or h <= 0:
        raise ValueError("--roi width and height must be positive")
    ox, oy = origin
    return image[y : y + h, x : x + w].copy(), (ox + x, oy + y)


def _execute_solution(
    args: argparse.Namespace,
    centers: list[tuple[float, float]],
    taps: list[int],
    source_device_serial: str | None,
) -> None:
    if args.backend == "mouse":
        import pyautogui

        pyautogui.PAUSE = args.delay
        for (x, y), count in zip(centers, taps):
            for _ in range(count):
                pyautogui.click(x=round(x), y=round(y))
        return

    if args.backend == "adb":
        from .android import choose_device, tap_adb

        serial = source_device_serial or choose_device(args.device).serial
        for (x, y), count in zip(centers, taps):
            for _ in range(count):
                tap_adb(serial, x, y)
                time.sleep(args.delay)
        return

    if args.backend == "maatouch":
        from .android import MaaTouchClient, choose_device

        serial = source_device_serial or choose_device(args.device).serial
        client = MaaTouchClient(
            serial,
            local_port=args.maatouch_port,
            binary_path=args.maatouch_bin,
        )
        try:
            client.ensure_ready()
            for (x, y), count in zip(centers, taps):
                for _ in range(count):
                    client.tap(x, y)
                    time.sleep(args.delay)
        finally:
            client.close()
        return

    raise ValueError(f"unknown backend: {args.backend}")


if __name__ == "__main__":
    raise SystemExit(main())
