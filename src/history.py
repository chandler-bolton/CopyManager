"""History tab: the most recent copies, newest first."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from .hotkeys import paste_modifier_label
from .store import HISTORY
from .ui.clips import ClipView


class HistoryView(ClipView):
    kind = HISTORY
    title = "History"
    placeholder = "Filter history"

    def build_actions(self, parent: tk.Frame) -> None:
        ttk.Button(parent, text="Clear history", command=self.app.clear_history).pack(side="right")
        super().build_actions(parent)

    def items(self):
        return self.app.store.history

    def subtitle_text(self) -> str:
        store, prefs = self.app.store, self.app.cfg.data["preferences"]
        text = f"{len(store.history)} of {store.history_capacity()} items  ·  newest first  ·  click an item to copy it"
        if prefs["hotkeys-enabled"]:
            text += f"  ·  {paste_modifier_label(prefs)}+0–9 pastes the numbered items"
        return text

    def empty_message(self, has_items: bool) -> str:
        if has_items:
            return "No items match your filter."
        if not self.app.cfg.get("preferences", "capture-enabled"):
            return "History is empty and capturing is paused.\nResume capturing from the status bar or the sidebar."
        return "Nothing copied yet.\nCopy something anywhere in Windows and it will show up here."
