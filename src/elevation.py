"""Running CopyManager as administrator.

Windows won't let a normal app send keystrokes to an app running as administrator,
so shortcut pastes into admin apps need CopyManager (and its monitor, which inherits
the rights) to be elevated too.

Relaunching: start a new copy through the UAC prompt ("runas"), passing our process id
with --after-pid. The new copy waits for this one to exit before it starts, so the
single-instance check and the clipboard monitor hand over cleanly.
"""
from __future__ import annotations

import ctypes
import logging
import os
import sys
from pathlib import Path

from . import paths

log = logging.getLogger(__name__)

ERROR_CANCELLED = 1223  # the user said "No" to the UAC prompt


def is_elevated() -> bool:
    if os.name != "nt":
        return False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _pythonw() -> str:
    exe = Path(sys.executable)
    pythonw = exe.with_name("pythonw.exe")  # no console window
    return str(pythonw if pythonw.exists() else exe)


def relaunch_elevated() -> tuple[bool, str | None]:
    """Start an elevated copy of the app (shows the UAC prompt).

    Returns (started, error message). The caller should exit right after a successful start.
    """
    if os.name != "nt":
        return False, "Running as administrator is only supported on Windows."
    params = f'"{paths.APP_SCRIPT}" --after-pid {os.getpid()} --no-elevate'
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    shell32.ShellExecuteW.restype = ctypes.c_void_p
    shell32.ShellExecuteW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
                                      ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int]
    result = shell32.ShellExecuteW(None, "runas", _pythonw(), params, str(paths.ROOT), 1) or 0
    if result > 32:
        return True, None
    error = ctypes.get_last_error()
    if error == ERROR_CANCELLED or result == 5:
        return False, "The administrator prompt was cancelled."
    return False, f"Windows couldn't start CopyManager as administrator (error {error or result})."


def wait_for_exit(pid: int, timeout_s: float = 15.0) -> None:
    """Block until process `pid` has exited (used by a relaunched copy waiting for the old one)."""
    if os.name != "nt" or pid <= 0:
        return
    SYNCHRONIZE = 0x00100000
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
    if handle:
        kernel32.WaitForSingleObject(handle, int(timeout_s * 1000))
        kernel32.CloseHandle(handle)
