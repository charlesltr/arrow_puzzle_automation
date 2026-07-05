from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageTk


def select_screen_roi() -> tuple[np.ndarray, tuple[int, int]]:
    import pyautogui
    import tkinter as tk

    screenshot = pyautogui.screenshot()
    root = tk.Tk()
    root.attributes("-fullscreen", True)
    root.attributes("-topmost", True)
    root.configure(cursor="crosshair")

    screen_w, screen_h = screenshot.size
    photo = ImageTk.PhotoImage(screenshot)
    canvas = tk.Canvas(root, width=screen_w, height=screen_h, highlightthickness=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, image=photo, anchor="nw")

    state: dict[str, int | None] = {"x0": None, "y0": None, "rect": None}
    result: dict[str, tuple[int, int, int, int] | None] = {"box": None}

    def on_down(event: tk.Event) -> None:
        state["x0"], state["y0"] = event.x, event.y
        state["rect"] = canvas.create_rectangle(event.x, event.y, event.x, event.y, outline="#39d353", width=2)

    def on_drag(event: tk.Event) -> None:
        if state["rect"] is not None and state["x0"] is not None and state["y0"] is not None:
            canvas.coords(state["rect"], state["x0"], state["y0"], event.x, event.y)

    def on_up(event: tk.Event) -> None:
        if state["x0"] is None or state["y0"] is None:
            return
        x0, x1 = sorted([state["x0"], event.x])
        y0, y1 = sorted([state["y0"], event.y])
        result["box"] = (int(x0), int(y0), int(x1), int(y1))
        root.destroy()

    def on_escape(_: tk.Event) -> None:
        root.destroy()

    canvas.bind("<ButtonPress-1>", on_down)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_up)
    root.bind("<Escape>", on_escape)
    root.mainloop()

    if result["box"] is None:
        raise RuntimeError("ROI selection was cancelled")
    x0, y0, x1, y1 = result["box"]
    cropped = screenshot.crop((x0, y0, x1, y1))
    return _pil_to_bgr(cropped), (x0, y0)


def load_roi_from_image(path: str | Path) -> tuple[np.ndarray, tuple[int, int]]:
    from .vision import load_image

    return load_image(path), (0, 0)


def _pil_to_bgr(image: Image.Image) -> np.ndarray:
    rgb = np.array(image.convert("RGB"))
    return rgb[:, :, ::-1].copy()
