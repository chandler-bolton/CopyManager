"""Left-hand navigation: collapsible sections, and the whole bar collapses to icons (Ctrl+B).

Built once and only ever updated (rebuilding is what made it flash), and laid out
so collapsing doesn't need to move anything:

  * One panel. Every row has a fixed 48px icon column on the left with the label to
    its right, and the panel always keeps its full width. The sidebar itself is just
    a window onto it: collapsing narrows that window to 48px, so the icons stay
    exactly where they are while the labels slide out of view. (Swapping between a
    separate icon-only panel made the icons jump.)
  * Section headers keep their place too; their chevrons simply become thin dividers
    while collapsed.
  * While the width animates, the main area defers re-wrapping its text until the end
    (App.layout_frozen), so the content stretches smoothly instead of re-flowing on
    every frame.
  * A section's items slide open/closed inside a clipping frame whose height animates.
"""
from __future__ import annotations

import time
import tkinter as tk
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable

from .widgets import IconButton, Tooltip, bind_hover

if TYPE_CHECKING:
    from .theme import Theme

ANIMATION_MS = 180   # total duration of a collapse/expand
FRAME_MS = 12        # ~80 fps target; slow frames are skipped, not queued
RAIL = 48            # width of the icon column (= the collapsed sidebar), at 96 DPI


@dataclass
class NavEntry:
    key: str
    label: str
    icon: str
    action: Callable[[], None]
    is_view: bool = False  # views get the "active" highlight when their tab is selected
    extra: tuple[str, str, Callable[[], None]] | None = None  # (icon, tooltip, action): hover button on the row


@dataclass
class NavSection:
    key: str
    title: str
    entries: list[NavEntry] = field(default_factory=list)


def _rail_columns(frame: tk.Frame, theme: "Theme") -> None:
    """Column 0: 2px marker, column 1: the icon cell (rail minus marker), column 2: label (stretches)."""
    frame.grid_columnconfigure(0, minsize=theme.px(2))
    frame.grid_columnconfigure(1, minsize=theme.px(RAIL) - theme.px(2))
    frame.grid_columnconfigure(2, weight=1)


class _NavItem(tk.Frame):
    def __init__(self, master, sidebar: "Sidebar", entry: NavEntry):
        theme = sidebar.theme
        p, px = theme.p, theme.px
        super().__init__(master, bg=p["surface"], cursor="hand2")
        self.theme, self.entry = theme, entry
        self.active = self.hover = False
        _rail_columns(self, theme)
        self.marker = tk.Frame(self, width=px(2), bg=p["surface"])
        self.marker.grid(row=0, column=0, sticky="ns")
        self.icon = tk.Label(self, text=theme.glyph(entry.icon), bg=p["surface"], fg=p["fg_muted"],
                             font=theme.fonts["icon_small"])
        self.icon.grid(row=0, column=1, pady=px(7))  # centred in the rail: same spot collapsed or not
        # Only collapsed rows need a tooltip (the label is visible otherwise).
        Tooltip(self.icon, theme, lambda: self.entry.label if sidebar.collapsed else "")
        self.label = tk.Label(self, text=entry.label, bg=p["surface"], fg=p["fg"],
                              font=theme.fonts["base"], anchor="w")
        self.label.grid(row=0, column=2, sticky="ew")
        self.badge = tk.Label(self, text="", bg=p["badge_off"], fg=p["fg_strong"],
                              font=theme.fonts["caption"], padx=px(6))
        self.extra = None
        if entry.extra:
            icon, tip, action = entry.extra
            self.extra = IconButton(self, theme, icon, command=action, tooltip=tip, bg=p["surface"],
                                    hover_bg=p["button_hover"], padx=px(4), pady=px(1))
            self.extra.grid(row=0, column=4, padx=(px(2), px(8)))
        for w in (self, self.marker, self.icon, self.label):
            w.bind("<Button-1>", lambda _e: self.entry.action())
        bind_hover(self, self._enter, self._leave)
        self.paint()

    def update_entry(self, entry: NavEntry) -> None:
        """Change the label/icon/action in place (e.g. "Pause capturing" <-> "Resume capturing")."""
        self.entry = entry
        self.icon.configure(text=self.theme.glyph(entry.icon))
        self.label.configure(text=entry.label)

    def _enter(self):
        self.hover = True
        self.paint()

    def _leave(self):
        self.hover = False
        self.paint()

    def set_active(self, active: bool) -> None:
        if active != self.active:
            self.active = active
            self.paint()

    def set_badge(self, text: str) -> None:
        shown = bool(self.badge.winfo_manager())
        if self.badge.cget("text") != text:
            self.badge.configure(text=text)
        if text and not shown:
            self.badge.grid(row=0, column=3, padx=(0, self.theme.px(4 if self.extra else 12)))
        elif not text and shown:
            self.badge.grid_remove()

    def paint(self):
        p = self.theme.p
        bg = p["surface_active"] if self.active else (p["surface_hover"] if self.hover else p["surface"])
        for w in (self, self.icon, self.label):
            w.configure(bg=bg)
        self.marker.configure(bg=p["accent"] if self.active else bg)
        self.icon.configure(fg=p["fg_strong"] if (self.active or self.hover) else p["fg_muted"])
        self.label.configure(fg=p["fg_strong"] if self.active else p["fg"])
        if self.extra is not None:  # only visible while hovering the row
            self.extra.set_base(bg=bg, fg=p["fg_muted"] if self.hover else bg)


@dataclass
class _Section:
    header: tk.Frame
    chevron: tk.Label
    divider: tk.Frame  # shown in the chevron's place while the sidebar is collapsed
    title: str
    clip: tk.Frame     # fixed-height window onto `body`; its height is what gets animated
    body: tk.Frame


class Sidebar(tk.Frame):
    def __init__(self, master, app):
        self.app = app
        self.theme = app.theme
        p = self.theme.p
        super().__init__(master, bg=p["surface"])
        self.pack_propagate(False)
        self.grid_propagate(False)
        self.items: dict[str, _NavItem] = {}
        self.sections: dict[str, _Section] = {}
        self._jobs: dict[str, str] = {}          # running animations
        self._animating: set[str] = set()
        self._active: str | None = None

        self.panel = tk.Frame(self, bg=p["surface"])
        self._build()
        self.panel.place(x=0, y=0, relheight=1, width=self.full_width())  # fixed width: clipped, never squashed
        self.configure(width=self.compact_width() if self.collapsed else self.full_width())
        self._set_compact_look(self.collapsed)
        self.update_badges()

    # ---------- sizes / state ----------
    def full_width(self) -> int:
        return self.theme.px(max(220, 22 * self.theme.font_size))

    def compact_width(self) -> int:
        return self.theme.px(RAIL)

    @property
    def collapsed(self) -> bool:
        return bool(self.app.cfg.state("sidebar-collapsed"))

    # ---------- building (once) ----------
    def _build(self) -> None:
        theme, p, px = self.theme, self.theme.p, self.theme.px
        header = tk.Frame(self.panel, bg=p["surface"])
        header.pack(fill="x")
        _rail_columns(header, theme)
        self.toggle_btn = IconButton(header, theme, "menu", command=self.toggle, bg=p["surface"],
                                     hover_bg=p["surface_hover"], font=theme.fonts["icon"], pady=px(8),
                                     tooltip=lambda: "Expand sidebar (Ctrl+B)" if self.collapsed
                                     else "Collapse sidebar (Ctrl+B)")
        self.toggle_btn.grid(row=0, column=1)
        tk.Label(header, text="COPY MANAGER", font=theme.fonts["caption"], bg=p["surface"],
                 fg=p["fg_muted"], anchor="w").grid(row=0, column=2, sticky="w")

        closed_sections = set(self.app.cfg.state("collapsed-sections"))
        for section in self.app.nav_sections():
            closed = section.key in closed_sections
            row = tk.Frame(self.panel, bg=p["surface"], cursor="hand2")
            row.pack(fill="x", pady=(px(4), 0))
            _rail_columns(row, theme)
            chevron = tk.Label(row, text=theme.glyph("chevron-right" if closed else "chevron-down"),
                               font=theme.fonts["icon_small"], bg=p["surface"], fg=p["fg_muted"])
            chevron.grid(row=0, column=1, pady=px(3))
            divider = tk.Frame(row, bg=p["border"], height=1, width=px(22))
            title = tk.Label(row, text=section.title.upper(), font=theme.fonts["caption"],
                             bg=p["surface"], fg=p["fg_muted"], anchor="w")
            title.grid(row=0, column=2, sticky="w")
            parts = (row, chevron, title)
            for w in parts:
                w.bind("<Button-1>", lambda _e, k=section.key: self.toggle_section(k))
            Tooltip(row, theme, lambda t=section.title, k=section.key: (
                f"{'Show' if k in self.app.cfg.state('collapsed-sections') else 'Hide'} {t}"
                if self.collapsed else ""))
            bind_hover(row,
                       lambda ws=parts: [w.configure(bg=p["surface_hover"]) for w in ws],
                       lambda ws=parts: [w.configure(bg=p["surface"]) for w in ws])

            clip = tk.Frame(self.panel, bg=p["surface"], height=1)
            clip.pack_propagate(False)
            body = tk.Frame(clip, bg=p["surface"])
            body.place(x=0, y=0, relwidth=1)  # natural height; the clip shows as much as its own height
            for entry in section.entries:
                item = _NavItem(body, self, entry)
                item.pack(fill="x")
                self.items[entry.key] = item
            body.bind("<Configure>", lambda _e, k=section.key: self._fit_section(k))
            self.sections[section.key] = _Section(row, chevron, divider, section.title, clip, body)
            if not closed:
                clip.pack(fill="x", after=row)

    def _set_compact_look(self, compact: bool) -> None:
        """Collapsed: chevrons become thin dividers. The chevron stays in the layout with blank
        text (keeping the row exactly as tall) and the divider is drawn over it with place(),
        which doesn't affect layout, so nothing below moves by even a pixel."""
        closed = set(self.app.cfg.state("collapsed-sections"))
        for key, sec in self.sections.items():
            if compact:
                sec.chevron.configure(text="")
                sec.divider.place(in_=sec.chevron, relx=0.5, rely=0.5, anchor="center")
            else:
                sec.divider.place_forget()
                sec.chevron.configure(text=self.theme.glyph("chevron-right" if key in closed else "chevron-down"))

    def _fit_section(self, key: str) -> None:
        """Keep an open section's clip exactly as tall as its items (e.g. after a font change)."""
        sec = self.sections[key]
        if f"section:{key}" not in self._animating and sec.clip.winfo_manager():
            sec.clip.configure(height=max(1, sec.body.winfo_reqheight()))

    # ---------- animation ----------
    def _animate(self, name: str, start: int, end: int, apply: Callable[[int], None],
                 done: Callable[[], None] | None = None) -> None:
        """Time-based: each frame computes how far along it should be, so the animation always
        takes about ANIMATION_MS even if a frame is slow (it just skips ahead instead of dragging)."""
        if name in self._jobs:
            self.after_cancel(self._jobs.pop(name))
        self._animating.add(name)
        began = time.perf_counter()

        def tick() -> None:
            t = min(1.0, (time.perf_counter() - began) * 1000 / ANIMATION_MS)
            eased = 1 - (1 - t) ** 3  # ease-out: quick start, gentle finish
            apply(round(start + (end - start) * eased))
            if t < 1.0:
                self._jobs[name] = self.after(FRAME_MS, tick)
            else:
                self._jobs.pop(name, None)
                self._animating.discard(name)
                if done:
                    done()

        tick()

    # ---------- collapse / expand ----------
    def toggle(self) -> None:
        """Collapse the whole sidebar to its icon rail, or expand it again (Ctrl+B)."""
        collapse = not self.collapsed
        self.app.cfg.set_state("sidebar-collapsed", collapse)
        current = int(self.cget("width"))
        target = self.compact_width() if collapse else self.full_width()
        if not collapse:
            self._set_compact_look(False)  # chevrons back before the labels slide in
        self.app.sidebar_slide_start(target)  # main area: sized once, then just slid along
        # Do that one-time resize now, before the animation clock starts, so the first frame
        # isn't spent waiting on it (which made the animation skip ahead).
        self.update_idletasks()

        def frame(width: int) -> None:
            self.configure(width=max(1, width))
            self.app.sidebar_slide_frame(max(1, width))

        def finished() -> None:
            if collapse:
                self._set_compact_look(True)
            self.app.sidebar_slide_end()

        self._animate("width", current, target, frame, done=finished)

    def toggle_section(self, key: str) -> None:
        """Slide a section's items open or closed."""
        closed = set(self.app.cfg.state("collapsed-sections"))
        sec = self.sections[key]
        name = f"section:{key}"
        apply = lambda h: sec.clip.configure(height=max(1, h))  # noqa: E731
        current = sec.clip.winfo_height() if sec.clip.winfo_manager() else 1
        show_chevron = not self.collapsed  # while collapsed, the divider stands in for it
        if key in closed:  # open it
            closed.discard(key)
            if show_chevron:
                sec.chevron.configure(text=self.theme.glyph("chevron-down"))
            if not sec.clip.winfo_manager():
                sec.clip.configure(height=1)
                sec.clip.pack(fill="x", after=sec.header)
            self._animate(name, current, sec.body.winfo_reqheight(), apply)
        else:              # close it
            closed.add(key)
            if show_chevron:
                sec.chevron.configure(text=self.theme.glyph("chevron-right"))
            self._animate(name, current, 1, apply, done=sec.clip.pack_forget)
        self.app.cfg.set_state("collapsed-sections", sorted(closed))

    # ---------- live updates (no rebuilding) ----------
    def update_entries(self) -> None:
        """Refresh labels/icons that depend on state, e.g. "Pause capturing" <-> "Resume capturing"."""
        for section in self.app.nav_sections():
            for entry in section.entries:
                if entry.key in self.items:
                    self.items[entry.key].update_entry(entry)

    def on_fonts_changed(self) -> None:
        """Font size changed: the full width depends on it (section heights follow automatically)."""
        self.panel.place_configure(width=self.full_width())
        if not self.collapsed and "width" not in self._animating:
            self.configure(width=self.full_width())

    def render(self) -> None:  # kept for callers that just want everything up to date
        self.update_entries()
        self.on_fonts_changed()
        self.update_badges()

    def set_active(self, view_key: str | None) -> None:
        self._active = view_key
        for key, item in self.items.items():
            item.set_active(item.entry.is_view and key == view_key)

    def update_badges(self) -> None:
        store = self.app.store
        counts = {"history": len(store.history), "pinned": len(store.pinned)}
        for key, count in counts.items():
            if key in self.items:
                self.items[key].set_badge(str(count) if count else "")
