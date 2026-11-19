"""Prompts for choosing keyboard shortcuts, with a clash check.

Two kinds of shortcut:
    "paste"   modifiers only, used with 0-9      (preferences: custom-hotkey-modifiers)
    "popout"  modifiers + one key                 (preferences: popout-hotkey)

If the chosen keys clash with Windows, another app, or another CopyManager
shortcut, the user is warned and asked whether to use them anyway: Yes saves
them, No reopens the prompt with the same keys selected.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk

from .. import hotkeys

PASTE, POPOUT = "paste", "popout"

TEXT = {
    PASTE: ("Custom paste shortcut",
            "Choose the keys to hold while pressing 0–9.\n0 pastes the most recent History item, 9 the tenth."),
    POPOUT: ("Pop-out shortcut",
             "Choose the keys that open and close the History pop-out\nfrom anywhere."),
}


class ShortcutDialog(tk.Toplevel):
    """Modal dialog. After it closes, .result is a shortcut string, or None if cancelled."""

    def __init__(self, app, kind: str, initial: str):
        theme, p, px = app.theme, app.theme.p, app.theme.px
        super().__init__(app.root, bg=p["bg"])
        self.app, self.kind = app, kind
        self.with_key = kind == POPOUT
        self.result: str | None = None
        title, intro = TEXT[kind]
        self.withdraw()
        self.title(title)
        self.resizable(False, False)
        self.transient(app.root)

        body = tk.Frame(self, bg=p["bg"], padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=title, style="Heading.TLabel").pack(anchor="w")
        ttk.Label(body, text=intro, style="Muted.TLabel", justify="left").pack(anchor="w", pady=(px(4), px(14)))

        mods, key = hotkeys.parse(initial)
        self.vars = {m: tk.BooleanVar(value=m in mods) for m in hotkeys.MODIFIER_ORDER}
        row = tk.Frame(body, bg=p["bg"])
        row.pack(anchor="w")
        for m in hotkeys.MODIFIER_ORDER:
            ttk.Checkbutton(row, text=hotkeys.MODIFIER_LABELS[m], variable=self.vars[m],
                            command=self._update).pack(side="left", padx=(0, px(14)))
        self.key_var = tk.StringVar(value=hotkeys.KEYS[key][0] if key else "")
        self._label_to_key = {lbl: k for k, (lbl, _vk) in hotkeys.KEYS.items()}
        if self.with_key:
            ttk.Label(row, text="+", style="Muted.TLabel").pack(side="left", padx=(0, px(10)))
            combo = ttk.Combobox(row, textvariable=self.key_var, state="readonly", width=9, height=14,
                                 values=[lbl for lbl, _vk in hotkeys.KEYS.values()])
            combo.bind("<<ComboboxSelected>>", lambda _e: (combo.selection_clear(), self._update()))
            combo.pack(side="left")
        else:
            ttk.Label(row, text="+  0–9", style="Muted.TLabel").pack(side="left")

        self.preview = ttk.Label(body, style="Bold.TLabel")
        self.preview.pack(anchor="w", pady=(px(14), 0))
        self.error = ttk.Label(body, style="Error.TLabel")
        self.error.pack(anchor="w", pady=(px(2), 0))

        buttons = tk.Frame(body, bg=p["bg"])
        buttons.pack(fill="x", pady=(px(16), 0))
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right")
        self.save_btn = ttk.Button(buttons, text="Save", style="Accent.TButton", command=self._save)
        self.save_btn.pack(side="right", padx=(0, px(8)))

        self.bind("<Return>", lambda _e: self._save())
        self.bind("<Escape>", lambda _e: self.destroy())
        self._update()
        self._center()
        self.deiconify()
        self.after(10, lambda: theme.apply_titlebar(self))
        self.grab_set()
        self.focus_force()

    def _spec(self) -> str:
        mods = {m for m, var in self.vars.items() if var.get()}
        key = self._label_to_key.get(self.key_var.get()) if self.with_key else None
        return hotkeys.build(mods, key)

    def _problem(self) -> str:
        mods, key = hotkeys.parse(self._spec())
        if not hotkeys.is_valid_modifiers(mods):
            return "Include at least one of Ctrl, Alt or Win."
        if self.with_key and not key:
            return "Pick a key."
        return ""

    def _update(self) -> None:
        spec = self._spec()
        suffix = "" if self.with_key else " + 0–9"
        self.preview.configure(text=f"Shortcut: {hotkeys.label(spec) or '—'}{suffix}")
        problem = self._problem()
        self.error.configure(text=problem)
        self.save_btn.state(["disabled"] if problem else ["!disabled"])

    def _save(self) -> None:
        if not self._problem():
            self.result = self._spec()
            self.destroy()

    def _center(self) -> None:
        self.update_idletasks()
        root = self.app.root
        w, h = self.winfo_reqwidth(), self.winfo_reqheight()
        x = root.winfo_rootx() + (root.winfo_width() - w) // 2
        y = root.winfo_rooty() + (root.winfo_height() - h) // 3
        self.geometry(f"+{max(0, x)}+{max(0, y)}")


# ---------------------------------------------------------------- clash checks
def _paste_modifiers(app, override: str | None = None) -> frozenset[str] | None:
    """Modifiers of CopyManager's own paste shortcuts, or None if they're turned off."""
    prefs = app.cfg.data["preferences"]
    if not prefs["hotkeys-enabled"]:
        return None
    if override is not None:
        return hotkeys.parse(override)[0]
    value = prefs["custom-hotkey-modifiers"] if prefs["hotkey-modifiers"] == "custom" else prefs["hotkey-modifiers"]
    return hotkeys.parse(value)[0]


def _own_popout(app) -> tuple[frozenset[str], str | None] | None:
    prefs = app.cfg.data["preferences"]
    if not prefs["popout-hotkey-enabled"]:
        return None
    return hotkeys.parse(prefs["popout-hotkey"])


def conflict_message(app, kind: str, spec: str) -> str | None:
    """Warning text if the shortcut clashes with Windows, another app or CopyManager itself, else None."""
    name = hotkeys.label(spec)
    reasons: list[str] = []
    mods, key = hotkeys.parse(spec)

    if kind == PASTE:
        known, taken = hotkeys.digit_conflicts(spec)
        # Digits our own monitor already holds with these keys show up as "taken"; they aren't a clash.
        owned = app.monitor_link.owned_digits(mods)
        taken = [d for d in taken if d not in owned]
        own = _own_popout(app)
        if own and own[0] == mods and own[1] in hotkeys.KEYS and own[1].isdigit():
            reasons.append(f"CopyManager: {name}+{own[1]} is the pop-out shortcut.")
            taken = [d for d in taken if d != own[1]]  # that one is held by this app, not someone else
        if known:
            reasons.append(f"Windows: {known}")
        elif taken:
            reasons.append("Registered by Windows or another running app: "
                           + ", ".join(f"{name}+{d}" for d in taken))
        shown = f"{name}+0–9"
    else:
        paste = _paste_modifiers(app)
        if paste is not None and paste == mods and key and key.isdigit():
            reasons.append(f"CopyManager: {name} is one of the paste shortcuts.")
        # The app's own current registration would show up as "taken", so skip the probe for it.
        elif spec != app.popout_hotkey.spec and key and hotkeys.is_taken(mods, key):
            reasons.append("It's already registered by Windows or another running app.")
        shown = name

    if not reasons:
        return None
    return (f"{shown} is already in use:\n\n" + "\n".join(f"•  {r}" for r in reasons)
            + "\n\nIt may not work, or may trigger the other action instead.\n\nUse this shortcut anyway?")


def ask_shortcut(app, kind: str, initial: str) -> str | None:
    """Show the prompt until the user saves a shortcut they're happy with, or cancels."""
    current = initial
    while True:
        dialog = ShortcutDialog(app, kind, current)
        app.root.wait_window(dialog)
        if dialog.result is None:
            return None
        warning = conflict_message(app, kind, dialog.result)
        if warning is None or messagebox.askyesno("Shortcut already in use", warning, icon="warning",
                                                  default="no", parent=app.root):
            return dialog.result
        current = dialog.result  # reopen with the same keys selected
