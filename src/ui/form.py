"""Builds a Settings/Preferences page from a list of field definitions.

Changes save right away, as in VSCode. A blue bar on the left of a field
means it differs from the default.
"""
from __future__ import annotations

import os
import tkinter as tk
from dataclasses import dataclass
from tkinter import messagebox, ttk
from typing import Any, Callable

from .. import hotkeys, paths
from .widgets import ScrollFrame


@dataclass(frozen=True)
class Field:
    key: str
    label: str
    description: str = ""
    kind: str = "bool"            # bool | int | choice | font | modifiers | hotkey
    choices: tuple[tuple[str, str], ...] = ()  # (value, label) pairs for "choice"
    min: int = 0
    max: int = 100
    visible_when: tuple[str, Any] | None = None  # (other key, value): only shown while that key has that value


@dataclass(frozen=True)
class Group:
    title: str
    fields: tuple[Field, ...]
    note: str = ""


class FormView(tk.Frame):
    section = ""            # "settings" or "preferences"
    title = ""
    subtitle = ""
    groups: tuple[Group, ...] = ()

    def __init__(self, master, app):
        self.app = app
        self.theme = theme = app.theme
        p, px = theme.p, theme.px
        super().__init__(master, bg=p["bg"])
        self._setters: dict[str, Callable[[Any], None]] = {}
        self._markers: dict[str, tk.Frame] = {}
        self._wrapping: list[ttk.Label] = []
        self._conditional: list[tuple[tk.Frame, Field]] = []
        self._flushers: list[Callable[[], None]] = []  # commit half-finished edits (e.g. a typed number)

        head = tk.Frame(self, bg=p["bg"])
        head.pack(fill="x", padx=(px(28), px(26)), pady=(px(20), px(4)))
        top = tk.Frame(head, bg=p["bg"])
        top.pack(fill="x")
        ttk.Label(top, text=self.title, style="Title.TLabel").pack(side="left")
        ttk.Button(top, text="Save & refresh", style="Accent.TButton",
                   command=lambda: app.save_and_refresh()).pack(side="right")
        ttk.Button(top, text="Reset to defaults", command=self._reset).pack(side="right", padx=(0, px(8)))
        ttk.Button(top, text="Open JSON", command=self._open_json).pack(side="right", padx=(0, px(8)))
        sub = ttk.Label(head, text=self.subtitle, style="Muted.TLabel", justify="left")
        sub.pack(anchor="w", pady=(px(2), 0))
        self._wrapping.append(sub)
        # Result of the last "Save & refresh" (what's active in the app and the monitor)
        self.refresh_status = ttk.Label(head, style="Muted.TLabel", justify="left")
        self.refresh_status.pack(anchor="w", pady=(px(4), 0))
        self._wrapping.append(self.refresh_status)
        if app.last_refresh_report:
            self.set_refresh_status(*app.last_refresh_report)

        self.scroll = ScrollFrame(self, theme, bg=p["bg"])
        self.scroll.pack(fill="both", expand=True)
        self.scroll.canvas.bind("<Configure>", self._rewrap, add="+")
        inner = self.scroll.inner
        inner.columnconfigure(0, weight=1)
        row = 0

        def place(widget, **grid):
            nonlocal row
            widget.grid(row=row, column=0, **grid)
            row += 1

        for group in self.groups:
            place(ttk.Label(inner, text=group.title, style="Heading.TLabel"),
                  sticky="w", padx=px(28), pady=(px(20), px(2)))
            if group.note:
                note = ttk.Label(inner, text=group.note, style="Muted.TLabel", justify="left")
                place(note, sticky="w", padx=px(28), pady=(0, px(4)))
                self._wrapping.append(note)
            place(tk.Frame(inner, bg=p["border"], height=1), sticky="ew", padx=(px(28), px(24)), pady=px(4))
            for f in group.fields:
                place(self._build_field(inner, f), sticky="ew", padx=(px(18), px(24)), pady=px(7))
        place(tk.Frame(inner, bg=p["bg"], height=px(24)))

        self._apply_visibility()
        app.cfg.subscribe(self._on_config)

    # ---------- fields ----------
    def _build_field(self, parent: tk.Widget, f: Field) -> tk.Frame:
        p, px = self.theme.p, self.theme.px
        value = self.app.cfg.get(self.section, f.key)
        row = tk.Frame(parent, bg=p["bg"])
        marker = tk.Frame(row, width=px(2), bg=p["bg"])
        marker.pack(side="left", fill="y", padx=(0, px(8)))
        self._markers[f.key] = marker
        content = tk.Frame(row, bg=p["bg"])
        content.pack(side="left", fill="x", expand=True)
        ttk.Label(content, text=f.label, style="Bold.TLabel").pack(anchor="w")

        if f.kind == "bool":
            line = tk.Frame(content, bg=p["bg"])
            line.pack(fill="x", pady=(px(4), 0))
            var = tk.BooleanVar(value=value)
            ttk.Checkbutton(line, variable=var, command=lambda: self._commit(f, var.get())).pack(
                side="left", anchor="n")
            desc = ttk.Label(line, text=f.description, style="Muted.TLabel", justify="left", cursor="hand2")
            desc.pack(side="left", fill="x", expand=True)
            desc.bind("<Button-1>", lambda _e: (var.set(not var.get()), self._commit(f, var.get())))
            self._wrapping.append(desc)
            self._setters[f.key] = var.set
        else:
            if f.description:
                desc = ttk.Label(content, text=f.description, style="Muted.TLabel", justify="left")
                desc.pack(anchor="w", pady=(px(2), px(6)))
                self._wrapping.append(desc)
            if f.kind == "int":
                self._build_int(content, f, value).pack(anchor="w")
            elif f.kind in ("modifiers", "hotkey"):
                self._build_shortcut(content, f, value)
            else:
                self._build_choice(content, f, value).pack(anchor="w")
        self._update_marker(f.key, value)
        if f.visible_when:
            self._conditional.append((row, f))
        return row

    def _build_int(self, parent, f: Field, value: int) -> ttk.Spinbox:
        var = tk.StringVar(value=str(value))

        def commit(_e=None):
            try:
                v = int(var.get())
            except ValueError:
                v = self.app.cfg.get(self.section, f.key)
            v = max(f.min, min(f.max, v))
            var.set(str(v))
            self._commit(f, v)

        spin = ttk.Spinbox(parent, from_=f.min, to=f.max, increment=1, textvariable=var, width=8, command=commit)
        spin.bind("<FocusOut>", commit)
        spin.bind("<Return>", commit)
        self._flushers.append(commit)
        self._setters[f.key] = lambda v: var.set(str(v))
        return spin

    def _build_choice(self, parent, f: Field, value: str) -> ttk.Combobox:
        pairs = [(fam, fam) for fam in self.theme.families] if f.kind == "font" else list(f.choices)
        to_label = {v: l for v, l in pairs}
        to_value = {l: v for v, l in pairs}
        labels = [l for _, l in pairs]
        var = tk.StringVar(value=to_label.get(value, str(value)))
        width = max(16, min(32, max((len(l) for l in labels), default=16) + 2))
        combo = ttk.Combobox(parent, textvariable=var, values=labels, state="readonly", width=width, height=14)

        def on_select(_e):
            combo.selection_clear()
            value = to_value.get(var.get(), var.get())
            if self.before_commit(f, value):
                self._commit(f, value)
            else:  # the hook handled it (or cancelled): show whatever is saved now
                var.set(to_label.get(self.app.cfg.get(self.section, f.key), ""))

        combo.bind("<<ComboboxSelected>>", on_select)
        self._setters[f.key] = lambda v: var.set(to_label.get(v, str(v)))
        return combo

    def _build_shortcut(self, parent, f: Field, value: str) -> None:
        """Read-only summary of a shortcut + a "Change…" button that opens the shortcut prompt.
        kind "modifiers" = modifiers used with 0-9; kind "hotkey" = modifiers + one key."""
        px = self.theme.px
        line = tk.Frame(parent, bg=self.theme.p["bg"])
        line.pack(anchor="w")
        current = ttk.Label(line, style="Bold.TLabel")
        current.pack(side="left", padx=(0, px(12)))
        ttk.Button(line, text="Change…", command=lambda: self.edit_shortcut(f)).pack(side="left")
        suffix = " + 0–9" if f.kind == "modifiers" else ""

        def show(spec: str) -> None:
            current.configure(text=f"{hotkeys.label(spec) or '—'}{suffix}")

        show(value)
        self._setters[f.key] = show

    def edit_shortcut(self, f: Field) -> None:
        from .shortcut_dialog import PASTE, POPOUT, ask_shortcut
        kind = PASTE if f.kind == "modifiers" else POPOUT
        spec = ask_shortcut(self.app, kind, self.app.cfg.get(self.section, f.key))
        if spec:
            self._commit(f, spec)

    def before_commit(self, f: Field, value: Any) -> bool:
        """Subclasses can intercept a choice. Return False if it was handled/cancelled."""
        return True

    # ---------- behaviour ----------
    def flush(self) -> None:
        """Commit edits that haven't been saved yet (called by "Save & refresh")."""
        for commit in self._flushers:
            commit()

    def set_refresh_status(self, text: str, ok: bool) -> None:
        if self.refresh_status.winfo_exists():
            self.refresh_status.configure(text=text, style="Muted.TLabel" if ok else "Error.TLabel")

    def _commit(self, f: Field, value: Any) -> None:
        self.app.cfg.set(self.section, f.key, value)

    def _update_marker(self, key: str, value: Any) -> None:
        p = self.theme.p
        modified = value != self.app.cfg.default(self.section, key)
        self._markers[key].configure(bg=p["accent"] if modified else p["bg"])

    def _apply_visibility(self) -> None:
        for row, f in self._conditional:
            other, wanted = f.visible_when
            if self.app.cfg.get(self.section, other) == wanted:
                row.grid()
            else:
                row.grid_remove()

    def _on_config(self, section: str, key: str, value: Any) -> None:
        if section != self.section:
            return
        if key in self._setters:
            self._setters[key](value)
            self._update_marker(key, value)
        self._apply_visibility()

    def _rewrap(self, _e=None) -> None:
        if self.app.layout_frozen:  # sidebar animating: re-wrap once when it's done (App.relayout)
            return
        width = max(self.theme.px(200), self.scroll.canvas.winfo_width() - self.theme.px(120))
        for label in self._wrapping:
            label.configure(wraplength=width)

    def _reset(self) -> None:
        if messagebox.askyesno(f"Reset {self.title.lower()}",
                               f"Restore all {self.title.lower()} to their default values?",
                               parent=self.app.root):
            self.app.cfg.reset(self.section)
            self.app.statusbar.flash(f"{self.title} reset to defaults")

    def _open_json(self) -> None:
        try:
            os.startfile(paths.SETTINGS_PATH)  # type: ignore[attr-defined]
        except OSError as exc:
            messagebox.showerror("Open JSON", str(exc), parent=self.app.root)

    def destroy(self) -> None:
        self.app.cfg.unsubscribe(self._on_config)
        super().destroy()
