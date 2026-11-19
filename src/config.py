"""Loads, validates and saves utils/settings.json.

Layout of the file:
    settings     - how the app itself behaves (startup, window, theme, fonts)
    preferences  - how clipboard capture and shortcut pasting work (read by the C++ monitor)
    state        - UI session state (open tabs, window size); not user-facing
    defaults     - copy of DEFAULTS below, so the monitor knows the fallback values
"""
from __future__ import annotations

import copy
import logging
import os
import shutil
from pathlib import Path
from typing import Any, Callable

from .util import read_json, write_json_atomic

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1

# Preset modifiers for the 0-9 paste shortcuts: value -> label shown in the UI.
# "hotkey-modifiers" can also be "custom", which uses "custom-hotkey-modifiers".
# (Win+Alt and Ctrl+Win aren't offered: Windows already uses both with 0-9.)
HOTKEY_MODIFIERS = {
    "ctrl+alt": "Ctrl+Alt",
    "ctrl+shift": "Ctrl+Shift",
}

DEFAULTS: dict[str, dict[str, Any]] = {
    "settings": {
        "run-on-startup": False,
        "run-as-admin": False,                    # relaunch elevated (UAC prompt) at every start
        "start-minimized": False,
        "always-on-top": False,
        "restore-open-tabs": True,
        "confirm-clear-history": True,
        "popout-always-on-top": True,
        "theme": "dark",
        "font-family": "Segoe UI",
        "font-size": 10,
    },
    "preferences": {
        "capture-enabled": True,
        "max-history-limit": 10,
        "pins-bypass-limit": True,
        "duplicate-handling": "move-to-top",      # move-to-top | keep-both | ignore
        "ignore-whitespace-only": True,
        "hotkeys-enabled": True,
        "hotkey-modifiers": "ctrl+alt",           # a HOTKEY_MODIFIERS key, or "custom"
        "custom-hotkey-modifiers": "ctrl+alt+shift",  # used when hotkey-modifiers == "custom"
        "paste-mode": "keep-formatting",          # keep-formatting | text-only
        "trim-whitespace-on-paste": False,
        "paste-add-space": False,                 # press Space after a shortcut paste
        "paste-add-newline": False,               # press Enter after a shortcut paste (after the space, if both)
        "restore-clipboard-after-paste": True,
        "popout-hotkey-enabled": True,
        "popout-hotkey": "ctrl+alt+win+v",        # opens the History pop-out (registered by the GUI)
        "popout-close-on-copy": False,
    },
}

DEFAULT_STATE: dict[str, Any] = {
    "open-tabs": ["history", "pinned"],
    "active-tab": "history",
    "sidebar-collapsed": False,
    "collapsed-sections": [],
    "window-geometry": "1100x700",
    "window-maximized": False,
    "open-popouts": [],
    "popout-geometry": {},      # "history"/"pinned" -> "WxH+X+Y"
}

# Allowed values for choice fields; anything else in the file falls back to the default.
CHOICES: dict[tuple[str, str], set[str]] = {
    ("settings", "theme"): {"dark", "light"},
    ("preferences", "hotkey-modifiers"): set(HOTKEY_MODIFIERS) | {"custom"},
    ("preferences", "duplicate-handling"): {"move-to-top", "keep-both", "ignore"},
    ("preferences", "paste-mode"): {"keep-formatting", "text-only"},
}

Listener = Callable[[str, str, Any], None]


def _coerce(value: Any, default: Any) -> Any:
    """Return value if it has the same type as default, else default."""
    if isinstance(default, bool):
        return value if isinstance(value, bool) else default
    if isinstance(default, int):
        return value if isinstance(value, int) and not isinstance(value, bool) else default
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    if isinstance(default, list):
        return list(value) if isinstance(value, list) else copy.deepcopy(default)
    if isinstance(default, dict):
        return dict(value) if isinstance(value, dict) else copy.deepcopy(default)
    return default if value is None else value


class Config:
    def __init__(self, path: Path):
        self.path = path
        self._listeners: list[Listener] = []
        self._signature: tuple[int, int] | None = None
        self._bad_edit_logged = False
        self.data = self._load()
        self.save()  # writes back any migrated/missing keys

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    def poll(self) -> None:
        """Pick up edits made to settings.json outside the app (e.g. via "Open JSON")."""
        sig = self._stat()
        if sig is None or sig == self._signature:
            return
        try:
            raw = read_json(self.path)
            if not isinstance(raw, dict):
                raise ValueError("not a JSON object")
        except (OSError, ValueError) as exc:
            if not self._bad_edit_logged:  # probably mid-edit; keep the current values until it's valid again
                log.warning("settings.json was edited but can't be read yet (%s); keeping the current settings", exc)
                self._bad_edit_logged = True
            return
        self._bad_edit_logged = False
        self._signature = sig
        new = self._build(raw)
        new["state"] = self.data["state"]  # in-memory UI state is newer than what's on disk
        changes = [(section, key, value) for section in DEFAULTS for key, value in new[section].items()
                   if self.data[section][key] != value]
        self.data = new
        if changes:
            log.info("settings.json was changed outside the app; picked up %d change(s)", len(changes))
            for section, key, value in changes:
                self._notify(section, key, value)

    # ---------- persistence ----------
    def _load(self) -> dict[str, Any]:
        raw: dict[str, Any] = {}
        try:
            loaded = read_json(self.path)
            if isinstance(loaded, dict):
                raw = loaded
            else:
                log.warning("%s is not a JSON object; using defaults", self.path.name)
        except FileNotFoundError:
            log.info("No settings file yet; creating %s", self.path)
        except (OSError, ValueError) as exc:
            backup = self.path.with_name(self.path.name + ".corrupt")
            try:
                shutil.copy2(self.path, backup)
                log.warning("Could not read %s (%s). Saved a copy to %s and reset to defaults.",
                            self.path.name, exc, backup.name)
            except OSError:
                log.warning("Could not read %s (%s); using defaults", self.path.name, exc)
        return self._build(raw)

    def _build(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Validated settings from a parsed file (missing/invalid values -> defaults)."""
        data: dict[str, Any] = {"schema-version": SCHEMA_VERSION}
        for section, defaults in DEFAULTS.items():
            stored = raw.get(section) if isinstance(raw.get(section), dict) else {}
            data[section] = {k: _coerce(stored.get(k, d), d) for k, d in defaults.items()}
            for k, d in defaults.items():
                allowed = CHOICES.get((section, k))
                if allowed and data[section][k] not in allowed:
                    log.info("%s.%s: %r is no longer an option; using %r", section, k, data[section][k], d)
                    data[section][k] = d
        stored_state = raw.get("state") if isinstance(raw.get("state"), dict) else {}
        data["state"] = {k: _coerce(stored_state.get(k, d), d) for k, d in DEFAULT_STATE.items()}
        data["defaults"] = copy.deepcopy(DEFAULTS)
        return data

    def save(self) -> None:
        try:
            write_json_atomic(self.path, self.data)
            self._signature = self._stat()  # our own write isn't an "outside" change
        except OSError as exc:
            log.error("Could not save %s: %s", self.path.name, exc)

    # ---------- settings / preferences ----------
    def get(self, section: str, key: str) -> Any:
        return self.data[section][key]

    def default(self, section: str, key: str) -> Any:
        return DEFAULTS[section][key]

    def set(self, section: str, key: str, value: Any) -> None:
        if self.data[section].get(key) == value:
            return
        self.data[section][key] = value
        self.save()
        log.info("%s.%s = %r", section, key, value)
        self._notify(section, key, value)

    def reset(self, section: str) -> None:
        changed = [k for k, d in DEFAULTS[section].items() if self.data[section][k] != d]
        for k in changed:
            self.data[section][k] = copy.deepcopy(DEFAULTS[section][k])
        if not changed:
            return
        self.save()
        log.info("Reset %s to defaults (%s)", section, ", ".join(changed))
        for k in changed:
            self._notify(section, k, self.data[section][k])

    # ---------- UI state (saved on exit) ----------
    def state(self, key: str) -> Any:
        return self.data["state"][key]

    def set_state(self, key: str, value: Any) -> None:
        self.data["state"][key] = value

    # ---------- change notifications ----------
    def subscribe(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Listener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self, section: str, key: str, value: Any) -> None:
        for fn in list(self._listeners):
            try:
                fn(section, key, value)
            except Exception:
                log.exception("Settings listener failed")
