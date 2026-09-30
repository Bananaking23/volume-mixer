"""Arduino CLI discovery, COM port detection and compile/upload helpers.

Everything that shells out lives here so the UI layer never touches
subprocess directly. Long-running operations (compile/upload) stream their
output line-by-line through a callback so the GUI can show live progress.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys

IS_WINDOWS = platform.system() == "Windows"


# ---------------------------------------------------------------------------
# arduino-cli discovery
# ---------------------------------------------------------------------------

_COMMON_WINDOWS_LOCATIONS = [
    r"C:\Program Files\Arduino CLI\arduino-cli.exe",
    r"C:\Arduino CLI\arduino-cli.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\arduino-cli\arduino-cli.exe"),
    os.path.expandvars(r"%USERPROFILE%\arduino-cli\arduino-cli.exe"),
]


def find_arduino_cli(configured_path: str = "") -> str | None:
    """Return a usable path to arduino-cli, or None if it can't be found.

    Order: an explicitly configured path -> PATH -> common install
    locations (Windows only).
    """
    if configured_path and os.path.isfile(configured_path):
        return configured_path

    on_path = shutil.which("arduino-cli")
    if on_path:
        return on_path

    if IS_WINDOWS:
        for candidate in _COMMON_WINDOWS_LOCATIONS:
            if candidate and os.path.isfile(candidate):
                return candidate

    return None


def get_cli_version(cli_path: str) -> str | None:
    try:
        result = subprocess.run(
            [cli_path, "version"], capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return None


# ---------------------------------------------------------------------------
# COM port detection
# ---------------------------------------------------------------------------

def list_com_ports_via_cli(cli_path: str):
    """Return a list of port name strings using `arduino-cli board list --json`."""
    if not cli_path:
        return []
    try:
        result = subprocess.run(
            [cli_path, "board", "list", "--json"],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return []
        data = json.loads(result.stdout)
        ports = []
        for entry in data.get("detected_ports", data if isinstance(data, list) else []):
            port_info = entry.get("port", {}) if isinstance(entry, dict) else {}
            address = port_info.get("address")
            if address:
                ports.append(address)
        return ports
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError, AttributeError):
        return []


def list_com_ports_fallback_windows():
    """Fallback COM port detection via the Windows registry, used when
    arduino-cli isn't available. Returns [] on any non-Windows platform or
    error."""
    if not IS_WINDOWS:
        return []
    try:
        import winreg  # type: ignore
    except ImportError:
        return []
    ports = []
    try:
        key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DEVICEMAP\SERIALCOMM")
        i = 0
        while True:
            try:
                _name, value, _type = winreg.EnumValue(key, i)
                ports.append(value)
                i += 1
            except OSError:
                break
        winreg.CloseKey(key)
    except OSError:
        return []
    return ports


def list_com_ports(cli_path: str = ""):
    """Best-effort combined COM port listing: arduino-cli first, then the
    Windows registry fallback. Always returns a de-duplicated, sorted list
    (possibly empty)."""
    ports = set(list_com_ports_via_cli(cli_path))
    ports.update(list_com_ports_fallback_windows())
    return sorted(ports)


# ---------------------------------------------------------------------------
# Compile / upload
# ---------------------------------------------------------------------------

class CommandResult:
    def __init__(self, ok: bool, exit_code: int | None, message: str = ""):
        self.ok = ok
        self.exit_code = exit_code
        self.message = message


def run_streaming(cmd, on_line, cwd=None) -> CommandResult:
    """Run `cmd` (a list), calling on_line(str) for every line of combined
    stdout/stderr as it arrives. Blocks until the process exits -- callers
    running this from a GUI should invoke it on a background thread."""
    try:
        process = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            universal_newlines=True,
        )
    except OSError as exc:
        on_line(f"[error] Failed to launch: {exc}")
        return CommandResult(False, None, str(exc))

    assert process.stdout is not None
    for line in process.stdout:
        on_line(line.rstrip("\n"))

    process.wait()
    ok = process.returncode == 0
    return CommandResult(ok, process.returncode)


def compile_sketch(cli_path: str, fqbn: str, sketch_dir: str, on_line) -> CommandResult:
    cmd = [cli_path, "compile", "--fqbn", fqbn, sketch_dir]
    on_line(f"$ {' '.join(cmd)}")
    return run_streaming(cmd, on_line)


def upload_sketch(cli_path: str, fqbn: str, port: str, sketch_dir: str, on_line) -> CommandResult:
    cmd = [cli_path, "upload", "-p", port, "--fqbn", fqbn, sketch_dir]
    on_line(f"$ {' '.join(cmd)}")
    return run_streaming(cmd, on_line)


def compile_and_upload(cli_path: str, fqbn: str, port: str, sketch_dir: str, on_line) -> CommandResult:
    result = compile_sketch(cli_path, fqbn, sketch_dir, on_line)
    if not result.ok:
        on_line("[error] Compile failed; aborting before upload.")
        return result
    on_line("[ok] Compile succeeded.")
    return upload_sketch(cli_path, fqbn, port, sketch_dir, on_line)


def start_serial_monitor(cli_path: str, port: str, baud: int):
    """Start `arduino-cli monitor` and return its process for the UI to read."""
    cmd = [cli_path, "monitor", "-p", port, "--config", f"baudrate={baud}"]
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True,
        bufsize=1,
        universal_newlines=True,
    )


# ---------------------------------------------------------------------------
# Opening the generated sketch in the OS-default editor / Arduino IDE
# ---------------------------------------------------------------------------

def open_path_with_default_app(path: str) -> CommandResult:
    try:
        if IS_WINDOWS and hasattr(os, "startfile"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return CommandResult(True, 0)
    except OSError as exc:
        return CommandResult(False, None, str(exc))
