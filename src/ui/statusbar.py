"""Bottom status bar: capture on/off, counts, sync status, and short messages."""
from __future__ import annotations

import tkinter as tk
from typing import Callable

from .widgets import Tooltip


class StatusBar(tk.Frame):
    def __init__(self, master, app):
        self.app = app
        self.theme = app.theme
        super().__init__(master, bg=self.theme.p["statusbar"])
        self._items: list[tk.Label] = []
        self._hovered: tk.Label | None = None
        self._flash_job: str | None = None

        self.capture = self._item("left", self._capture_clicked, self._capture_tooltip)
        self.history = self._item("left", lambda: app.open_view("history"), "Open History")
        self.pinned = self._item("left", lambda: app.open_view("pinned"), "Open Pinned")
        self.shortcuts = self._item("left", lambda: app.open_view("preferences"), self._shortcuts_tooltip)
        if app.elevated:  # packed first on the right, so it sits in the far bottom-right corner
            self.admin = self._item("right", lambda: app.open_view("settings"),
                                    "CopyManager is running as administrator, so shortcut pastes also work "
                                    "in apps that run as administrator.")
            self.admin.configure(text="ADMIN", font=self.theme.fonts["caption"])
        self.sync = self._item("right", lambda: app.open_view("output"), "Open Output")
        self.message = self._item("right", None, None)
        self.refresh()

    @property
    def _capturing(self) -> bool:
        return bool(self.app.cfg.get("preferences", "capture-enabled")) and not self.app.monitor_error

    def _capture_clicked(self) -> None:
        if self.app.monitor_error:
            self.app.open_view("output")
        else:
            self.app.toggle_capture()

    def _shortcuts_tooltip(self) -> str:
        _short, paste, _ok = self.app.paste_shortcut_state()
        popout, _ok = self.app.popout_shortcut_state()
        return f"{paste}\n{popout}\nClick to change them in Preferences."

    def _capture_tooltip(self) -> str:
        if self.app.monitor_error:
            return "New copies aren't being recorded. Click to see Output for details."
        return "Click to pause capturing" if self._capturing else "Click to resume capturing"

    def _colors(self) -> tuple[str, str]:
        p = self.theme.p
        if self.app.elevated:  # red whenever running as administrator (the capture text still shows pauses)
            return p["statusbar_admin"], p["statusbar_admin_hover"]
        if self._capturing:
            return p["statusbar"], p["statusbar_hover"]
        return p["statusbar_paused"], p["statusbar_paused_hover"]

    def _item(self, side: str, command: Callable[[], None] | None, tooltip) -> tk.Label:
        p, px = self.theme.p, self.theme.px
        label = tk.Label(self, bg=p["statusbar"], fg=p["statusbar_fg"], font=self.theme.fonts["small"],
                         padx=px(10), pady=px(3), cursor="hand2" if command else "")
        label.pack(side=side)
        if command:
            label.bind("<Enter>", lambda _e: self._hover(label))
            label.bind("<Leave>", lambda _e: self._hover(None))
            label.bind("<Button-1>", lambda _e: command())
        if tooltip:
            Tooltip(label, self.theme, tooltip)
        self._items.append(label)
        return label

    def _hover(self, label: tk.Label | None) -> None:
        self._hovered = label
        self._paint()

    def _paint(self) -> None:
        base, hover = self._colors()
        self.configure(bg=base)
        for item in self._items:
            item.configure(bg=hover if item is self._hovered else base)

    def refresh(self) -> None:
        store, cfg = self.app.store, self.app.cfg
        if self.app.monitor_error:
            self.capture.configure(text=f"⚠  {self.app.monitor_error}")
        else:
            self.capture.configure(text="●  Capturing" if self._capturing else "○  Capture paused")
        self.history.configure(text=f"History {len(store.history)}/{store.history_capacity()}")
        self.pinned.configure(text=f"Pinned {len(store.pinned)}")
        self.shortcuts.configure(text=self.app.paste_shortcut_state()[0])
        self.refresh_sync()
        self._paint()

    def refresh_sync(self) -> None:
        store = self.app.store
        if store.error:
            text = "⚠  history.json unreadable"
        elif store.last_sync:
            text = f"Synced {store.last_sync:%H:%M:%S}"
        else:
            text = ""
        if self.sync.cget("text") != text:
            self.sync.configure(text=text)

    def flash(self, text: str, ms: int = 3000) -> None:
        if self._flash_job:
            self.after_cancel(self._flash_job)
        self.message.configure(text=text)
        self._flash_job = self.after(ms, lambda: self.message.configure(text=""))
