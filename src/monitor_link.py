"""The GUI's connection to the C++ clipboard monitor (bin/monitor.exe).

* start()/check()/stop(): run the monitor and restart it if it crashes.
* request_refresh(): ask it to reload settings.json right now. It replies by
  writing the refresh id into its status file (logs/monitor-status.json).
* status: what the monitor last reported (e.g. which paste shortcuts it
  actually registered).
"""
from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import time
from typing import Any

from . import hotkeys, paths
from .util import read_json

log = logging.getLogger(__name__)

WM_APP_REFRESH = 0x8000 + 1   # WM_APP + 1, handled in monitor.cpp
HWND_MESSAGE = -3
WINDOW_CLASS = "CopyManagerMonitor"


class MonitorLink:
    MAX_RESTARTS = 3

    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.error: str | None = None          # why copies aren't being captured, if they aren't
        self.status: dict[str, Any] = {}
        self._status_sig: tuple[int, int] | None = None
        self._restarts = 0
        self._next_id = (int(time.time()) % 100_000) * 1000 + 1  # unique across runs
        self._exe_stamp: int | None = None  # monitor.exe's modified time when we started it

    @staticmethod
    def _stamp() -> int | None:
        try:
            return paths.MONITOR_EXE.stat().st_mtime_ns
        except OSError:
            return None

    # ---------- process ----------
    def start(self) -> None:
        exe = paths.MONITOR_EXE
        if not exe.exists():
            self.error = "Clipboard monitor isn't built"
            log.error("Clipboard monitor not found at %s. Run build_monitor.bat to build it; "
                      "until then, copies aren't recorded and paste shortcuts don't work.", exe)
            return
        for leftover in (paths.MONITOR_STATUS_PATH,                      # a previous run's status
                         paths.MONITOR_EXE.with_name("monitor.old.exe")):  # moved aside by build_monitor.bat
            try:
                leftover.unlink(missing_ok=True)
            except OSError:
                pass  # still running (e.g. the old build); removed next time
        self._exe_stamp = self._stamp()
        self.status, self._status_sig = {}, None
        args = [str(exe),
                "--settings", str(paths.SETTINGS_PATH),
                "--history", str(paths.HISTORY_PATH),
                "--log", str(paths.MONITOR_LOG_PATH),
                "--status", str(paths.MONITOR_STATUS_PATH),
                "--parent-pid", str(os.getpid())]
        try:
            self.process = subprocess.Popen(args, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                            stderr=subprocess.DEVNULL)
            self.error = None
        except OSError as exc:
            self.process = None
            self.error = "Clipboard monitor couldn't start"
            log.error("Couldn't start the clipboard monitor: %s", exc)

    def is_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def check(self) -> bool:
        """Call regularly. Restarts a crashed monitor and re-reads its status. Returns True if anything changed."""
        changed = self.read_status()
        if self.process is None or self.process.poll() is None:
            return changed
        code = self.process.returncode
        self.process = None
        if code == 0:
            return changed  # another monitor was already running; that one does the work
        if self._restarts < self.MAX_RESTARTS:
            self._restarts += 1
            log.warning("Clipboard monitor stopped unexpectedly (exit code %s); restarting", code)
            self.start()
        else:
            self.error = "Clipboard monitor keeps stopping"
            log.error("Clipboard monitor stopped again (exit code %s); not restarting. See logs/monitor.log.", code)
        return True

    def ensure_running(self) -> None:
        """Used by "Save & refresh": start the monitor if it isn't running (e.g. it was just built),
        or restart it if monitor.exe has been rebuilt since it started."""
        if self.is_running() and self._stamp() != self._exe_stamp:
            log.info("monitor.exe was rebuilt; restarting the clipboard monitor on the new build")
            self.process.terminate()
            try:
                self.process.wait(3)
            except subprocess.TimeoutExpired:
                self.process.kill()
            self.process = None
        if not self.is_running() and not self._window():
            self._restarts = 0
            self.start()

    def stop(self) -> None:
        """Stop the monitor and wait until it has really exited, so nothing is left running
        (and a relaunched copy of the app can start its own monitor straight away)."""
        if not self.is_running():
            self.process = None
            return
        self.process.terminate()  # it would also notice the GUI has exited; this is just quicker
        try:
            self.process.wait(3)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(2)
        self.process = None

    # ---------- talking to it ----------
    def _window(self) -> int:
        if os.name != "nt":
            return 0
        user32 = ctypes.windll.user32
        user32.FindWindowExW.restype = ctypes.c_void_p
        user32.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        return user32.FindWindowExW(ctypes.c_void_p(HWND_MESSAGE), None, WINDOW_CLASS, None) or 0

    def request_refresh(self) -> int | None:
        """Ask the monitor to reload settings now. Returns the id it will acknowledge, or None."""
        hwnd = self._window()
        if not hwnd:
            return None
        refresh_id = self._next_id
        self._next_id += 1
        user32 = ctypes.windll.user32
        user32.PostMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        return refresh_id if user32.PostMessageW(hwnd, WM_APP_REFRESH, refresh_id, 0) else None

    def read_status(self) -> bool:
        try:
            st = os.stat(paths.MONITOR_STATUS_PATH)
            sig = (st.st_mtime_ns, st.st_size)
        except OSError:
            sig = None
        if sig == self._status_sig:
            return False
        self._status_sig = sig
        try:
            self.status = read_json(paths.MONITOR_STATUS_PATH) if sig else {}
        except (OSError, ValueError):
            self._status_sig = None  # half-written; try again next time
            return False
        return True

    # ---------- what's active ----------
    def paste_state(self) -> dict[str, Any]:
        state = self.status.get("paste-shortcuts")
        return state if isinstance(state, dict) else {}

    def paste_modifiers(self) -> frozenset[str] | None:
        """Modifiers the monitor has the 0-9 shortcuts registered with (None if none)."""
        state = self.paste_state()
        return hotkeys.parse(state["modifiers"])[0] if state.get("enabled") and state.get("modifiers") else None

    def owned_digits(self, mods: frozenset[str]) -> set[str]:
        """Digits the monitor itself holds with these modifiers (so they aren't "taken by another app")."""
        return set(self.paste_state().get("registered", [])) if self.paste_modifiers() == mods else set()
