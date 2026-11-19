"""Output tab: log messages from this app plus whatever the C++ monitor writes to logs/monitor.log."""
from __future__ import annotations

import logging
import os
import tkinter as tk
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from tkinter import ttk
from typing import Callable

from . import paths

LogListener = Callable[[str, str, str], None]  # (level, line, source)


class LogBuffer(logging.Handler):
    """Keeps recent log lines in memory so the Output tab can show them even if it was closed."""

    def __init__(self, maxlen: int = 2000):
        super().__init__(level=logging.INFO)
        self.lines: deque[tuple[str, str, str]] = deque(maxlen=maxlen)
        self._listeners: list[LogListener] = []
        self.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.add(record.levelname, self.format(record), "app")
        except Exception:
            self.handleError(record)

    def add(self, level: str, line: str, source: str) -> None:
        self.lines.append((level, line, source))
        for fn in list(self._listeners):
            fn(level, line, source)

    def clear(self) -> None:
        self.lines.clear()

    def subscribe(self, fn: LogListener) -> None:
        self._listeners.append(fn)

    def unsubscribe(self, fn: LogListener) -> None:
        if fn in self._listeners:
            self._listeners.remove(fn)

    def unsubscribe_all(self) -> None:
        """On shutdown: no window is left to show log lines."""
        self._listeners.clear()


LOG_BUFFER = LogBuffer()


def setup_logging() -> None:
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(LOG_BUFFER)
    try:
        paths.LOG_DIR.mkdir(exist_ok=True)
        file_handler = RotatingFileHandler(paths.APP_LOG_PATH, maxBytes=512_000, backupCount=2, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        root.addHandler(file_handler)
    except OSError:
        pass


class LogTail:
    """Follows a log file that another process (the C++ monitor) appends to."""

    def __init__(self, path: Path, initial_bytes: int = 8192):
        self.path = path
        self.initial_bytes = initial_bytes
        self.offset: int | None = None
        self._partial = ""
        self._skip_first = False

    def poll(self) -> list[str]:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if self.offset is None:  # first look: show only the tail of an existing log
            self.offset = max(0, size - self.initial_bytes)
            self._skip_first = self.offset > 0
        if size < self.offset:   # truncated or rotated
            self.offset, self._partial, self._skip_first = 0, "", False
        if size == self.offset:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read(size - self.offset)
        except OSError:
            return []
        self.offset = size
        lines = (self._partial + chunk.decode("utf-8", errors="replace")).split("\n")
        self._partial = lines.pop()
        if self._skip_first and lines:
            lines.pop(0)  # probably a cut-off line
            self._skip_first = False
        return [line.rstrip("\r") for line in lines if line.strip()]


def monitor_level(line: str) -> str:
    upper = line.upper()
    if "ERROR" in upper or "FATAL" in upper:
        return "ERROR"
    if "WARN" in upper:
        return "WARNING"
    return "INFO"


class OutputView(tk.Frame):
    MAX_LINES = 2500

    def __init__(self, master, app):
        self.app = app
        theme, p, px = app.theme, app.theme.p, app.theme.px
        super().__init__(master, bg=p["bg"])

        head = tk.Frame(self, bg=p["bg"])
        head.pack(fill="x", padx=(px(28), px(26)), pady=(px(20), px(12)))
        top = tk.Frame(head, bg=p["bg"])
        top.pack(fill="x")
        ttk.Label(top, text="Output", style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Clear", command=self.clear).pack(side="right")
        ttk.Button(top, text="Open logs folder", command=self.open_folder).pack(side="right", padx=(0, px(8)))
        self.autoscroll = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Auto-scroll", variable=self.autoscroll).pack(side="right", padx=(0, px(14)))
        ttk.Label(head, style="Muted.TLabel",
                  text="Activity from CopyManager and the clipboard monitor (logs/monitor.log)."
                  ).pack(anchor="w", pady=(px(2), 0))

        body = tk.Frame(self, bg=p["bg"], highlightthickness=1, highlightbackground=p["border"])
        body.pack(fill="both", expand=True, padx=(px(28), px(16)), pady=(0, px(16)))
        self.text = tk.Text(body, bg=p["bg"], fg=p["fg"], font=theme.fonts["mono"], relief="flat", bd=0,
                            highlightthickness=0, wrap="word", padx=px(10), pady=px(8),
                            insertbackground=p["fg"], selectbackground=p["selection"],
                            selectforeground=p["fg_strong"], cursor="arrow")
        bar = ttk.Scrollbar(body, orient="vertical", command=self.text.yview, style="Slim.Vertical.TScrollbar")
        self.text.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)
        self.text.tag_configure("WARNING", foreground=p["warning"])
        self.text.tag_configure("ERROR", foreground=p["danger"])
        self.text.tag_configure("CRITICAL", foreground=p["danger"])
        self.text.tag_configure("monitor", foreground=p["info"])

        for level, line, source in LOG_BUFFER.lines:
            self._insert(level, line, source)
        self.text.configure(state="disabled")
        self.text.see("end")
        LOG_BUFFER.subscribe(self.append)

    def _insert(self, level: str, line: str, source: str) -> None:
        tags = (level, "monitor") if source == "monitor" else (level,)
        self.text.insert("end", line + "\n", tags)

    def append(self, level: str, line: str, source: str) -> None:
        self.text.configure(state="normal")
        self._insert(level, line, source)
        excess = int(self.text.index("end-1c").split(".")[0]) - self.MAX_LINES
        if excess > 0:
            self.text.delete("1.0", f"{excess + 1}.0")
        self.text.configure(state="disabled")
        if self.autoscroll.get():
            self.text.see("end")

    def clear(self) -> None:
        LOG_BUFFER.clear()
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")

    def open_folder(self) -> None:
        paths.LOG_DIR.mkdir(exist_ok=True)
        os.startfile(paths.LOG_DIR)  # type: ignore[attr-defined]

    def destroy(self) -> None:
        LOG_BUFFER.unsubscribe(self.append)
        super().destroy()
