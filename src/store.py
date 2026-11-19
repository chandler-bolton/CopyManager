"""History and pinned items, stored in src/history.json.

The C++ monitor adds new copies to "history". The GUI pins, unpins, deletes
and reorders items. Both sides read and write the same file, so the GUI:
  * watches the file and reloads it when it changes (poll()), and
  * reloads right before every edit it makes, so it doesn't overwrite
    something the monitor just added.

Item schema (see README.md for the full contract):
    {
        "id": "a1b2c3d4e5f6",       unique, stable id
        "text": "...",              plain text (always present)
        "html": null | "...",       CF_HTML payload, if the copy had it
        "rtf": null | "...",        RTF payload, if the copy had it
        "copied-at": "2026-11-19T09:12:44",
        "source": null | "WINWORD.EXE",
        # pinned items only:
        "label": null | "My signature",
        "pinned-at": "2026-11-19T09:20:00"
    }
"""
from __future__ import annotations

import hashlib
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .util import now_iso, read_json, write_json_atomic

log = logging.getLogger(__name__)

SCHEMA_VERSION = 1
HISTORY = "history"
PINNED = "pinned"
TYPED_SOURCE = "Typed in Copy Manager"  # "source" of pins created by hand on the Pinned tab

Item = dict[str, Any]
LimitProvider = Callable[[], "tuple[int, bool]"]  # (max-history-limit, pins-bypass-limit)


class _HistoryLock:
    """Named mutex shared with the C++ monitor, so the two never edit history.json at the same time."""
    NAME = "Local\\CopyManager.HistoryLock"

    def __init__(self) -> None:
        self._handle = None
        if os.name == "nt":
            import ctypes
            self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            self._handle = self._kernel32.CreateMutexW(None, False, self.NAME)

    def __enter__(self):
        self._held = False
        if self._handle:
            result = self._kernel32.WaitForSingleObject(self._handle, 5000)
            self._held = result in (0, 0x80)  # WAIT_OBJECT_0, WAIT_ABANDONED
            if not self._held:
                log.warning("Couldn't get the history lock; saving anyway")
        return self

    def __exit__(self, *_exc):
        if self._held:
            self._kernel32.ReleaseMutex(self._handle)


def _str_or_none(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def normalize_item(raw: Any, pinned: bool) -> Item | None:
    """Accept a full item dict or a bare string (old placeholder format)."""
    if isinstance(raw, str):
        raw = {"text": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("text"), str):
        return None
    item: Item = {
        "id": _str_or_none(raw.get("id")),
        "text": raw["text"],
        "html": _str_or_none(raw.get("html")),
        "rtf": _str_or_none(raw.get("rtf")),
        "copied-at": _str_or_none(raw.get("copied-at")),
        "source": _str_or_none(raw.get("source")),
    }
    if pinned:
        item["label"] = _str_or_none(raw.get("label"))
        item["pinned-at"] = _str_or_none(raw.get("pinned-at"))
    return item


def _assign_ids(items: list[Item], seen: set[str]) -> bool:
    """Give every item a unique id. Returns True if any id was added or changed."""
    changed = False
    for item in items:
        base = item["id"] or hashlib.sha1(
            f'{item["text"]}\0{item["copied-at"]}'.encode("utf-8")).hexdigest()[:12]
        uid, n = base, 1
        while uid in seen:
            n += 1
            uid = f"{base}-{n}"
        if uid != item["id"]:
            item["id"] = uid
            changed = True
        seen.add(uid)
    return changed


def _as_list(value: Any) -> list:
    return value if isinstance(value, list) else []


class ClipStore:
    def __init__(self, path: Path, limit_provider: LimitProvider):
        self.path = path
        self.limit_provider = limit_provider
        self.history: list[Item] = []
        self.pinned: list[Item] = []
        self.error: str | None = None          # set while history.json can't be parsed
        self.last_sync: datetime | None = None
        self._signature: tuple[int, int] | None = None
        self._listeners: list[Callable[[], None]] = []
        self._lock = _HistoryLock()
        if self.path.exists():
            self._load()
        else:
            log.info("Creating %s", self.path)
            self._save()

    # ---------- file sync ----------
    def _stat(self) -> tuple[int, int] | None:
        try:
            st = os.stat(self.path)
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    def _load(self) -> bool:
        signature = self._stat()  # taken before reading, so a write during the read is noticed next poll
        try:
            raw = read_json(self.path)
        except FileNotFoundError:
            raw = {}
        except (OSError, ValueError) as exc:
            if self.error != str(exc):
                log.warning("Could not read %s: %s (will keep retrying)", self.path.name, exc)
            self.error = str(exc)
            return False
        if not isinstance(raw, dict):
            raw = {}
        if self.error:
            log.info("%s is readable again", self.path.name)
        self.error = None

        history = [i for i in (normalize_item(x, False) for x in _as_list(raw.get(HISTORY))) if i]
        pinned = [i for i in (normalize_item(x, True) for x in _as_list(raw.get(PINNED))) if i]
        seen: set[str] = set()
        ids_changed = _assign_ids(pinned, seen)
        ids_changed = _assign_ids(history, seen) or ids_changed

        self.history, self.pinned = history, pinned
        self._signature = signature
        self.last_sync = datetime.now()
        if ids_changed or raw.get("schema-version") != SCHEMA_VERSION:
            with self._lock:  # (re-entrant: fine if _mutate already holds it)
                self._save()  # write the normalized form back
        return True

    def _save(self) -> None:
        data = {"schema-version": SCHEMA_VERSION, HISTORY: self.history, PINNED: self.pinned}
        try:
            write_json_atomic(self.path, data)
        except OSError as exc:
            log.error("Could not save %s: %s", self.path.name, exc)
            return
        self._signature = self._stat()
        self.last_sync = datetime.now()

    def poll(self) -> None:
        """Reload if the file changed on disk (e.g. the monitor added a copy)."""
        if self._stat() != self._signature and self._load():
            self._notify()

    # ---------- listeners ----------
    def subscribe(self, fn: Callable[[], None]) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: Callable[[], None]) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def _notify(self) -> None:
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                log.exception("History listener failed")

    # ---------- edits ----------
    def _mutate(self, op: Callable[[], bool]) -> bool:
        with self._lock:  # the monitor can't add a copy between our reload and save
            reloaded = self._stat() != self._signature and self._load()
            changed = op()
            trimmed = self._apply_limit()
            if changed or trimmed:
                self._save()
        if changed or trimmed or reloaded:
            self._notify()
        return changed or trimmed

    def history_capacity(self) -> int:
        max_items, pins_bypass = self.limit_provider()
        return max_items if pins_bypass else max(0, max_items - len(self.pinned))

    def _apply_limit(self) -> bool:
        limit = self.history_capacity()
        if len(self.history) <= limit:
            return False
        removed = len(self.history) - limit
        del self.history[limit:]
        log.info("Trimmed %d old item(s) to fit the history limit of %d", removed, limit)
        return True

    def find(self, item_id: str) -> tuple[str | None, int]:
        for name, items in ((HISTORY, self.history), (PINNED, self.pinned)):
            for i, item in enumerate(items):
                if item["id"] == item_id:
                    return name, i
        return None, -1

    def enforce_limit(self) -> bool:
        return self._mutate(lambda: False)

    def pin(self, item_id: str) -> bool:
        def op() -> bool:
            where, i = self.find(item_id)
            if where != HISTORY:
                return False
            item = self.history.pop(i)
            item["label"] = None
            item["pinned-at"] = now_iso()
            self.pinned.insert(0, item)
            return True
        return self._mutate(op)

    def unpin(self, item_id: str) -> bool:
        def op() -> bool:
            where, i = self.find(item_id)
            if where != PINNED:
                return False
            item = self.pinned.pop(i)
            item.pop("label", None)
            item.pop("pinned-at", None)
            self.history.insert(0, item)
            return True
        return self._mutate(op)

    def delete(self, item_id: str) -> bool:
        def op() -> bool:
            where, i = self.find(item_id)
            if where is None:
                return False
            (self.history if where == HISTORY else self.pinned).pop(i)
            return True
        return self._mutate(op)

    def clear_history(self) -> bool:
        def op() -> bool:
            if not self.history:
                return False
            self.history.clear()
            return True
        return self._mutate(op)

    def add_pinned(self, text: str, label: str | None = None) -> bool:
        """Pin text typed in by hand. It's always saved as plain text (no HTML/RTF).
        Returns False if the text is blank or already pinned."""
        def op() -> bool:
            if not text.strip() or any(item["text"] == text for item in self.pinned):
                return False
            ids = {item["id"] for item in self.history + self.pinned}
            new_id = uuid.uuid4().hex[:12]
            while new_id in ids:
                new_id = uuid.uuid4().hex[:12]
            now = now_iso()
            self.pinned.insert(0, {
                "id": new_id, "text": text, "html": None, "rtf": None,
                "copied-at": now, "source": TYPED_SOURCE,
                "label": (label or "").strip() or None, "pinned-at": now,
            })
            return True
        return self._mutate(op)

    def move_pinned(self, item_id: str, step: int) -> bool:
        def op() -> bool:
            where, i = self.find(item_id)
            j = i + step
            if where != PINNED or not 0 <= j < len(self.pinned):
                return False
            self.pinned[i], self.pinned[j] = self.pinned[j], self.pinned[i]
            return True
        return self._mutate(op)

    def rename_pinned(self, item_id: str, label: str | None) -> bool:
        def op() -> bool:
            where, i = self.find(item_id)
            new = (label or "").strip() or None
            if where != PINNED or self.pinned[i].get("label") == new:
                return False
            self.pinned[i]["label"] = new
            return True
        return self._mutate(op)
