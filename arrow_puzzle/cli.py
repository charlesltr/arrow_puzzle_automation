from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import numpy as np

from .solver import click_matrix_from_centers, solve_board
from .vision import board_text, find_completion_button, load_image, overlay_solution, recognize_board


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arrow-puzzle", description="Recognize and solve the arrow puzzle.")
    sub = parser.add_subparsers(dest="command", required=True)

    devices = sub.add_parser("devices", help="List connected ADB devices.")
    _add_adb_options(devices)
    devices.set_defaults(func=cmd_devices)

    connect = sub.add_parser("connect-emulators", help="Try common local Android emulator ADB ports.")
    _add_adb_options(connect)
    connect.set_defaults(func=cmd_connect_emulators)

    solve = sub.add_parser("solve", help="Recognize a board, solve it, and optionally click it.")
    source = solve.add_mutually_exclusive_group(required=True)
    source.add_argument("--image", help="Read a screenshot/image file.")
    source.add_argument("--screen", action="store_true", help="Select a screen region with the mouse.")
    source.add_argument("--adb-screenshot", action="store_true", help="Capture screenshot from an Android device.")
    _add_adb_options(solve)
    solve.add_argument("--roi", help="Crop ROI as x,y,w,h after loading image or adb screenshot.")
    solve.add_argument("--device", default="auto", help="ADB serial, or auto for interactive selection.")
    solve.add_argument("--backend", choices=["dry-run", "mouse", "adb", "maatouch"], default="dry-run")
    solve.add_argument("--expected-cells", type=int, default=37)
    solve.add_argument("--manual-values", help="Override OCR with digits in detected spatial order.")
    solve.add_argument("--review", action="store_true", help="Print OCR result and allow manual correction.")
    solve.add_argument("--annotate", default="debug/solution.png", help="Write a solution overlay image.")
    solve.add_argument("--delay", type=float, default=0.04, help="Delay between physical taps, in seconds.")
    solve.add_argument("--tap-duration", type=float, default=0.025, help="How long each tap is held, in seconds.")
    solve.add_argument("--maatouch-bin", help="Path to MaaTouch binary. If omitted, try latest GitHub release.")
    solve.add_argument("--maatouch-port", type=int, default=11180)
    solve.set_defaults(func=cmd_solve)

    loop = sub.add_parser("loop", help="Continuously solve games and start the next round when complete.")
    _add_adb_options(loop)
    loop.add_argument("--device", default="auto", help="ADB serial, or auto for interactive selection.")
    loop.add_argument("--roi", default="0,300,720,760", help="Puzzle crop ROI as x,y,w,h.")
    loop.add_argument("--backend", choices=["adb", "maatouch"], default="adb")
    loop.add_argument("--expected-cells", type=int, default=37)
    loop.add_argument("--interval", type=float, default=5.0, help="Seconds between confirmation screenshots.")
    loop.add_argument("--settle", type=float, default=0.5, help="Seconds to wait after the last tap before confirming completion.")
    loop.add_argument("--endgame-attempts", type=int, default=1, help="Extra residual solve attempts after the first solve.")
    loop.add_argument("--delay", type=float, default=0.04, help="Delay between physical taps, in seconds.")
    loop.add_argument("--tap-duration", type=float, default=0.025, help="How long each tap is held, in seconds.")
    loop.add_argument("--maatouch-bin", help="Path to MaaTouch binary. If omitted, try latest GitHub release.")
    loop.add_argument("--maatouch-port", type=int, default=11180)
    loop.add_argument("--annotate-dir", default="debug/loop", help="Directory for loop overlay images.")
    loop.set_defaults(func=cmd_loop)

    args = parser.parse_args(argv)
    _configure_adb(args)
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _add_adb_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--adb-path",
        help="Path to adb.exe. Defaults include D:\\leidian\\LDPlayer9\\adb.exe and ARROW_PUZZLE_ADB.",
    )


def _configure_adb(args: argparse.Namespace) -> None:
    if hasattr(args, "adb_path") and args.adb_path:
        from .android import set_adb_path

        set_adb_path(args.adb_path)


def cmd_devices(_: argparse.Namespace) -> int:
    from .android import adb_available, list_devices

    if not adb_available():
        print("adb was not found; pass --adb-path or set ARROW_PUZZLE_ADB")
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
        print("adb was not found; pass --adb-path or set ARROW_PUZZLE_ADB")
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


def cmd_loop(args: argparse.Namespace) -> int:
    from .android import choose_device

    device = choose_device(args.device)
    stop_event = _start_stop_listener()
    annotate_dir = Path(args.annotate_dir)
    annotate_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path = Path("captures") / f"{device.serial.replace(':', '_')}-loop.png"
    completed_rounds = 0
    checks = 0

    print("Loop running. Type anything and press Enter to stop.")
    while not stop_event.is_set():
        checks += 1
        try:
            full_image, board_image, board, solution = _capture_loop_state(args, device.serial, screenshot_path)
            overlay_solution(board_image, board, solution.taps, annotate_dir / f"check-{checks:04d}.png")

            timestamp = time.strftime("%H:%M:%S")
            if solution.total_taps == 0:
                completed_rounds += 1
                _tap_next_game(args, full_image, device.serial, completed_rounds, timestamp)
            else:
                print(f"[{timestamp}] solving board with {solution.total_taps} taps.")
                _execute_solution(args, board.absolute_centers, solution.taps, device.serial)
                completed_rounds = _confirm_after_solve(
                    args,
                    device.serial,
                    screenshot_path,
                    annotate_dir,
                    checks,
                    completed_rounds,
                )
        except Exception as exc:
            print(f"[{time.strftime('%H:%M:%S')}] check failed: {exc}", file=sys.stderr)

        stop_event.wait(max(0.1, args.interval))

    print(f"Loop stopped. Completed rounds: {completed_rounds}; checks: {checks}")
    return 0


def _capture_loop_state(
    args: argparse.Namespace,
    serial: str,
    screenshot_path: Path,
):
    from .android import screencap

    screencap(serial, screenshot_path)
    full_image = load_image(screenshot_path)
    board_image, origin = _apply_roi(full_image, (0, 0), args.roi)
    board = recognize_board(
        board_image,
        expected_cells=args.expected_cells,
        roi_origin=origin,
    )
    matrix = click_matrix_from_centers(board.centers)
    solution = solve_board(board.values, matrix)
    return full_image, board_image, board, solution


def _confirm_after_solve(
    args: argparse.Namespace,
    serial: str,
    screenshot_path: Path,
    annotate_dir: Path,
    check_index: int,
    completed_rounds: int,
) -> int:
    time.sleep(max(0.0, args.settle))
    for attempt in range(args.endgame_attempts + 1):
        full_image, board_image, board, solution = _capture_loop_state(args, serial, screenshot_path)
        overlay_solution(
            board_image,
            board,
            solution.taps,
            annotate_dir / f"confirm-{check_index:04d}-{attempt:02d}.png",
        )
        timestamp = time.strftime("%H:%M:%S")
        if solution.total_taps == 0:
            completed_rounds += 1
            _tap_next_game(args, full_image, serial, completed_rounds, timestamp)
            return completed_rounds
        if attempt >= args.endgame_attempts:
            print(
                f"[{timestamp}] board still unfinished after residual solve attempts; "
                f"remaining taps: {solution.total_taps}"
            )
            return completed_rounds

        print(f"[{timestamp}] residual board detected; solving endgame with {solution.total_taps} taps.")
        _execute_solution(args, board.absolute_centers, solution.taps, serial)
        time.sleep(max(0.0, args.settle))
    return completed_rounds


def _tap_next_game(
    args: argparse.Namespace,
    full_image: np.ndarray,
    serial: str,
    completed_rounds: int,
    timestamp: str,
) -> None:
    x, y = find_completion_button(full_image)
    print(
        f"[{timestamp}] completed board detected; tapping next-game button "
        f"at ({round(x)}, {round(y)}). Completed rounds: {completed_rounds}"
    )
    _execute_solution(args, [(x, y)], [1], serial)


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

        for (x, y), count in zip(centers, taps):
            for _ in range(count):
                pyautogui.mouseDown(x=round(x), y=round(y))
                time.sleep(args.tap_duration)
                pyautogui.mouseUp(x=round(x), y=round(y))
                time.sleep(args.delay)
        return

    if args.backend == "adb":
        from .android import choose_device

        serial = source_device_serial or choose_device(args.device).serial
        _tap_points(args, centers, taps, serial)
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
                    client.tap(x, y, duration_ms=round(args.tap_duration * 1000))
                    time.sleep(args.delay)
        finally:
            client.close()
        return

    raise ValueError(f"unknown backend: {args.backend}")


def _tap_points(
    args: argparse.Namespace,
    centers: list[tuple[float, float]],
    taps: list[int],
    serial: str,
) -> None:
    from .android import tap_adb

    for (x, y), count in zip(centers, taps):
        for _ in range(count):
            tap_adb(serial, x, y, duration_ms=round(args.tap_duration * 1000))
            time.sleep(args.delay)


def _start_stop_listener() -> threading.Event:
    stop_event = threading.Event()

    def wait_for_input() -> None:
        try:
            sys.stdin.readline()
        finally:
            stop_event.set()

    threading.Thread(target=wait_for_input, daemon=True).start()
    return stop_event


if __name__ == "__main__":
    raise SystemExit(main())
