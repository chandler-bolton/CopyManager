"""Preferences tab: how clipboard capture, pasting and the pop-outs work.

The C++ monitor reads the capture and paste values from utils/settings.json
("preferences"). The GUI itself handles the pop-out shortcut.
"""
from __future__ import annotations

from typing import Any

from .config import HOTKEY_MODIFIERS
from .ui.form import Field, FormView, Group
from .ui.shortcut_dialog import PASTE, ask_shortcut


class PreferencesView(FormView):
    section = "preferences"
    title = "Preferences"
    subtitle = ("How CopyManager captures what you copy, how the paste shortcuts behave, and how the "
                "pop-outs work. For the app's look and startup behaviour, see Settings. Changes save and apply "
                "immediately; Save & refresh re-applies everything and confirms what's active.")
    groups = (
        Group("Capture", (
            Field("capture-enabled", "Capture clipboard",
                  "Record new copies in History. Turn this off to pause capturing for a while."),
            Field("max-history-limit", "History size",
                  "Most items to keep in History. When it's full, the oldest items are removed first. "
                  "Lowering this removes items right away.",
                  kind="int", min=1, max=250),
            Field("pins-bypass-limit", "Pins don't count toward the limit",
                  "When this is off, each pinned item takes up one History slot."),
            Field("duplicate-handling", "When you copy something already in History",
                  "What to do when a new copy matches an existing History item.", kind="choice",
                  choices=(("move-to-top", "Move the existing item to the top"),
                           ("keep-both", "Keep both copies"),
                           ("ignore", "Ignore the new copy"))),
            Field("ignore-whitespace-only", "Ignore blank copies",
                  "Don't record copies that contain only spaces, tabs or line breaks."),
        )),
        Group("Shortcuts", (
            Field("hotkeys-enabled", "Enable paste shortcuts",
                  "Paste a History item into the focused window with a modifier + number key. "
                  "0 is the most recent item, 9 is the tenth."),
            Field("hotkey-modifiers", "Shortcut keys",
                  "Modifier keys used with 0–9. Choose Custom to pick your own combination.",
                  kind="choice",
                  choices=tuple((value, f"{label} + 0–9") for value, label in HOTKEY_MODIFIERS.items())
                  + (("custom", "Custom…"),)),
            Field("custom-hotkey-modifiers", "Custom shortcut keys",
                  "Hold these keys and press 0–9 to paste the matching History item. "
                  "You'll be warned if Windows already uses them.",
                  kind="modifiers", visible_when=("hotkey-modifiers", "custom")),
            Field("paste-mode", "Paste format", "What gets pasted when you use a shortcut.", kind="choice",
                  choices=(("keep-formatting", "Keep source formatting"),
                           ("text-only", "Plain text only"))),
            Field("trim-whitespace-on-paste", "Trim whitespace",
                  "Remove spaces and line breaks from the start and end of the text when pasting."),
            Field("paste-add-space", "Add a space after pasting",
                  "Type a space right after the pasted item, ready for the next word."),
            Field("paste-add-newline", "Add a new line after pasting",
                  "Press Enter right after the pasted item. (If both are on, the space comes first.) "
                  "In chat apps Enter usually sends the message."),
            Field("restore-clipboard-after-paste", "Restore clipboard after pasting",
                  "After a shortcut paste, put back whatever was on the clipboard before it."),
        ), note="Clicking an item in History, Pinned or a pop-out always copies its plain text. "
                "These options only apply to pasting with the keyboard shortcuts."),
        Group("Pop-out", (
            Field("popout-hotkey-enabled", "Enable pop-out shortcut",
                  "Open the History pop-out from anywhere with a keyboard shortcut. Press it again to close."),
            Field("popout-hotkey", "Pop-out shortcut",
                  "Works while CopyManager is running, even when minimized. "
                  "You'll be warned if Windows or another app already uses it.",
                  kind="hotkey"),
            Field("popout-close-on-copy", "Close after copying",
                  "Close the pop-out as soon as you click an item, so you can paste right away."),
        ), note="Pop-outs are small windows that show History or Pinned on their own. Open one with the "
                "pop-out button in the History or Pinned tab, or next to them in the sidebar."),
    )

    def before_commit(self, f: Field, value: Any) -> bool:
        # Choosing "Custom…" opens the shortcut prompt; it only switches to custom if the user saves.
        if f.key == "hotkey-modifiers" and value == "custom":
            spec = ask_shortcut(self.app, PASTE, self.app.cfg.get(self.section, "custom-hotkey-modifiers"))
            if spec:
                self.app.cfg.set(self.section, "custom-hotkey-modifiers", spec)
                self.app.cfg.set(self.section, "hotkey-modifiers", "custom")
            return False
        return True
