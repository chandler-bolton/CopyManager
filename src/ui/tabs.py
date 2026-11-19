"""Browser/VSCode-style tabs: open, switch, close (×, middle-click, Ctrl+W)."""
from __future__ import annotations

import tkinter as tk
from typing import TYPE_CHECKING, Callable

from .widgets import IconButton, bind_hover

if TYPE_CHECKING:
    from .theme import Theme

ViewFactory = Callable[[tk.Widget], tk.Widget]

SHORTCUTS = (
    ("Ctrl+B", "Show or collapse the sidebar"),
    ("Ctrl+Tab", "Next tab"),
    ("Ctrl+W", "Close tab"),
    ("Ctrl+F", "Filter the current list"),
    ("Ctrl+,", "Open Settings"),
)


class _Tab:
    def __init__(self, manager: "TabManager", key: str, title: str, icon: str):
        theme, p, px = manager.theme, manager.theme.p, manager.theme.px
        self.manager, self.key = manager, key
        self.view: tk.Widget | None = None
        self.hover = False

        self.frame = tk.Frame(manager.bar, bg=p["tab"], cursor="hand2")
        self.accent = tk.Frame(self.frame, height=px(2), bg=p["tab"])
        self.accent.pack(side="top", fill="x")
        self.body = tk.Frame(self.frame, bg=p["tab"])
        self.body.pack(side="top", fill="both", expand=True)
        self.icon = tk.Label(self.body, text=theme.glyph(icon), font=theme.fonts["icon_small"], bg=p["tab"])
        self.icon.pack(side="left", padx=(px(12), px(6)), pady=(px(6), px(8)))
        self.label = tk.Label(self.body, text=title, font=theme.fonts["base"], bg=p["tab"])
        self.label.pack(side="left")
        self.close_btn = IconButton(self.body, theme, "close", command=lambda: manager.close(key),
                                    tooltip="Close (Ctrl+W)", padx=px(3), pady=px(1))
        self.close_btn.pack(side="left", padx=(px(10), px(6)))
        self.sep = tk.Frame(manager.bar, width=1, bg=p["border"])

        for w in (self.frame, self.accent, self.body, self.icon, self.label):
            w.bind("<Button-1>", lambda _e: manager.select(key))
            w.bind("<Button-2>", lambda _e: manager.close(key))  # middle-click
            w.bind("<Button-3>", self._menu)
        bind_hover(self.frame, self._enter, self._leave)
        self.frame.pack(side="left", fill="y")
        self.sep.pack(side="left", fill="y")

    def _enter(self):
        self.hover = True
        self.paint()

    def _leave(self):
        self.hover = False
        self.paint()

    def paint(self):
        if not self.frame.winfo_exists():
            return
        p = self.manager.theme.p
        active = self.manager.active == self.key
        bg = p["tab_active"] if active else (p["tab_hover"] if self.hover else p["tab"])
        fg = p["fg_strong"] if active else p["fg_muted"]
        for w in (self.frame, self.body, self.icon, self.label):
            w.configure(bg=bg)
        self.icon.configure(fg=p["accent"] if active else fg)
        self.label.configure(fg=fg)
        self.accent.configure(bg=p["accent"] if active else bg)
        # Like VSCode: the × shows on the active tab and on hover, but always takes up space.
        self.close_btn.set_base(bg=bg, fg=fg if (active or self.hover) else bg)

    def _menu(self, event):
        p, m = self.manager.theme.p, self.manager
        menu = tk.Menu(self.frame, tearoff=0, bg=p["surface"], fg=p["fg"],
                       activebackground=p["accent"], activeforeground=p["accent_fg"])
        menu.add_command(label="Close", accelerator="Ctrl+W", command=lambda: m.close(self.key))
        menu.add_command(label="Close others", command=lambda: m.close_all(except_key=self.key),
                         state="normal" if len(m.tabs) > 1 else "disabled")
        menu.add_command(label="Close all", command=m.close_all)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()


class TabManager(tk.Frame):
    def __init__(self, master, theme: "Theme", on_change: Callable[[], None]):
        p = theme.p
        super().__init__(master, bg=p["bg"])
        self.theme, self.on_change = theme, on_change
        self.tabs: dict[str, _Tab] = {}
        self.active: str | None = None
        self.bar = tk.Frame(self, bg=p["tabbar"])
        self._bar_shown = False
        self.body = tk.Frame(self, bg=p["bg"])
        self.body.pack(fill="both", expand=True)
        self.empty = self._build_empty()
        self.empty.place(relx=0.5, rely=0.45, anchor="center")

    # ---------- public API ----------
    def open(self, key: str, title: str, icon: str, factory: ViewFactory, select: bool = True) -> None:
        if key not in self.tabs:
            tab = _Tab(self, key, title, icon)
            tab.view = factory(self.body)
            self.tabs[key] = tab
            if not self._bar_shown:
                self.bar.pack(side="top", fill="x", before=self.body)
                self._bar_shown = True
            tab.paint()
        if select or self.active is None:
            self.select(key)

    def select(self, key: str) -> None:
        if key not in self.tabs:
            return
        if self.active and self.active != key and self.active in self.tabs:
            self.tabs[self.active].view.pack_forget()
        self.active = key
        self.empty.place_forget()
        view = self.tabs[key].view
        view.pack(fill="both", expand=True)
        for tab in self.tabs.values():
            tab.paint()
        if hasattr(view, "on_show"):
            view.on_show()
        self.on_change()

    def close(self, key: str) -> None:
        keys = self.open_keys()
        tab = self.tabs.pop(key, None)
        if tab is None:
            return
        index = keys.index(key)
        tab.frame.destroy()
        tab.sep.destroy()
        tab.view.destroy()
        if self.active == key:
            self.active = None
            if self.tabs:
                remaining = self.open_keys()
                self.select(remaining[min(index, len(remaining) - 1)])
                return
            self.bar.pack_forget()
            self._bar_shown = False
            self.empty.place(relx=0.5, rely=0.45, anchor="center")
        self.on_change()

    def close_all(self, except_key: str | None = None) -> None:
        for key in self.open_keys():
            if key != except_key:
                self.close(key)

    def close_active(self) -> None:
        if self.active:
            self.close(self.active)

    def cycle(self, step: int) -> None:
        keys = self.open_keys()
        if len(keys) < 2 or self.active not in keys:
            return
        self.select(keys[(keys.index(self.active) + step) % len(keys)])

    def open_keys(self) -> list[str]:
        return list(self.tabs)

    def current_view(self) -> tk.Widget | None:
        return self.tabs[self.active].view if self.active else None

    # ---------- empty state ----------
    def _build_empty(self) -> tk.Frame:
        theme, p, px = self.theme, self.theme.p, self.theme.px
        f = tk.Frame(self.body, bg=p["bg"])
        tk.Label(f, text=theme.glyph("copy"), font=theme.fonts["icon_hero"], bg=p["bg"],
                 fg=p["border"]).pack(pady=(0, px(12)))
        tk.Label(f, text="Copy Manager", font=theme.fonts["title"], bg=p["bg"], fg=p["fg_strong"]).pack()
        tk.Label(f, text="Open History, Pinned, Settings and more from the sidebar.",
                 font=theme.fonts["base"], bg=p["bg"], fg=p["fg_muted"]).pack(pady=(px(4), px(18)))
        grid = tk.Frame(f, bg=p["bg"])
        grid.pack()
        for row, (keys, desc) in enumerate(SHORTCUTS):
            tk.Label(grid, text=desc, font=theme.fonts["small"], bg=p["bg"], fg=p["fg_muted"],
                     anchor="e").grid(row=row, column=0, sticky="e", padx=(0, px(12)), pady=px(2))
            tk.Label(grid, text=keys, font=theme.fonts["caption"], bg=p["surface"], fg=p["fg"],
                     padx=px(6), pady=px(1)).grid(row=row, column=1, sticky="w", pady=px(2))
        return f
