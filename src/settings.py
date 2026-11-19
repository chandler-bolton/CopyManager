"""Settings tab: how the app itself behaves and looks."""
from __future__ import annotations

from .ui.form import Field, FormView, Group


class SettingsView(FormView):
    section = "settings"
    title = "Settings"
    subtitle = ("How the CopyManager app itself behaves and looks. For how copying and pasting "
                "work, see Preferences. Changes save and apply immediately; Save & refresh re-applies "
                "everything and confirms what's active.")
    groups = (
        Group("General", (
            Field("run-on-startup", "Run on startup",
                  "Launch CopyManager automatically when you sign in to Windows."),
            Field("run-as-admin", "Always run as administrator",
                  "Start CopyManager with administrator rights, so shortcut pastes also work in apps that run "
                  "as administrator. Windows asks for permission each time it starts. To do this just once, "
                  "use \"Restart as administrator\" in the sidebar instead."),
            Field("start-minimized", "Start minimized",
                  "Open minimized to the taskbar instead of showing the window."),
            Field("always-on-top", "Always on top",
                  "Keep the CopyManager window above other windows."),
            Field("restore-open-tabs", "Restore open tabs",
                  "Reopen the tabs you had open when the app was last closed."),
            Field("confirm-clear-history", "Confirm before clearing history",
                  "Ask before removing every item from History."),
            Field("popout-always-on-top", "Keep pop-outs on top",
                  "History and Pinned pop-out windows stay above other windows."),
        )),
        Group("Appearance", (
            Field("theme", "Theme", "Color theme for the window.", kind="choice",
                  choices=(("dark", "Dark"), ("light", "Light"))),
            Field("font-family", "Font", "Font used throughout the app.", kind="font"),
            Field("font-size", "Font size", "Base font size in points.", kind="int", min=8, max=20),
        )),
    )
