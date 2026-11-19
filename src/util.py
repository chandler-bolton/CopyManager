"""Small helpers shared across the app."""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any:
    # utf-8-sig tolerates a BOM, which some Windows editors/tools add.
    with open(path, encoding="utf-8-sig") as f:
        return json.load(f)


def write_json_atomic(path: Path, data: Any) -> None:
    """Write to a temp file and swap it into place, so a reader (e.g. the C++
    monitor) never sees a half-written file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            f.write("\n")
        for attempt in range(10):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                # The other process has the file open right now; retry briefly.
                if attempt == 9:
                    raise
                time.sleep(0.03)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def now_iso() -> str:
    """Local time, e.g. 2026-11-19T09:12:44 (the format history.json uses)."""
    return datetime.now().isoformat(timespec="seconds")


def parse_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone().replace(tzinfo=None)
    return dt


def time_ago(value: Any) -> str:
    dt = parse_time(value)
    if dt is None:
        return ""
    secs = (datetime.now() - dt).total_seconds()
    if secs < 60:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} hr ago"
    days = int(secs // 86400)
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return f"{dt:%b} {dt.day}, {dt.year}"


def parse_geometry(geometry: str) -> tuple[int, int, int | None, int | None] | None:
    """Parse a Tk geometry string: "800x600", "800x600+10+20", or with negative offsets.
    Tk writes a negative position as "+-97" (e.g. a window dragged partly off the top
    or onto a monitor to the left), so each offset is a sign followed by an optional "-"."""
    import re
    match = re.fullmatch(r"(\d+)x(\d+)(?:([+-])(-?\d+)([+-])(-?\d+))?", (geometry or "").strip())
    if not match:
        return None
    w, h, sx, x, sy, y = match.groups()
    if x is None:
        return int(w), int(h), None, None
    # "+N" / "+-N" are positions; a bare "-N" counts from the right/bottom edge, treated here as a position.
    to_int = lambda sign, n: int(n) if sign == "+" else -abs(int(n))  # noqa: E731
    return int(w), int(h), to_int(sx, x), to_int(sy, y)


def point_on_screen(x: int, y: int) -> bool:
    """True if (x, y) is on any connected monitor (used to drop saved off-screen positions)."""
    if os.name != "nt":
        return True
    try:
        import ctypes
        from ctypes import wintypes
        return bool(ctypes.windll.user32.MonitorFromPoint(wintypes.POINT(x, y), 0))  # MONITOR_DEFAULTTONULL
    except Exception:
        return True


def one_line(text: str, limit: int = 60) -> str:
    s = " ".join(text.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"
