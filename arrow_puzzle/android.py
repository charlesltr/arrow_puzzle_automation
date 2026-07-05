from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path


ADB_DEFAULT_PORTS = {
    "Android Emulator": [5554, 5556, 5558, 5560],
    "BlueStacks": [5555, 5556],
    "NoxPlayer": [62001, 62025, 62026],
    "MuMu": [7555, 16384, 16416],
    "LDPlayer": [5555, 5557],
    "MEmu": [21503],
    "Genymotion": [5555],
    "WSA": [58526],
}

ADB_ENV_VAR = "ARROW_PUZZLE_ADB"
KNOWN_ADB_PATHS = [
    Path("D:/leidian/LDPlayer9/adb.exe"),
    Path("C:/leidian/LDPlayer9/adb.exe"),
    Path("D:/leidian/LDPlayer4/adb.exe"),
    Path("C:/leidian/LDPlayer4/adb.exe"),
]

_ADB_PATH_OVERRIDE: Path | None = None


@dataclass(frozen=True)
class AndroidDevice:
    serial: str
    state: str
    description: str
    manufacturer: str = ""
    model: str = ""
    brand: str = ""
    abi: str = ""
    emulator: str = "Unknown"


def set_adb_path(path: str | Path | None) -> None:
    global _ADB_PATH_OVERRIDE
    _ADB_PATH_OVERRIDE = Path(path) if path else None


def adb_path() -> str:
    for candidate in _adb_candidates():
        if candidate.is_file():
            return str(candidate)
    searched = ", ".join(str(path) for path in _adb_candidates())
    raise RuntimeError(
        f"adb was not found. Set {ADB_ENV_VAR}, pass --adb-path, or put adb on PATH. "
        f"Searched: {searched}"
    )


def run_adb(args: list[str], *, serial: str | None = None, timeout: float = 20) -> subprocess.CompletedProcess[str]:
    cmd = [adb_path()]
    if serial:
        cmd.extend(["-s", serial])
    cmd.extend(args)
    return subprocess.run(cmd, text=True, capture_output=True, timeout=timeout)


def list_devices() -> list[AndroidDevice]:
    proc = run_adb(["devices", "-l"])
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())

    devices: list[AndroidDevice] = []
    for line in proc.stdout.splitlines()[1:]:
        line = line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        description = " ".join(parts[2:])
        details = _device_props(serial) if state == "device" else {}
        devices.append(
            AndroidDevice(
                serial=serial,
                state=state,
                description=description,
                manufacturer=details.get("ro.product.manufacturer", ""),
                model=details.get("ro.product.model", ""),
                brand=details.get("ro.product.brand", ""),
                abi=details.get("ro.product.cpu.abi", ""),
                emulator=_detect_emulator(serial, description, details),
            )
        )
    return devices


def choose_device(serial: str | None = None) -> AndroidDevice:
    devices = list_devices()
    ready = [device for device in devices if device.state == "device"]
    if serial and serial != "auto":
        for device in devices:
            if device.serial == serial:
                if device.state != "device":
                    raise RuntimeError(f"device {serial} is not ready: {device.state}")
                return device
        raise RuntimeError(f"device {serial} was not found")

    if not ready:
        raise RuntimeError("no ready Android device found")
    if len(ready) == 1:
        return ready[0]

    print("Connected Android devices:")
    for i, device in enumerate(ready, start=1):
        label = f"{device.serial}  {device.emulator}  {device.manufacturer} {device.model}".strip()
        print(f"  {i}. {label}")
    while True:
        answer = input("Select device number: ").strip()
        if answer.isdigit() and 1 <= int(answer) <= len(ready):
            return ready[int(answer) - 1]


def connect_known_emulators() -> list[str]:
    """Try common local emulator adb ports. Returns successful serials."""
    successes: list[str] = []
    for ports in ADB_DEFAULT_PORTS.values():
        for port in ports:
            serial = f"127.0.0.1:{port}"
            proc = run_adb(["connect", serial], timeout=5)
            text = f"{proc.stdout}\n{proc.stderr}".lower()
            if "connected" in text or "already connected" in text:
                successes.append(serial)
    return sorted(set(successes))


def screencap(serial: str, output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    cmd = [adb_path(), "-s", serial, "exec-out", "screencap", "-p"]
    proc = subprocess.run(cmd, capture_output=True, timeout=20)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode("utf-8", errors="replace").strip())
    raw = proc.stdout
    if not raw:
        raise RuntimeError("adb screencap returned no data")
    output.write_bytes(raw)
    return output


def tap_adb(serial: str, x: float, y: float) -> None:
    proc = run_adb(["shell", "input", "tap", str(round(x)), str(round(y))], serial=serial, timeout=5)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())


class MaaTouchClient:
    def __init__(
        self,
        serial: str,
        *,
        local_port: int = 11180,
        binary_path: str | Path | None = None,
        cache_dir: str | Path = ".cache/maatouch",
    ) -> None:
        self.serial = serial
        self.local_port = local_port
        self.binary_path = Path(binary_path) if binary_path else None
        self.cache_dir = Path(cache_dir)
        self.remote_path = "/data/local/tmp/maatouch"
        self.sock: socket.socket | None = None

    def ensure_ready(self) -> None:
        binary = self.binary_path or self._download_binary()
        self._push_binary(binary)
        self._start_server()
        self._forward_port()
        self._connect_socket()

    def tap(self, x: float, y: float, *, pressure: int = 50, duration_ms: int = 35) -> None:
        if self.sock is None:
            self.ensure_ready()
        assert self.sock is not None
        xi, yi = round(x), round(y)
        self._send(f"d 0 {xi} {yi} {pressure}\nc\n")
        time.sleep(max(0, duration_ms) / 1000)
        self._send("u 0\nc\n")

    def close(self) -> None:
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def _download_binary(self) -> Path:
        device = choose_device(self.serial)
        abi = device.abi or "arm64-v8a"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        release = _github_json("https://api.github.com/repos/MaaAssistantArknights/MaaTouch/releases/latest")
        assets = release.get("assets", [])
        asset = _select_maatouch_asset(assets, abi)
        if not asset:
            raise RuntimeError(
                f"could not find a MaaTouch release asset for ABI {abi}; pass --maatouch-bin manually"
            )
        url = asset["browser_download_url"]
        filename = asset["name"]
        output = self.cache_dir / filename
        if not output.exists():
            print(f"Downloading MaaTouch asset: {filename}")
            urllib.request.urlretrieve(url, output)
        return output

    def _push_binary(self, binary: Path) -> None:
        proc = run_adb(["push", str(binary), self.remote_path], serial=self.serial, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        chmod = run_adb(["shell", "chmod", "755", self.remote_path], serial=self.serial, timeout=5)
        if chmod.returncode != 0:
            raise RuntimeError(chmod.stderr.strip() or chmod.stdout.strip())

    def _start_server(self) -> None:
        # nohup is not guaranteed on Android; the shell background form is enough for adb sessions.
        run_adb(["shell", f"{self.remote_path} >/dev/null 2>&1 &"], serial=self.serial, timeout=5)
        time.sleep(0.5)

    def _forward_port(self) -> None:
        proc = run_adb(
            ["forward", f"tcp:{self.local_port}", "localabstract:maatouch"],
            serial=self.serial,
            timeout=5,
        )
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())

    def _connect_socket(self) -> None:
        last_error: OSError | None = None
        for _ in range(20):
            try:
                self.sock = socket.create_connection(("127.0.0.1", self.local_port), timeout=2)
                self.sock.recv(512)
                return
            except OSError as exc:
                last_error = exc
                time.sleep(0.2)
        raise RuntimeError(f"failed to connect to MaaTouch: {last_error}")

    def _send(self, text: str) -> None:
        assert self.sock is not None
        self.sock.sendall(text.encode("ascii"))


def _device_props(serial: str) -> dict[str, str]:
    keys = [
        "ro.product.manufacturer",
        "ro.product.model",
        "ro.product.brand",
        "ro.product.name",
        "ro.hardware",
        "ro.kernel.qemu",
        "ro.product.cpu.abi",
    ]
    props: dict[str, str] = {}
    for key in keys:
        proc = run_adb(["shell", "getprop", key], serial=serial, timeout=3)
        if proc.returncode == 0:
            props[key] = proc.stdout.strip()
    return props


def _detect_emulator(serial: str, description: str, props: dict[str, str]) -> str:
    blob = " ".join([serial, description, *props.values()]).lower()
    if serial.startswith("emulator-") or props.get("ro.kernel.qemu") == "1":
        return "Android Emulator"
    checks = [
        ("BlueStacks", ["bluestacks", "bst"]),
        ("NoxPlayer", ["nox"]),
        ("MuMu", ["mumu", "netease"]),
        ("LDPlayer", ["ldplayer", "leidian"]),
        ("MEmu", ["memu", "microvirt"]),
        ("Genymotion", ["genymotion", "vbox"]),
        ("WSA", ["windows subsystem", "wsa"]),
    ]
    for name, tokens in checks:
        if any(token in blob for token in tokens):
            return name
    if re.match(r"^(127\.0\.0\.1|localhost):\d+$", serial):
        return "Local emulator"
    return "Physical/Unknown"


def _github_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "arrow-puzzle-automation"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _select_maatouch_asset(assets: list[dict], abi: str) -> dict | None:
    abi_tokens = {
        "arm64-v8a": ["arm64", "aarch64", "armv8"],
        "armeabi-v7a": ["armeabi", "armv7", "arm32"],
        "x86": ["x86"],
        "x86_64": ["x86_64", "x64", "amd64"],
    }.get(abi, [abi.lower()])

    candidates = [
        asset for asset in assets
        if "name" in asset and "browser_download_url" in asset and "maatouch" in asset["name"].lower()
    ]
    for asset in candidates:
        name = asset["name"].lower()
        if any(token in name for token in abi_tokens):
            return asset
    return candidates[0] if len(candidates) == 1 else None


def adb_available() -> bool:
    try:
        adb_path()
    except RuntimeError:
        return False
    return True


def _adb_candidates() -> list[Path]:
    candidates: list[Path] = []
    if _ADB_PATH_OVERRIDE:
        candidates.append(_ADB_PATH_OVERRIDE)
    env_path = os.environ.get(ADB_ENV_VAR)
    if env_path:
        candidates.append(Path(env_path))
    candidates.extend(KNOWN_ADB_PATHS)
    found = shutil.which("adb")
    if found:
        candidates.append(Path(found))

    deduped: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate).lower()
        if key not in seen:
            deduped.append(candidate)
            seen.add(key)
    return deduped
