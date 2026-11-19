"""Shared list UI for the History and Pinned tabs."""
from __future__ import annotations

import tkinter as tk
from tkinter import simpledialog, ttk
from typing import Any, Callable

from .. import clipboard
from ..hotkeys import paste_modifier_label
from ..store import HISTORY, PINNED
from ..util import time_ago
from .widgets import IconButton, ScrollFrame, SearchBox, Tooltip, bind_hover

HOTKEY_SLOTS = 10  # History items 0-9 can be pasted with <modifier>+<digit>; 0 = most recent
# Preferences that change how rows look (badges, subtitles)
DISPLAY_PREFS = ("max-history-limit", "pins-bypass-limit", "hotkeys-enabled",
                 "hotkey-modifiers", "custom-hotkey-modifiers")


def show_item_menu(app, widget: tk.Widget, event, item: dict[str, Any], kind: str,
                   index: int, count: int, on_copy: Callable[[str], None]) -> None:
    """Right-click menu shared by the History/Pinned tabs and the pop-outs.
    on_copy(mode) copies the item; mode is clipboard.ALL / TEXT_ONLY / FORMAT_ONLY."""
    p, store, item_id = app.theme.p, app.store, item["id"]

    def pin():
        store.pin(item_id)
        app.statusbar.flash("Pinned")

    def unpin():
        store.unpin(item_id)
        app.statusbar.flash("Moved back to History")

    def delete():
        store.delete(item_id)
        app.statusbar.flash("Deleted")

    def rename():
        label = simpledialog.askstring("Rename pinned item", "Label (leave empty to remove it):",
                                       initialvalue=item.get("label") or "", parent=widget.winfo_toplevel())
        if label is not None:
            store.rename_pinned(item_id, label)

    menu = tk.Menu(widget, tearoff=0, bg=p["surface"], fg=p["fg"],
                   activebackground=p["accent"], activeforeground=p["accent_fg"])
    formatted = clipboard.has_formatting(item)
    menu.add_command(label="Copy", command=lambda: on_copy(clipboard.ALL))
    menu.add_command(label="Copy Text Only", command=lambda: on_copy(clipboard.TEXT_ONLY))
    menu.add_command(label="Copy Format Only" if formatted else "Copy Format Only (no formatting saved)",
                     command=lambda: on_copy(clipboard.FORMAT_ONLY),
                     state="normal" if formatted else "disabled")
    menu.add_separator()
    if kind == HISTORY:
        menu.add_command(label="Pin", command=pin)
    else:
        menu.add_command(label="Rename…", command=rename)
        menu.add_command(label="Move up", command=lambda: store.move_pinned(item_id, -1),
                         state="normal" if index > 0 else "disabled")
        menu.add_command(label="Move down", command=lambda: store.move_pinned(item_id, 1),
                         state="normal" if index < count - 1 else "disabled")
        menu.add_command(label="Unpin", command=unpin)
    menu.add_separator()
    menu.add_command(label="Delete", command=delete)
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()


def hotkey_badge(app, parent: tk.Widget, index: int, bg: str) -> tk.Label:
    """Numbered badge for History items 0-9 (blank label for the rest, to keep alignment)."""
    theme, p = app.theme, app.theme.p
    if index >= HOTKEY_SLOTS:
        return tk.Label(parent, text="", bg=bg, font=theme.fonts["caption"], width=3)
    prefs = app.cfg.data["preferences"]
    enabled = prefs["hotkeys-enabled"]
    unavailable = str(index) in app.paste_failed_digits()
    badge = tk.Label(parent, text=str(index), font=theme.fonts["caption"], width=3, fg=p["badge_fg"],
                     bg=p["danger"] if unavailable else (p["badge_bg"] if enabled else p["badge_off"]))

    def tooltip() -> str:
        label = paste_modifier_label(app.cfg.data["preferences"])
        if not app.cfg.get("preferences", "hotkeys-enabled"):
            return "Paste shortcuts are turned off in Preferences"
        if str(index) in app.paste_failed_digits():
            return f"{label}+{index} is already used by Windows or another app, so it can't paste this item"
        if app.monitor_error:
            return f"{label}+{index} won't paste right now: {app.monitor_error.lower()}"
        return f"Paste with {label}+{index}"

    Tooltip(badge, theme, tooltip)
    return badge


def make_preview(text: str, max_lines: int = 3, max_chars: int = 240) -> tuple[str, bool]:
    """Returns (preview text, is_blank)."""
    if not text.strip():
        return "(blank)", True
    lines = text.expandtabs(4).splitlines()
    while lines and not lines[0].strip():
        lines.pop(0)
    out = []
    for line in lines[:max_lines]:
        line = line.rstrip()
        out.append(line if len(line) <= max_chars else line[: max_chars - 1] + "…")
    if len(lines) > max_lines:
        out[-1] += "  …"
    return "\n".join(out), False


def describe(item: dict[str, Any], kind: str) -> str:
    text = item["text"]
    parts = []
    when = time_ago(item.get("pinned-at")) if kind == PINNED else ""
    if when:
        parts.append(f"Pinned {when}")
    else:
        when = time_ago(item.get("copied-at"))
        if when:
            parts.append(f"Copied {when}")
    parts.append(f"{len(text):,} chars")
    line_count = len(text.splitlines())
    if line_count > 1:
        parts.append(f"{line_count:,} lines")
    formats = [name for name in ("html", "rtf") if item.get(name)]
    if formats:
        parts.append(" + ".join(f.upper() for f in formats))
    if item.get("source"):
        parts.append(item["source"])
    return "   ·   ".join(parts)


class ClipRow(tk.Frame):
    def __init__(self, master, clip_list: "ClipList", item: dict[str, Any], index: int, count: int):
        app = clip_list.app
        theme, p, px = app.theme, app.theme.p, app.theme.px
        self.app, self.item, self.kind = app, item, clip_list.kind
        self.index, self.count = index, count
        self._hover = False
        super().__init__(master, bg=p["row"], highlightthickness=1, highlightbackground=p["border"],
                         cursor="hand2")
        self._bg_widgets: list[tk.Widget] = [self]
        self.columnconfigure(1, weight=1)

        self.badge = self._make_badge()
        self.badge.grid(row=0, column=0, sticky="n", padx=(px(12), px(10)), pady=px(10))

        body = tk.Frame(self, bg=p["row"])
        body.grid(row=0, column=1, sticky="ew", pady=px(8))
        self._bg_widgets.append(body)
        clickable: list[tk.Widget] = [self, body, self.badge]
        if item.get("label"):
            title = tk.Label(body, text=item["label"], font=theme.fonts["bold"], bg=p["row"],
                             fg=p["fg_strong"], anchor="w")
            title.pack(fill="x")
            self._bg_widgets.append(title)
            clickable.append(title)
        preview, blank = make_preview(item["text"])
        self.preview = tk.Label(body, text=preview, font=theme.fonts["base"], bg=p["row"],
                                fg=p["fg_muted"] if blank else p["fg"], anchor="w", justify="left")
        self.preview.pack(fill="x")
        meta = tk.Label(body, text=describe(item, self.kind), font=theme.fonts["small"], bg=p["row"],
                        fg=p["fg_muted"], anchor="w")
        meta.pack(fill="x", pady=(px(3), 0))
        self._bg_widgets += [self.preview, meta]
        clickable += [self.preview, meta]

        actions = tk.Frame(self, bg=p["row"])
        actions.grid(row=0, column=2, sticky="ne", padx=(px(6), px(8)), pady=px(6))
        self._bg_widgets.append(actions)
        self.buttons = [IconButton(actions, theme, "copy", self.copy, "Copy to clipboard", bg=p["row"])]
        if self.kind == HISTORY:
            self.buttons.append(IconButton(actions, theme, "pin", self.pin, "Pin", bg=p["row"]))
        else:
            self.buttons.append(IconButton(actions, theme, "unpin", self.unpin,
                                           "Unpin (move back to History)", bg=p["row"]))
        self.buttons.append(IconButton(actions, theme, "delete", self.delete, "Delete", bg=p["row"],
                                       hover_fg=p["danger"]))
        for b in self.buttons:
            b.pack(side="left")

        for w in clickable:
            w.bind("<Button-1>", lambda _e: self.copy())
        for w in clickable + [actions]:
            w.bind("<Button-3>", self.show_menu)
        bind_hover(self, lambda: self._set_hover(True), lambda: self._set_hover(False))

    def _make_badge(self) -> tk.Label:
        theme, p = self.app.theme, self.app.theme.p
        if self.kind == PINNED:
            badge = tk.Label(self, text=theme.glyph("pin"), font=theme.fonts["icon_small"],
                             bg=p["row"], fg=p["accent"], width=3)
            self._bg_widgets.append(badge)
            return badge
        badge = hotkey_badge(self.app, self, self.index, p["row"])
        if self.index >= HOTKEY_SLOTS:
            self._bg_widgets.append(badge)
        return badge

    # ---------- visuals ----------
    def _paint(self, bg: str) -> None:
        if not self.winfo_exists():
            return
        for w in self._bg_widgets:
            w.configure(bg=bg)
        for b in self.buttons:
            b.set_base(bg=bg)

    def _set_hover(self, hover: bool) -> None:
        self._hover = hover
        p = self.app.theme.p
        self._paint(p["row_hover"] if hover else p["row"])
        self.configure(highlightbackground=p["accent"] if hover else p["border"])

    def set_wrap(self, width: int) -> None:
        self.preview.configure(wraplength=width)

    # ---------- actions ----------
    def copy(self, mode: str = clipboard.ALL) -> None:
        if not self.app.copy_item(self.item, mode):
            return
        if self.winfo_exists():
            self._paint(self.app.theme.p["row_flash"])
            self.after(250, lambda: self._set_hover(self._hover))

    def pin(self) -> None:
        self.app.store.pin(self.item["id"])
        self.app.statusbar.flash("Pinned")

    def unpin(self) -> None:
        self.app.store.unpin(self.item["id"])
        self.app.statusbar.flash("Moved back to History")

    def delete(self) -> None:
        self.app.store.delete(self.item["id"])
        self.app.statusbar.flash("Deleted")

    def show_menu(self, event) -> None:
        show_item_menu(self.app, self, event, self.item, self.kind, self.index, self.count, self.copy)


class ClipList(ScrollFrame):
    def __init__(self, master, app, kind: str, empty_text):
        super().__init__(master, app.theme, bg=app.theme.p["bg"])
        self.app, self.kind, self.empty_text = app, kind, empty_text
        self.rows: list[ClipRow] = []
        self._empty: tk.Label | None = None
        self.canvas.bind("<Configure>", lambda _e: self._rewrap(), add="+")

    def set_items(self, items: list[dict[str, Any]], query: str) -> None:
        for child in self.inner.winfo_children():  # everything, so nothing can pile up between refreshes
            child.destroy()
        self.rows = []
        self._empty = None
        p, px = self.app.theme.p, self.app.theme.px
        q = query.strip().lower()
        shown = [(i, it) for i, it in enumerate(items)
                 if not q or q in it["text"].lower() or q in (it.get("label") or "").lower()]
        if not shown:
            self._empty = tk.Label(self.inner, text=self.empty_text(bool(items)), bg=p["bg"],
                                   fg=p["fg_muted"], font=self.app.theme.fonts["base"], justify="center")
            self._empty.pack(pady=px(48))
            return
        for index, item in shown:
            row = ClipRow(self.inner, self, item, index, len(items))
            row.pack(fill="x", padx=(px(28), px(16)), pady=(0, px(6)))
            self.rows.append(row)
        self._rewrap()

    def _rewrap(self) -> None:
        if self.app.layout_frozen:  # sidebar animating: re-wrap once when it's done (App.relayout)
            return
        width = max(self.app.theme.px(160), self.canvas.winfo_width() - self.app.theme.px(230))
        for row in self.rows:
            row.set_wrap(width)


class ClipView(tk.Frame):
    """Base for HistoryView / PinnedView."""
    kind = HISTORY
    title = ""
    placeholder = "Filter"

    def __init__(self, master, app):
        self.app = app
        theme, p, px = app.theme, app.theme.p, app.theme.px
        super().__init__(master, bg=p["bg"])

        head = tk.Frame(self, bg=p["bg"])
        head.pack(fill="x", padx=(px(28), px(26)), pady=(px(20), px(12)))
        top = tk.Frame(head, bg=p["bg"])
        top.pack(fill="x")
        ttk.Label(top, text=self.title, style="Title.TLabel").pack(side="left")
        actions = tk.Frame(top, bg=p["bg"])
        actions.pack(side="right")
        self.build_actions(actions)
        self.subtitle = ttk.Label(head, style="Muted.TLabel")
        self.subtitle.pack(anchor="w", pady=(px(2), px(12)))
        self.build_header_extras(head)
        self.search = SearchBox(head, theme, self.placeholder, lambda _q: self.refresh(scroll_top=True))
        self.search.pack(fill="x")

        self.list = ClipList(self, app, self.kind, self.empty_message)
        self.list.pack(fill="both", expand=True, pady=(0, px(8)))

        app.store.subscribe(self.refresh)
        app.cfg.subscribe(self._on_config)
        self._timer = self.after(60_000, self._tick)
        self.refresh()

    # Subclasses override these
    def build_header_extras(self, parent: tk.Frame) -> None:
        """Anything to show between the subtitle and the filter box (e.g. the Pinned tab's composer)."""

    def build_actions(self, parent: tk.Frame) -> None:
        ttk.Button(parent, text="Pop out", command=lambda: self.app.open_popout(self.kind)).pack(
            side="right", padx=(0, self.app.theme.px(8)))

    def items(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def subtitle_text(self) -> str:
        return ""

    def empty_message(self, has_items: bool) -> str:
        return "No items match your filter." if has_items else "Nothing here yet."

    # ---------- behaviour ----------
    def refresh(self, scroll_top: bool = False) -> None:
        self.subtitle.configure(text=self.subtitle_text())
        self.list.set_items(self.items(), self.search.get())
        if scroll_top:
            self.list.scroll_to_top()

    def focus_search(self) -> None:
        self.search.focus()

    def _on_config(self, section: str, key: str, _value) -> None:
        if section == "preferences" and key in DISPLAY_PREFS:
            self.refresh()

    def _tick(self) -> None:
        self.refresh()  # keeps the "5 min ago" labels current
        self._timer = self.after(60_000, self._tick)

    def destroy(self) -> None:
        self.app.store.unsubscribe(self.refresh)
        self.app.cfg.unsubscribe(self._on_config)
        try:
            self.after_cancel(self._timer)
        except tk.TclError:
            pass
        super().destroy()
