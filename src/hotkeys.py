"""Keyboard shortcut strings and a Windows global-hotkey listener.

Shortcut strings are lowercase tokens joined by "+": modifiers first, in the
order ctrl, alt, shift, win, then an optional key. For example
"ctrl+alt+win+v", or "ctrl+shift" for a modifiers-only combo (used with 0-9).
The C++ monitor parses the same format.
"""
from __future__ import annotations

import ctypes
import queue
import sys
import threading
from typing import Iterable

MODIFIER_ORDER = ("ctrl", "alt", "shift", "win")
MODIFIER_LABELS = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "win": "Win"}
_MOD_FLAGS = {"alt": 0x0001, "ctrl": 0x0002, "shift": 0x0004, "win": 0x0008}
_MOD_NOREPEAT = 0x4000
_WM_HOTKEY = 0x0312
_WM_QUIT = 0x0012


def _build_keys() -> dict[str, tuple[str, int]]:
    keys: dict[str, tuple[str, int]] = {}
    for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        keys[c.lower()] = (c, ord(c))
    for d in "0123456789":
        keys[d] = (d, ord(d))
    for n in range(1, 13):
        keys[f"f{n}"] = (f"F{n}", 0x6F + n)
    keys.update({
        "space": ("Space", 0x20), "insert": ("Insert", 0x2D), "home": ("Home", 0x24),
        "end": ("End", 0x23), "pageup": ("PageUp", 0x21), "pagedown": ("PageDown", 0x22),
    })
    return keys


KEYS = _build_keys()  # token -> (label, Windows virtual-key code)


def parse(spec: str) -> tuple[frozenset[str], str | None]:
    parts = [p.strip().lower() for p in (spec or "").split("+") if p.strip()]
    mods = frozenset(p for p in parts if p in MODIFIER_ORDER)
    keys = [p for p in parts if p in KEYS]
    return mods, (keys[-1] if keys else None)


def build(mods: Iterable[str], key: str | None = None) -> str:
    mods = set(mods)
    return "+".join([m for m in MODIFIER_ORDER if m in mods] + ([key] if key else []))


def label(spec: str) -> str:
    """"ctrl+alt+win+v" -> "Ctrl+Alt+Win+V"."""
    mods, key = parse(spec)
    parts = [MODIFIER_LABELS[m] for m in MODIFIER_ORDER if m in mods]
    if key:
        parts.append(KEYS[key][0])
    return "+".join(parts)


def is_valid_modifiers(mods: Iterable[str]) -> bool:
    """Shift alone isn't enough: Shift+digit just types a symbol."""
    return bool(set(mods) & {"ctrl", "alt", "win"})


# Built-in Windows shortcuts that use modifiers + a number key.
WINDOWS_DIGIT_SHORTCUTS: dict[frozenset[str], str] = {
    frozenset({"win"}): "Win+number opens or switches to the app pinned at that spot on the taskbar.",
    frozenset({"win", "shift"}): "Win+Shift+number opens a new window of the app pinned at that taskbar spot.",
    frozenset({"win", "ctrl"}): "Win+Ctrl+number switches to the last active window of that taskbar app.",
    frozenset({"win", "alt"}): "Win+Alt+number opens the jump list of that taskbar app.",
    frozenset({"win", "ctrl", "shift"}): "Win+Ctrl+Shift+number opens that taskbar app as administrator.",
}


def _flags(mods: Iterable[str]) -> int:
    flags = _MOD_NOREPEAT
    for m in mods:
        flags |= _MOD_FLAGS[m]
    return flags


def is_taken(mods: Iterable[str], key: str) -> bool:
    """True if Windows or another running app has already registered this combination.
    (Only system-wide shortcuts can be detected, not ones that exist inside a single app.)"""
    if sys.platform != "win32" or key not in KEYS:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    probe_id = 0xBFF0  # temporary id, released right away
    if user32.RegisterHotKey(None, probe_id, _flags(mods), KEYS[key][1]):
        user32.UnregisterHotKey(None, probe_id)
        return False
    return True


def digit_conflicts(spec: str) -> tuple[str | None, list[str]]:
    """Check modifiers + 0-9 for clashes.

    Returns (known Windows shortcut description or None, digits that are already
    registered system-wide by Windows or another running app).
    """
    mods, _key = parse(spec)
    known = WINDOWS_DIGIT_SHORTCUTS.get(mods)
    taken = [d for d in "0123456789" if mods and is_taken(mods, d)]
    return known, taken


def paste_spec(prefs: dict) -> str:
    """The modifiers actually used with 0-9 (resolves "custom")."""
    value = prefs["hotkey-modifiers"]
    return prefs["custom-hotkey-modifiers"] if value == "custom" else value


def paste_modifier_label(prefs: dict) -> str:
    """Display text for the modifiers used with 0-9, e.g. "Win+Alt"."""
    from .config import HOTKEY_MODIFIERS
    value = prefs["hotkey-modifiers"]
    if value == "custom":
        return label(prefs["custom-hotkey-modifiers"]) or "Custom"
    return HOTKEY_MODIFIERS.get(value, label(value))


class GlobalHotkey:
    """Registers one system-wide hotkey on a background thread (RegisterHotKey).

    Key presses are queued; call drain() from the Tk thread, because Tk
    must not be touched from other threads.
    """

    def __init__(self) -> None:
        self.events: queue.SimpleQueue[int] = queue.SimpleQueue()
        self.spec: str | None = None
        self._thread: threading.Thread | None = None
        self._thread_id = 0

    def set(self, spec: str | None) -> str | None:
        """Register spec, or unregister when spec is None. Returns an error message or None."""
        self.stop()
        if not spec:
            return None
        if sys.platform != "win32":
            return "Global shortcuts are only supported on Windows."
        mods, key = parse(spec)
        if key is None or not is_valid_modifiers(mods):
            return f"“{spec}” isn't a valid shortcut."
        flags = _MOD_NOREPEAT
        for m in mods:
            flags |= _MOD_FLAGS[m]
        ready, result = threading.Event(), {}
        thread = threading.Thread(target=self._run, args=(flags, KEYS[key][1], self.events, ready, result),
                                  name="global-hotkey", daemon=True)
        thread.start()
        ready.wait(2)
        if not result.get("ok"):
            thread.join(1)
            if result.get("error") == 1409:  # ERROR_HOTKEY_ALREADY_REGISTERED
                return f"{label(spec)} is already used by Windows or another app."
            return f"Couldn't register {label(spec)} (error {result.get('error')})."
        self._thread, self._thread_id, self.spec = thread, result["thread_id"], spec
        return None

    @staticmethod
    def _run(flags: int, vk: int, events: "queue.SimpleQueue[int]", ready: threading.Event, result: dict) -> None:
        from ctypes import wintypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        msg = wintypes.MSG()
        user32.PeekMessageW(ctypes.byref(msg), None, 0x0400, 0x0400, 0)  # make sure this thread has a message queue
        result["thread_id"] = ctypes.windll.kernel32.GetCurrentThreadId()
        ok = user32.RegisterHotKey(None, 1, flags, vk)
        result["ok"], result["error"] = bool(ok), ctypes.get_last_error()
        ready.set()
        if not ok:
            return
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == _WM_HOTKEY:
                    events.put(1)
        finally:
            user32.UnregisterHotKey(None, 1)

    def stop(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, _WM_QUIT, 0, 0)
            self._thread.join(1)
        self._thread, self._thread_id, self.spec = None, 0, None

    def drain(self) -> int:
        """Number of presses since the last call."""
        count = 0
        while True:
            try:
                self.events.get_nowait()
            except queue.Empty:
                return count
            count += 1
