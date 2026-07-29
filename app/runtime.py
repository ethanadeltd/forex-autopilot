"""Simple runtime signal files for start/stop/know-if-running."""

from __future__ import annotations

import os
import time
from pathlib import Path

DATA_DIR = Path("data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

PID_FILE = DATA_DIR / "bot.pid"
STOP_FILE = DATA_DIR / "bot.stop"


def mark_running() -> None:
    """Write PID file when bot starts."""
    PID_FILE.write_text(str(os.getpid()), encoding="utf-8")
    if STOP_FILE.exists():
        STOP_FILE.unlink()


def mark_stopped() -> None:
    """Remove PID file when bot stops."""
    if PID_FILE.exists():
        PID_FILE.unlink()
    if STOP_FILE.exists():
        STOP_FILE.unlink()


def is_running() -> bool:
    """Check if PID file exists and process is alive."""
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text(encoding="utf-8").strip())
        import ctypes
        kernel32 = ctypes.windll.kernel32
        SYNCHRONIZE = 0x00100000
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        PID_FILE.unlink(missing_ok=True)
        return False
    except (ValueError, FileNotFoundError, OSError, AttributeError):
        return False


def request_stop() -> None:
    """Write stop signal file."""
    STOP_FILE.write_text("stop", encoding="utf-8")


def should_stop() -> bool:
    """Bot checks this each iteration. If True, break loop."""
    return STOP_FILE.exists()


def wait_for_stop(timeout_ms: int = 30000) -> bool:
    """Wait up to timeout_ms for bot to stop. Returns True if stopped."""
    deadline = time.time() + timeout_ms / 1000.0
    while time.time() < deadline:
        if not is_running():
            return True
        time.sleep(0.5)
    return False


def pid() -> int | None:
    if not PID_FILE.exists():
        return None
    try:
        return int(PID_FILE.read_text(encoding="utf-8").strip())
    except (ValueError, FileNotFoundError):
        return None
