"""Pinned tab: items kept separately from History and never trimmed.

Also has a box for typing a new pinned item by hand. Typed pins are always
plain text: tk's Text widget only ever receives text, even when something
formatted is pasted into it, and the store saves them with no HTML/RTF.
"""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .store import PINNED
from .ui.clips import ClipView


class PinnedView(ClipView):
    kind = PINNED
    title = "Pinned"
    placeholder = "Filter pinned items"

    def items(self):
        return self.app.store.pinned

    def subtitle_text(self) -> str:
        n = len(self.app.store.pinned)
        bypass = self.app.cfg.get("preferences", "pins-bypass-limit")
        limit_note = "not counted toward the history limit" if bypass else "each pin uses one history slot"
        return (f"{n} pinned item{'s' if n != 1 else ''}  ·  {limit_note}  ·  "
                "click to copy, right-click to rename or reorder")

    def empty_message(self, has_items: bool) -> str:
        if has_items:
            return "No pinned items match your filter."
        return "No pinned items.\nType one above, or pin something from History."

    # ---------- "New pinned item" composer ----------
    def build_header_extras(self, parent: tk.Frame) -> None:
        theme, p, px = self.app.theme, self.app.theme.p, self.app.theme.px
        box = tk.Frame(parent, bg=p["surface"], highlightthickness=1, highlightbackground=p["border"],
                       padx=px(12), pady=px(10))
        box.pack(fill="x", pady=(0, px(12)))
        ttk.Label(box, text="New pinned item", style="Bold.TLabel", background=p["surface"]).pack(anchor="w")
        ttk.Label(box, text="Type or paste text to pin. It's saved as plain text, without formatting.",
                  style="Muted.TLabel", background=p["surface"]).pack(anchor="w", pady=(px(1), px(6)))

        # Text to pin
        self.new_text = tk.Text(box, height=3, wrap="word", undo=True, bg=p["input_bg"], fg=p["fg"],
                                insertbackground=p["fg"], relief="flat", bd=0, highlightthickness=1,
                                highlightbackground=p["border"], highlightcolor=p["accent"],
                                selectbackground=p["selection"], selectforeground=p["fg_strong"],
                                font=theme.fonts["base"], padx=px(8), pady=px(6))
        self.new_text.pack(fill="x")
        self._text_placeholder = tk.Label(box, text="Text to pin…", bg=p["input_bg"], fg=p["fg_muted"],
                                          font=theme.fonts["base"], cursor="xterm")
        self._text_placeholder.bind("<Button-1>", lambda _e: self.new_text.focus_set())

        # Optional label + button
        row = tk.Frame(box, bg=p["surface"])
        row.pack(fill="x", pady=(px(8), 0))
        self.new_label = tk.Entry(row, bg=p["input_bg"], fg=p["fg"], insertbackground=p["fg"], relief="flat",
                                  bd=0, highlightthickness=1, highlightbackground=p["border"],
                                  highlightcolor=p["accent"], font=theme.fonts["base"], width=28)
        self.new_label.pack(side="left", ipady=px(5), padx=(0, px(10)))
        self._label_placeholder = tk.Label(row, text="Label (optional)", bg=p["input_bg"], fg=p["fg_muted"],
                                           font=theme.fonts["base"], cursor="xterm")
        self._label_placeholder.bind("<Button-1>", lambda _e: self.new_label.focus_set())
        self.pin_btn = ttk.Button(row, text="Pin it", style="Accent.TButton", command=self.add_pin)
        self.pin_btn.pack(side="left")
        self._hint = ttk.Label(row, text="Ctrl+Enter", style="Muted.TLabel", background=p["surface"])
        self._hint.pack(side="left", padx=(px(10), 0))

        for widget in (self.new_text, self.new_label):
            widget.bind("<Control-Return>", lambda _e: (self.add_pin(), "break")[1])
            widget.bind("<KeyRelease>", lambda _e: self._sync_composer(), add="+")
            widget.bind("<FocusIn>", lambda _e: self._sync_composer(), add="+")
            widget.bind("<FocusOut>", lambda _e: self._sync_composer(), add="+")
        self.new_text.bind("<<Paste>>", lambda _e: self.after_idle(self._sync_composer), add="+")
        self.new_label.bind("<Return>", lambda _e: (self.add_pin(), "break")[1])
        self._sync_composer()

    def _typed_text(self) -> str:
        return self.new_text.get("1.0", "end-1c")  # exactly what was typed (minus Tk's own trailing newline)

    def _sync_composer(self) -> None:
        """Placeholders and the button's enabled state."""
        if self._typed_text():
            self._text_placeholder.place_forget()
        else:
            self._text_placeholder.place(in_=self.new_text, x=self.app.theme.px(9), y=self.app.theme.px(6))
        if self.new_label.get():
            self._label_placeholder.place_forget()
        else:
            self._label_placeholder.place(in_=self.new_label, x=self.app.theme.px(4), rely=0.5, anchor="w")
        self.pin_btn.state(["!disabled"] if self._typed_text().strip() else ["disabled"])

    def add_pin(self) -> None:
        text = self._typed_text()
        if not text.strip():
            return
        if any(item["text"] == text for item in self.app.store.pinned):
            self.app.statusbar.flash("That text is already pinned")
            return
        if self.app.store.add_pinned(text, self.new_label.get()):
            self.new_text.delete("1.0", "end")
            self.new_label.delete(0, "end")
            self._sync_composer()
            self.list.scroll_to_top()
            self.app.statusbar.flash("Pinned")
