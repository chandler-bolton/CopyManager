"""Small stand-alone History / Pinned windows ("pop-outs").

They stay on top by default (Settings > Keep pop-outs on top), can be
filtered, and copy an item when you click it. Esc closes them.
"""
from __future__ import annotations

import re
import sys
import tkinter as tk
from typing import Any

from .. import clipboard
from ..store import HISTORY, PINNED
from ..util import one_line, parse_geometry, point_on_screen, time_ago
from .clips import DISPLAY_PREFS, hotkey_badge, show_item_menu
from .widgets import IconButton, ScrollFrame, SearchBox, bind_hover

TITLES = {HISTORY: "History", PINNED: "Pinned"}


class PopoutRow(tk.Frame):
    def __init__(self, master, popout: "PopoutWindow", item: dict[str, Any], index: int, count: int):
        app = popout.app
        theme, p, px = app.theme, app.theme.p, app.theme.px
        self.popout, self.item, self.index, self.count = popout, item, index, count
        super().__init__(master, bg=p["bg"], cursor="hand2")
        self.columnconfigure(1, weight=1)
        self._bg_widgets: list[tk.Widget] = [self]

        if popout.kind == HISTORY:
            badge = hotkey_badge(app, self, index, p["bg"])
            if badge.cget("text") == "":
                self._bg_widgets.append(badge)
        else:
            badge = tk.Label(self, text=theme.glyph("pin"), font=theme.fonts["icon_small"],
                             bg=p["bg"], fg=p["accent"], width=3)
            self._bg_widgets.append(badge)
        badge.grid(row=0, column=0, rowspan=2, sticky="n", padx=(px(8), px(8)), pady=px(7))

        text = item["text"]
        title = item.get("label") or (one_line(text, 140) if text.strip() else "(blank)")
        self.preview = tk.Label(self, text=title, anchor="w", justify="left", bg=p["bg"],
                                font=theme.fonts["bold" if item.get("label") else "base"],
                                fg=p["fg"] if text.strip() else p["fg_muted"])
        self.preview.grid(row=0, column=1, sticky="ew", pady=(px(6), 0), padx=(0, px(8)))
        when = time_ago(item.get("copied-at"))
        meta_text = "   ·   ".join(x for x in (when, f"{len(text):,} chars") if x)
        meta = tk.Label(self, text=meta_text, anchor="w", bg=p["bg"], fg=p["fg_muted"],
                        font=theme.fonts["small"])
        meta.grid(row=1, column=1, sticky="ew", pady=(0, px(6)), padx=(0, px(8)))
        self._bg_widgets += [self.preview, meta]

        for w in (self, badge, self.preview, meta):
            w.bind("<Button-1>", lambda _e: self.copy())
            w.bind("<Button-3>", self.menu)
        bind_hover(self, lambda: self._paint(p["row_hover"]), lambda: self._paint(p["bg"]))

    def _paint(self, bg: str) -> None:
        if self.winfo_exists():
            for w in self._bg_widgets:
                w.configure(bg=bg)

    def set_wrap(self, width: int) -> None:
        self.preview.configure(wraplength=width)

    def copy(self, mode: str = clipboard.ALL) -> None:
        if not self.popout.copy(self.item, mode):
            return
        if self.winfo_exists():
            self._paint(self.popout.app.theme.p["row_flash"])
            self.after(250, lambda: self._paint(self.popout.app.theme.p["row_hover"]))

    def menu(self, event) -> None:
        show_item_menu(self.popout.app, self, event, self.item, self.popout.kind,
                       self.index, self.count, self.copy)


class PopoutWindow(tk.Toplevel):
    DEFAULT_SIZE = (380, 520)

    def __init__(self, app, kind: str):
        self.app, self.kind = app, kind
        theme, p, px = app.theme, app.theme.p, app.theme.px
        super().__init__(app.root, bg=p["bg"])
        self.withdraw()
        self.title(f"{TITLES[kind]} · {app.window_title}")
        self.minsize(px(260), px(220))
        self._place()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.bind("<Escape>", lambda _e: self.close())

        # Header: title, count, keep-on-top toggle
        header = tk.Frame(self, bg=p["surface"])
        header.pack(fill="x")
        tk.Label(header, text=TITLES[kind], font=theme.fonts["heading"], bg=p["surface"],
                 fg=p["fg_strong"]).pack(side="left", padx=(px(12), px(6)), pady=px(8))
        self.count = tk.Label(header, font=theme.fonts["small"], bg=p["surface"], fg=p["fg_muted"])
        self.count.pack(side="left")
        self.pin_btn = IconButton(header, theme, "pin", command=self.toggle_on_top, bg=p["surface"],
                                  hover_bg=p["surface_hover"], tooltip=self._pin_tooltip)
        self.pin_btn.pack(side="right", padx=px(6))

        self.search = SearchBox(self, theme, f"Filter {TITLES[kind].lower()}",
                                lambda _q: self.refresh(scroll_top=True))
        self.search.pack(fill="x", padx=px(10), pady=(px(10), px(6)))
        self.list = ScrollFrame(self, theme, bg=p["bg"])
        self.list.pack(fill="both", expand=True, padx=(px(2), 0))
        self.list.canvas.bind("<Configure>", lambda _e: self._rewrap(), add="+")
        tk.Label(self, text="Click to copy  ·  Right-click for more  ·  Esc to close",
                 font=theme.fonts["small"], bg=p["surface"], fg=p["fg_muted"],
                 pady=px(4)).pack(fill="x", side="bottom")

        self.rows: list[PopoutRow] = []
        self._empty: tk.Label | None = None
        self._timer = self.after(60_000, self._tick)
        app.store.subscribe(self.refresh)
        app.cfg.subscribe(self._on_config)
        self._apply_on_top()
        self.refresh()
        self.deiconify()
        self.after(30, lambda: theme.apply_titlebar(self))

    # ---------- window placement ----------
    def _place(self) -> None:
        parsed = parse_geometry(self.app.cfg.state("popout-geometry").get(self.kind, ""))
        if parsed and parsed[2] is not None and point_on_screen(parsed[2] + 40, parsed[3] + 10):
            w, h, x, y = parsed
            self.geometry(f"{w}x{h}{x:+d}{y:+d}")
            return
        # First time: open next to the mouse pointer, kept on screen.
        w, h = (self.app.theme.px(v) for v in self.DEFAULT_SIZE)
        x, y = self.winfo_pointerxy()
        x = max(0, min(x - w // 2, self.winfo_screenwidth() - w))
        y = max(0, min(y - h // 3, self.winfo_screenheight() - h - 48))
        self.geometry(f"{w}x{h}+{x}+{y}")

    def show(self) -> None:
        """Bring to the front and focus the filter box."""
        self.deiconify()
        self.lift()
        self.focus_force()
        self.search.entry.focus_set()

    def is_foreground(self) -> bool:
        if sys.platform != "win32":
            return self.focus_displayof() is not None
        import ctypes
        try:
            return ctypes.windll.user32.GetForegroundWindow() == ctypes.windll.user32.GetParent(self.winfo_id())
        except Exception:
            return False

    def close(self) -> None:
        states = dict(self.app.cfg.state("popout-geometry"))
        if self.state() == "normal":
            states[self.kind] = self.geometry()
            self.app.cfg.set_state("popout-geometry", states)
        self.app.popout_closed(self.kind)
        self.destroy()

    # ---------- keep on top ----------
    def _on_top(self) -> bool:
        return bool(self.app.cfg.get("settings", "popout-always-on-top"))

    def _pin_tooltip(self) -> str:
        return "Stop keeping pop-outs on top" if self._on_top() else "Keep pop-outs on top"

    def toggle_on_top(self) -> None:
        self.app.cfg.set("settings", "popout-always-on-top", not self._on_top())

    def _apply_on_top(self) -> None:
        p = self.app.theme.p
        on_top = self._on_top()
        self.attributes("-topmost", int(on_top))
        self.pin_btn.configure(text=self.app.theme.glyph("pin" if on_top else "unpin"))
        self.pin_btn.set_base(fg=p["accent"] if on_top else p["fg_muted"])

    # ---------- content ----------
    def items(self) -> list[dict[str, Any]]:
        return self.app.store.history if self.kind == HISTORY else self.app.store.pinned

    def refresh(self, scroll_top: bool = False) -> None:
        # Clear everything in the list: rows, the divider lines between them and the empty message.
        # (Destroying only the rows left the dividers behind, so each refresh grew a blank area.)
        for child in self.list.inner.winfo_children():
            child.destroy()
        self.rows = []
        self._empty = None
        p, px = self.app.theme.p, self.app.theme.px
        items = self.items()
        self.count.configure(text=f"{len(items)} item{'s' if len(items) != 1 else ''}")
        q = self.search.get().strip().lower()
        shown = [(i, it) for i, it in enumerate(items)
                 if not q or q in it["text"].lower() or q in (it.get("label") or "").lower()]
        if not shown:
            text = "No matches." if items else ("Nothing copied yet." if self.kind == HISTORY else "No pinned items.")
            self._empty = tk.Label(self.list.inner, text=text, bg=p["bg"], fg=p["fg_muted"],
                                   font=self.app.theme.fonts["base"])
            self._empty.pack(pady=px(30))
        for n, (index, item) in enumerate(shown):
            if n:
                tk.Frame(self.list.inner, bg=p["border"], height=1).pack(fill="x", padx=px(10))
            row = PopoutRow(self.list.inner, self, item, index, len(items))
            row.pack(fill="x", padx=(px(2), px(6)))
            self.rows.append(row)
        self._rewrap()
        if scroll_top:
            self.list.scroll_to_top()

    def _rewrap(self) -> None:
        width = max(self.app.theme.px(120), self.list.canvas.winfo_width() - self.app.theme.px(70))
        for row in self.rows:
            row.set_wrap(width)

    def copy(self, item: dict[str, Any], mode: str = clipboard.ALL) -> bool:
        if not self.app.copy_item(item, mode):
            return False
        if self.app.cfg.get("preferences", "popout-close-on-copy"):
            self.after(120, self.close)  # brief flash so the click registers visually
        return True

    def _on_config(self, section: str, key: str, _value) -> None:
        if section == "settings" and key == "popout-always-on-top":
            self._apply_on_top()
        elif section == "preferences" and key in DISPLAY_PREFS:
            self.refresh()

    def _tick(self) -> None:
        self.refresh()
        self._timer = self.after(60_000, self._tick)

    def destroy(self) -> None:
        self.app.store.unsubscribe(self.refresh)
        self.app.cfg.unsubscribe(self._on_config)
        try:
            self.after_cancel(self._timer)
        except tk.TclError:
            pass
        super().destroy()
