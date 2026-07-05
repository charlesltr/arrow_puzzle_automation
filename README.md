# Arrow Puzzle Automation

Local helper for recognizing and solving the Exponential Idle arrow puzzle from a screenshot.

The project has three layers:

1. Local vision: detect the honeycomb cells and read values `1..6`.
2. Solver: solve the board as a modulo-6 linear puzzle.
3. Executor: click by desktop mouse, plain ADB, or MaaTouch.

## Install

```powershell
python -m pip install -r requirements.txt
```

Or install the local command:

```powershell
python -m pip install -e .
arrow-puzzle --help
```

ADB is optional unless you want Android-device control. The tool will automatically try `D:\leidian\LDPlayer9\adb.exe`; you can also set `ARROW_PUZZLE_ADB` or pass `--adb-path`.

## Quick Start

Select the puzzle region on your desktop, recognize it, solve it, and only print the taps:

```powershell
python -m arrow_puzzle.cli solve --screen --review
```

If the OCR result is wrong, `--review` lets you paste corrected digits in the displayed cell order.

After the dry-run looks right, execute by desktop mouse:

```powershell
python -m arrow_puzzle.cli solve --screen --review --backend mouse
```

## Android / Emulator

List connected Android devices:

```powershell
python -m arrow_puzzle.cli devices
```

Use the LDPlayer ADB explicitly:

```powershell
python -m arrow_puzzle.cli devices --adb-path D:\leidian\LDPlayer9\adb.exe
```

Try common local emulator ADB ports for Android Emulator, BlueStacks, NoxPlayer, MuMu, LDPlayer, MEmu, Genymotion, and WSA:

```powershell
python -m arrow_puzzle.cli connect-emulators
```

Or set it once for the current PowerShell:

```powershell
$env:ARROW_PUZZLE_ADB = 'D:\leidian\LDPlayer9\adb.exe'
```

Capture from an Android device, crop to the puzzle area, and dry-run:

```powershell
python -m arrow_puzzle.cli solve --adb-screenshot --device auto --roi 0,300,720,760 --review
```

Use plain ADB tapping:

```powershell
python -m arrow_puzzle.cli solve --adb-screenshot --device auto --roi 0,300,720,760 --review --backend adb
```

The default physical click timing is a 25 ms press with a 40 ms interval. Override it with `--tap-duration` and `--delay`.

Use MaaTouch:

```powershell
python -m arrow_puzzle.cli solve --adb-screenshot --device auto --roi 0,300,720,760 --review --backend maatouch
```

Run continuously until you type anything in the terminal and press Enter:

```powershell
python -m arrow_puzzle.cli loop --device emulator-5556 --roi 0,300,720,760 --backend adb
```

The loop screenshots every 5 seconds. If the board is not complete it solves and taps it, waits 500 ms, confirms completion, and taps the bottom reward button to start the next game. If the confirm screenshot is still not complete, it performs one residual/endgame solve before checking again.

You can also pass a local MaaTouch binary:

```powershell
python -m arrow_puzzle.cli solve --adb-screenshot --backend maatouch --maatouch-bin C:\path\to\maatouch
```

MaaTouch uses the minitouch-style protocol documented by [MaaAssistantArknights/MaaTouch](https://github.com/MaaAssistantArknights/MaaTouch): press (`d`), commit (`c`), release (`u`), commit (`c`).

## Notes

- The default board size is 37 cells, matching the Hard board shown in the guide.
- The solver does not depend on the 8 hard end-case lookup table. It solves the full click system over modulo 6 by combining modulo 2 and modulo 3 solutions.
- Always run dry-run first. The overlay image is written to `debug/solution.png` by default.
