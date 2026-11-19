"""CopyManager - main window.

The C++ monitor (src/monitor.cpp) watches the clipboard and writes new copies to
src/history.json. This GUI shows that file, keeps it in sync, and edits
utils/settings.json (settings + preferences), which the monitor reads.

Layout:  [ sidebar | tabs (History, Pinned, Settings, Preferences, Output) ]
         [                       status bar                                ]
"""
from __future__ import annotations

import atexit
import logging
import os
import sys
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import messagebox
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import clipboard, elevation, paths, startup  # noqa: E402
from src.config import DEFAULT_STATE, Config  # noqa: E402
from src.history import HistoryView  # noqa: E402
from src import hotkeys  # noqa: E402
from src.hotkeys import GlobalHotkey  # noqa: E402
from src.monitor_link import MonitorLink  # noqa: E402
from src.output import LOG_BUFFER, LogTail, OutputView, monitor_level, setup_logging  # noqa: E402
from src.pinned import PinnedView  # noqa: E402
from src.preferences import PreferencesView  # noqa: E402
from src.settings import SettingsView  # noqa: E402
from src.store import HISTORY, PINNED, ClipStore  # noqa: E402
from src.ui.form import FormView  # noqa: E402
from src.ui.popout import PopoutWindow  # noqa: E402
from src.ui.sidebar import NavEntry, NavSection, Sidebar  # noqa: E402
from src.ui.statusbar import StatusBar  # noqa: E402
from src.ui.tabs import TabManager  # noqa: E402
from src.ui.theme import Theme  # noqa: E402
from src.ui.widgets import install_mousewheel  # noqa: E402
from src.util import one_line, parse_geometry, point_on_screen, read_json  # noqa: E402

log = logging.getLogger("app")

APP_TITLE = "Copy Manager"
# Every title the main window can have (older versions used "CopyManager"); used to find a running copy.
WINDOW_TITLES = (APP_TITLE, f"{APP_TITLE} | ADMIN", "CopyManager")

POLL_MS = 500        # how often history.json and monitor.log are checked for changes
HOTKEY_POLL_MS = 50   # how often the global-hotkey thread's queue is checked
REFRESH_TIMEOUT_MS = 3000  # how long "Save & refresh" waits for the monitor to confirm


@dataclass(frozen=True)
class ViewSpec:
    key: str
    title: str
    icon: str
    factory: Callable[[tk.Widget, "App"], tk.Widget]


VIEWS: dict[str, ViewSpec] = {v.key: v for v in (
    ViewSpec("history", "History", "history", HistoryView),
    ViewSpec("pinned", "Pinned", "pin", PinnedView),
    ViewSpec("settings", "Settings", "settings", SettingsView),
    ViewSpec("preferences", "Preferences", "preferences", PreferencesView),
    ViewSpec("output", "Output", "output", OutputView),
)}


def enable_dpi_awareness() -> None:
    """Crisp text on high-DPI displays (must run before Tk is created)."""
    if sys.platform != "win32":
        return
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


class App:
    def __init__(self, startup_note: str | None = None) -> None:
        setup_logging()
        self.elevated = elevation.is_elevated()
        self._closing = False
        self.layout_frozen = False  # True while the sidebar animates: views skip re-wrapping text until relayout()
        self._poll_job: str | None = None    # after() ids of the repeating checks, cancelled on close
        self._hotkey_job: str | None = None
        enable_dpi_awareness()
        self.root = tk.Tk()
        self.root.withdraw()  # build everything first, then show (avoids flicker)
        self.root.title(self.window_title)
        self.root.report_callback_exception = self._on_tk_error

        self.cfg = Config(paths.SETTINGS_PATH)
        self.store = ClipStore(paths.HISTORY_PATH, self.history_limits)
        self.store.enforce_limit()
        self.monitor_log = LogTail(paths.MONITOR_LOG_PATH)
        self.popouts: dict[str, PopoutWindow] = {}
        self.popout_hotkey = GlobalHotkey()
        self.popout_hotkey_error: str | None = None
        self.monitor_link = MonitorLink()
        self.monitor_link.start()
        atexit.register(self.shutdown)  # safety net if the app exits without quit() (e.g. a crash)
        self.last_refresh_report: tuple[str, bool] | None = None  # shown on the Settings/Preferences pages
        self._monitor_refresh_job: str | None = None

        s = self.cfg.data["settings"]
        self.theme = Theme(self.root, s["theme"], s["font-family"], s["font-size"])
        install_mousewheel(self.root)
        self.root.minsize(self.theme.px(560), self.theme.px(380))
        self.root.attributes("-topmost", int(s["always-on-top"]))
        self._restore_geometry()
        self._sync_startup_registration()

        self._build_ui()
        self._restore_tabs()
        self.cfg.subscribe(self._on_config_change)
        self.store.subscribe(self._on_store_change)
        self._bind_keys()
        self.root.protocol("WM_DELETE_WINDOW", self.quit)
        self.root.protocol("WM_SAVE_YOURSELF", self.quit)  # Windows is signing out / shutting down
        # However the main window goes away, stop the background work right then.
        self.root.bind("<Destroy>", lambda e: self.shutdown() if e.widget is self.root else None, add="+")

        self.root.deiconify()
        if self.cfg.state("window-maximized"):
            self.root.state("zoomed")
        if s["start-minimized"]:
            self.root.iconify()
        self.root.after(30, self.theme.apply_titlebar)
        if self.cfg.get("settings", "restore-open-tabs"):
            for kind in self.cfg.state("open-popouts"):
                if kind in (HISTORY, PINNED):
                    self.open_popout(kind)
        self._apply_popout_hotkey()
        self._poll()
        self._poll_hotkey()
        log.info("CopyManager started%s: %d in history, %d pinned", " as administrator" if self.elevated else "",
                 len(self.store.history), len(self.store.pinned))
        if startup_note:
            log.warning("%s", startup_note)
            self.root.after(500, lambda: self.statusbar.flash(startup_note, ms=8000))

    @property
    def window_title(self) -> str:
        """"Copy Manager", or "Copy Manager | ADMIN" when running as administrator."""
        return f"{APP_TITLE} | ADMIN" if self.elevated else APP_TITLE

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        p = self.theme.p
        self.shell = tk.Frame(self.root, bg=p["bg"])
        self.shell.pack(fill="both", expand=True)
        self.shell.rowconfigure(0, weight=1)
        self.shell.columnconfigure(2, weight=1)
        # TabManager is created before the sidebar because the sidebar asks which tab is active.
        self.tabs = TabManager(self.shell, self.theme, on_change=self._on_tabs_changed)
        self.sidebar = Sidebar(self.shell, self)
        self.sidebar.grid(row=0, column=0, sticky="ns")
        self.sidebar_divider = tk.Frame(self.shell, bg=p["border"], width=1)
        self.sidebar_divider.grid(row=0, column=1, sticky="ns")
        self._sliding = False  # True while the sidebar animates (see sidebar_slide_*)
        self.tabs.grid(row=0, column=2, sticky="nsew")
        self.statusbar = StatusBar(self.shell, self)
        self.statusbar.grid(row=1, column=0, columnspan=3, sticky="ew")

    def rebuild_ui(self) -> None:
        """Recreate every widget (used when the theme changes); open tabs are kept."""
        keys, active = self.tabs.open_keys(), self.tabs.active
        popouts = list(self.popouts)
        for kind in popouts:
            self.popouts[kind].close()
        self.theme.set_palette(self.cfg.get("settings", "theme"))
        self.shell.destroy()
        self._build_ui()
        for key in keys:
            self.open_view(key, select=False)
        if active:
            self.tabs.select(active)
        self.theme.apply_titlebar()
        for kind in popouts:
            self.open_popout(kind)

    def nav_sections(self) -> list[NavSection]:
        def view(key: str, popout: bool = False) -> NavEntry:
            spec = VIEWS[key]
            extra = ("popout", f"Pop out {spec.title.lower()}", lambda: self.open_popout(key)) if popout else None
            return NavEntry(key, spec.title, spec.icon, lambda: self.open_view(key), is_view=True, extra=extra)

        capturing = self.cfg.get("preferences", "capture-enabled")
        return [
            NavSection("clipboard", "Clipboard", [view("history", popout=True), view("pinned", popout=True)]),
            NavSection("configure", "Configure", [view("settings"), view("preferences")]),
            NavSection("tools", "Tools", [
                view("output"),
                NavEntry("capture", "Pause capturing" if capturing else "Resume capturing",
                         "pause" if capturing else "play", self.toggle_capture),
                NavEntry("clear", "Clear history", "clear", self.clear_history),
                NavEntry("folder", "Open data folder", "folder", self.open_data_folder),
            ] + ([] if self.elevated else [
                NavEntry("admin", "Restart as administrator", "shield", self.restart_as_admin),
            ])),
        ]

    # Sidebar collapse/expand animation. Resizing the main area on every frame is slow (each Tk
    # widget is a real window, so everything inside gets moved and redrawn), which made the
    # animation choppy. Instead the main area is sized once, to its final width, and slid
    # sideways as one piece while the sidebar animates; then it's put back in the grid at
    # exactly that size, so nothing shifts at the end.
    def sidebar_slide_start(self, final_sidebar_width: int) -> None:
        self.layout_frozen = True  # views re-wrap text once, at the end
        width = max(1, self.shell.winfo_width() - final_sidebar_width - self.sidebar_divider.winfo_width())
        height = self.sidebar.winfo_height()
        if not self._sliding:
            self._sliding = True
            self.tabs.grid_remove()
            self.tabs.place(x=self.sidebar.winfo_width() + self.sidebar_divider.winfo_width(), y=0,
                            width=width, height=height)
        else:  # toggled again mid-animation: just aim for the new final width
            self.tabs.place_configure(width=width, height=height)

    def sidebar_slide_frame(self, sidebar_width: int) -> None:
        if self._sliding:
            self.tabs.place_configure(x=sidebar_width + self.sidebar_divider.winfo_width())
        self._paint_now()

    def _paint_now(self) -> None:
        """Apply this frame's layout and repaint the uncovered areas right away. Otherwise Windows
        repaints them a moment later, and the old pixels briefly show through (smearing/ghosting)."""
        self.root.update_idletasks()
        if sys.platform == "win32":
            try:
                import ctypes
                hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
                # RDW_ALLCHILDREN | RDW_UPDATENOW: paint only what Windows has already marked as
                # needing it (the strips just uncovered), right now. Cheap: nothing else is redrawn.
                ctypes.windll.user32.RedrawWindow(hwnd, None, None, 0x0080 | 0x0100)
            except Exception:
                pass
        # Tk turns those paints into its own redraw events; handle them now, not on the next pass.
        self.root.tk.call("update")

    def sidebar_slide_end(self) -> None:
        if self._sliding:
            self._sliding = False
            self.tabs.place_forget()
            self.tabs.grid()  # same size it was sliding at, so nothing jumps
        self.layout_frozen = False
        self.relayout()

    def relayout(self) -> None:
        """Re-wrap text in the open views (after the sidebar finishes animating)."""
        for tab in self.tabs.tabs.values():
            view = tab.view
            rewrap = getattr(getattr(view, "list", None), "_rewrap", None) or getattr(view, "_rewrap", None)
            if rewrap:
                rewrap()

    def open_view(self, key: str, select: bool = True) -> None:
        spec = VIEWS[key]
        self.tabs.open(key, spec.title, spec.icon, lambda parent: spec.factory(parent, self), select=select)

    def _restore_tabs(self) -> None:
        if self.cfg.get("settings", "restore-open-tabs"):
            keys = [k for k in self.cfg.state("open-tabs") if k in VIEWS]
            active = self.cfg.state("active-tab")
        else:
            keys, active = list(DEFAULT_STATE["open-tabs"]), DEFAULT_STATE["active-tab"]
        for key in dict.fromkeys(keys):
            self.open_view(key, select=False)
        if active in self.tabs.tabs:
            self.tabs.select(active)

    def _restore_geometry(self) -> None:
        parsed = parse_geometry(self.cfg.state("window-geometry"))
        if parsed is None:
            self.root.geometry(DEFAULT_STATE["window-geometry"])
            return
        w, h, x, y = parsed
        # Only restore the position if the title bar would be on a screen (so the window can be dragged);
        # otherwise (e.g. it was above the top edge, or on a disconnected monitor) keep just the size.
        if x is not None and point_on_screen(x + 60, y + 10):
            self.root.geometry(f"{w}x{h}{x:+d}{y:+d}")
        else:
            self.root.geometry(f"{w}x{h}")

    def _bind_keys(self) -> None:
        def bind(sequence: str, fn: Callable[[], Any]) -> None:
            try:
                self.root.bind(sequence, lambda _e: (fn(), "break")[1])
            except tk.TclError:
                pass

        bind("<Control-w>", lambda: self.tabs.close_active())
        bind("<Control-Tab>", lambda: self.tabs.cycle(1))
        bind("<Control-Shift-Tab>", lambda: self.tabs.cycle(-1))
        bind("<Control-b>", lambda: self.sidebar.toggle())
        bind("<Control-f>", self._focus_search)
        bind("<Control-comma>", lambda: self.open_view("settings"))

    def _focus_search(self) -> None:
        view = self.tabs.current_view()
        if not hasattr(view, "focus_search"):
            self.open_view("history")
            view = self.tabs.current_view()
        view.focus_search()

    # ------------------------------------------------------------------ actions
    def history_limits(self) -> tuple[int, bool]:
        prefs = self.cfg.data["preferences"]
        return prefs["max-history-limit"], prefs["pins-bypass-limit"]

    def copy_item(self, item: dict[str, Any], mode: str = clipboard.ALL) -> bool:
        """Copy an item to the clipboard (clicking an item = ALL: text plus any formatting).

        The monitor sees copies that include text and, depending on duplicate handling,
        moves the item to the top of History. FORMAT_ONLY copies have no text, so it skips them.
        """
        preview = one_line(item["text"], 40)
        if not clipboard.is_supported():  # non-Windows fallback: plain text via Tk
            self.root.clipboard_clear()
            self.root.clipboard_append(item["text"])
            mode = clipboard.TEXT_ONLY
        else:
            try:
                clipboard.copy_item(item, mode, owner_hwnd=self.root.winfo_id())
            except (OSError, ValueError) as exc:
                log.warning("Couldn't copy item %s: %s", item["id"], exc)
                self.statusbar.flash(f"Couldn't copy: {exc}", ms=5000)
                return False
        what = {clipboard.ALL: "", clipboard.TEXT_ONLY: " as text only",
                clipboard.FORMAT_ONLY: " formatting only"}[mode]
        self.statusbar.flash(f"Copied “{preview}”{what}")
        log.info("Copied item %s to the clipboard%s (%d chars)", item["id"], what, len(item["text"]))
        return True

    def toggle_capture(self) -> None:
        self.cfg.set("preferences", "capture-enabled", not self.cfg.get("preferences", "capture-enabled"))

    def clear_history(self) -> None:
        count = len(self.store.history)
        if not count:
            self.statusbar.flash("History is already empty")
            return
        if self.cfg.get("settings", "confirm-clear-history") and not messagebox.askyesno(
                "Clear history", f"Remove all {count} item{'s' if count != 1 else ''} from History?\n\n"
                                 "Pinned items are kept.", icon="warning", parent=self.root):
            return
        self.store.clear_history()
        log.info("Cleared %d history item(s)", count)
        self.statusbar.flash("History cleared")

    def open_data_folder(self) -> None:
        os.startfile(paths.HISTORY_PATH.parent)  # type: ignore[attr-defined]

    # ------------------------------------------------------------------ pop-outs
    def open_popout(self, kind: str) -> None:
        window = self.popouts.get(kind)
        if window is None or not window.winfo_exists():
            window = self.popouts[kind] = PopoutWindow(self, kind)
        window.show()

    def toggle_popout(self, kind: str) -> None:
        """Global shortcut: open the pop-out, or close it if it's already visible.

        "Visible" = on top (it can't be hidden behind anything) or the active window.
        A pop-out buried behind other windows is brought forward instead.
        """
        window = self.popouts.get(kind)
        if window is not None and window.winfo_exists() and window.state() == "normal" and (
                self.cfg.get("settings", "popout-always-on-top") or window.is_foreground()):
            window.close()
        else:
            self.open_popout(kind)

    def popout_closed(self, kind: str) -> None:
        self.popouts.pop(kind, None)

    def _apply_popout_hotkey(self) -> None:
        prefs = self.cfg.data["preferences"]
        spec = prefs["popout-hotkey"] if prefs["popout-hotkey-enabled"] else None
        error = self.popout_hotkey_error = self.popout_hotkey.set(spec)
        if error:
            log.warning("Pop-out shortcut: %s", error)
            self.statusbar.flash(f"Pop-out shortcut: {error}", ms=8000)
        elif spec:
            log.info("Pop-out shortcut registered: %s", spec)

    def _poll_hotkey(self) -> None:
        if self._closing:
            return
        try:
            if self.popout_hotkey.drain():
                self.toggle_popout(HISTORY)
        finally:
            if not self._closing:
                self._hotkey_job = self.root.after(HOTKEY_POLL_MS, self._poll_hotkey)

    # ------------------------------------------------------------------ events
    def _on_tabs_changed(self) -> None:
        if hasattr(self, "sidebar"):
            self.sidebar.set_active(self.tabs.active)

    def _on_store_change(self) -> None:
        self.sidebar.update_badges()
        self.statusbar.refresh()

    def _on_config_change(self, section: str, key: str, value: Any) -> None:
        if section == "settings":
            if key == "theme":
                self.root.after_idle(self.rebuild_ui)
            elif key in ("font-family", "font-size"):
                s = self.cfg.data["settings"]
                self.theme.set_fonts(s["font-family"], s["font-size"])
                self.sidebar.on_fonts_changed()
            elif key == "always-on-top":
                self.root.attributes("-topmost", int(value))
            elif key == "run-on-startup":
                self._apply_startup(value)
            elif key == "run-as-admin":
                self.root.after_idle(self._on_run_as_admin_changed, value)
        elif section == "preferences":
            if key in ("max-history-limit", "pins-bypass-limit"):
                self.store.enforce_limit()
            elif key == "capture-enabled":
                log.info("Clipboard capture %s", "resumed" if value else "paused")
                self.sidebar.update_entries()  # "Pause capturing" <-> "Resume capturing", in place
            elif key in ("popout-hotkey", "popout-hotkey-enabled"):
                self._apply_popout_hotkey()
            self._schedule_monitor_refresh()
            self.statusbar.refresh()

    def _apply_startup(self, enabled: bool) -> None:
        if not startup.is_supported():
            return
        try:
            startup.set_enabled(enabled)
            log.info("Run on startup %s", "enabled" if enabled else "disabled")
        except OSError as exc:
            log.error("Could not update the startup registry entry: %s", exc)
            messagebox.showerror("Run on startup", f"Couldn't update the Windows startup entry:\n{exc}",
                                 parent=self.root)

    def _sync_startup_registration(self) -> None:
        """Make the registry match the setting (and refresh the path if the app moved)."""
        if not startup.is_supported():
            return
        try:
            if self.cfg.get("settings", "run-on-startup"):
                startup.set_enabled(True)
            elif startup.is_enabled():
                startup.set_enabled(False)
        except OSError as exc:
            log.warning("Could not sync the startup registry entry: %s", exc)

    # ------------------------------------------------------------------ clipboard monitor
    @property
    def monitor_error(self) -> str | None:
        return self.monitor_link.error

    def _schedule_monitor_refresh(self) -> None:
        """Preferences changed: have the monitor reload them right away (debounced).
        (It would also notice the file change within a second on its own.)"""
        if self._monitor_refresh_job:
            self.root.after_cancel(self._monitor_refresh_job)
        self._monitor_refresh_job = self.root.after(250, self._send_monitor_refresh)

    def _send_monitor_refresh(self) -> None:
        self._monitor_refresh_job = None
        self.monitor_link.request_refresh()

    def _on_monitor_status(self) -> None:
        """The monitor reported new status (e.g. which paste shortcuts it registered)."""
        self.statusbar.refresh()
        if "history" in self.tabs.tabs:
            self.tabs.tabs["history"].view.refresh()
        for window in self.popouts.values():
            window.refresh()

    def paste_shortcut_state(self) -> tuple[str, str, bool]:
        """(short text, full sentence, ok) for the 0-9 paste shortcuts, as the monitor actually has them."""
        prefs = self.cfg.data["preferences"]
        if not prefs["hotkeys-enabled"]:
            return "Paste shortcuts off", "Paste shortcuts are turned off.", True
        label = hotkeys.paste_modifier_label(prefs)
        if self.monitor_error:
            return (f"Paste {label}+0–9 (inactive)",
                    f"Paste shortcuts {label}+0–9 don't work right now: {self.monitor_error.lower()}.", False)
        wanted = hotkeys.parse(hotkeys.paste_spec(prefs))[0]
        if self.monitor_link.paste_modifiers() != wanted:
            return (f"Paste {label}+0–9 (applying…)",
                    f"Paste shortcuts {label}+0–9 haven't been picked up by the monitor yet.", True)
        failed = self.paste_failed_digits()
        if failed:
            keys = ", ".join(f"{label}+{d}" for d in failed)
            return (f"⚠ Paste {label}+0–9 ({len(failed)} unavailable)",
                    f"Paste shortcuts {label}+0–9 are active, except {keys}: already used by Windows "
                    "or another app.", False)
        return f"Paste {label}+0–9", f"Paste shortcuts {label}+0–9 are active.", True

    def paste_failed_digits(self) -> list[str]:
        """Digits whose paste shortcut the monitor couldn't register (with the current modifiers)."""
        prefs = self.cfg.data["preferences"]
        if not prefs["hotkeys-enabled"] or self.monitor_error:
            return []
        if self.monitor_link.paste_modifiers() != hotkeys.parse(hotkeys.paste_spec(prefs))[0]:
            return []
        return list(self.monitor_link.paste_state().get("failed", []))

    def popout_shortcut_state(self) -> tuple[str, bool]:
        prefs = self.cfg.data["preferences"]
        if not prefs["popout-hotkey-enabled"]:
            return "Pop-out shortcut is turned off.", True
        label = hotkeys.label(prefs["popout-hotkey"])
        if self.popout_hotkey_error:
            return f"Pop-out shortcut {label} isn't working: {self.popout_hotkey_error}", False
        return f"Pop-out shortcut {label} is active.", True

    # ------------------------------------------------------------------ save & refresh
    def save_and_refresh(self) -> None:
        """Save every setting and re-apply it everywhere, without restarting the app:
        the window (theme, fonts, on-top, startup entry), history limit, pop-out shortcut,
        and the clipboard monitor (capture + paste shortcuts)."""
        for view in self._form_views():
            view.flush()
        self.cfg.save()
        s = self.cfg.data["settings"]
        if s["theme"] != self.theme.name:
            self.rebuild_ui()
        else:
            self.theme.set_fonts(s["font-family"], s["font-size"])
            self.sidebar.render()
        self.root.attributes("-topmost", int(s["always-on-top"]))
        self._sync_startup_registration()
        self.store.enforce_limit()
        self._apply_popout_hotkey()
        for window in self.popouts.values():
            window._apply_on_top()
        log.info("Settings saved and refreshed")

        self._report_refresh("Saved. Refreshing the clipboard monitor…", True)
        self.monitor_link.ensure_running()
        refresh_id = self.monitor_link.request_refresh()
        self._await_monitor(refresh_id, self._now_ms() + REFRESH_TIMEOUT_MS)

    @staticmethod
    def _now_ms() -> int:
        import time
        return int(time.monotonic() * 1000)

    def _await_monitor(self, refresh_id: int | None, deadline: int) -> None:
        link = self.monitor_link
        link.read_status()
        if refresh_id is None and link.is_running():  # just (re)started: its first status is the answer
            if link.status.get("pid") == link.process.pid:
                return self._finish_refresh(True)
            refresh_id = link.request_refresh()  # its window may exist by now
        elif refresh_id is not None and link.status.get("refresh-id") == refresh_id:
            return self._finish_refresh(True)
        if self._now_ms() >= deadline or (refresh_id is None and not link.is_running()):
            return self._finish_refresh(False)
        self.root.after(100, self._await_monitor, refresh_id, deadline)

    def _finish_refresh(self, monitor_answered: bool) -> None:
        self._on_monitor_status()
        _short, paste_text, paste_ok = self.paste_shortcut_state()
        popout_text, popout_ok = self.popout_shortcut_state()
        parts = ["Saved and refreshed."]
        if self.monitor_error:
            parts.append(f"⚠ {self.monitor_error}: copies aren't recorded (see Output).")
        elif not monitor_answered:
            parts.append("⚠ The clipboard monitor didn't respond (see Output).")
        parts += [paste_text, popout_text]
        if self.cfg.get("settings", "run-as-admin") and not self.elevated:
            parts.append("\"Always run as administrator\" takes effect when CopyManager restarts "
                         "(or use Restart as administrator in the sidebar).")
        ok = monitor_answered and paste_ok and popout_ok and not self.monitor_error
        self._report_refresh("  ".join(parts), ok)
        self.statusbar.flash("Saved and refreshed" if ok else "Saved, with warnings (see Settings/Preferences)",
                             ms=5000)
        log.log(logging.INFO if ok else logging.WARNING, "%s", " ".join(parts))

    def _report_refresh(self, text: str, ok: bool) -> None:
        self.last_refresh_report = (text, ok)
        for view in self._form_views():
            view.set_refresh_status(text, ok)

    def _form_views(self) -> list[FormView]:
        return [tab.view for tab in self.tabs.tabs.values() if isinstance(tab.view, FormView)]

    def _poll(self) -> None:
        if self._closing:
            return
        try:
            if self.monitor_link.check():
                self._on_monitor_status()
            self.cfg.poll()
            self.store.poll()
            for line in self.monitor_log.poll():
                LOG_BUFFER.add(monitor_level(line), f"[monitor] {line}", "monitor")
            self.statusbar.refresh_sync()
        finally:
            if not self._closing:
                self._poll_job = self.root.after(POLL_MS, self._poll)

    def _on_tk_error(self, exc_type, exc, tb) -> None:
        log.error("Unexpected error: %s", exc, exc_info=(exc_type, exc, tb))
        try:
            self.statusbar.flash("Something went wrong. See Output for details.", ms=6000)
        except Exception:
            pass

    # ------------------------------------------------------------------ lifecycle
    def restart_as_admin(self) -> None:
        """Reopen this session as administrator, once. Doesn't change "Always run as administrator",
        so the next normal launch is a regular one again."""
        if self.elevated:
            self.statusbar.flash("CopyManager is already running as administrator")
            return
        started, error = elevation.relaunch_elevated()
        if not started:
            log.warning("Restart as administrator: %s", error)
            self.statusbar.flash(error, ms=6000)
            return
        log.info("Restarting as administrator")
        self.quit()  # the new copy waits for this one to exit completely

    def _on_run_as_admin_changed(self, enabled: bool) -> None:
        if enabled and not self.elevated:
            if messagebox.askyesno("Always run as administrator",
                                   "CopyManager will start as administrator from now on.\n\n"
                                   "Restart it as administrator now?", parent=self.root):
                self.restart_as_admin()
        elif not enabled and self.elevated:
            self.statusbar.flash("CopyManager will start without administrator rights next time", ms=6000)

    def quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            state = self.root.state()
            self.cfg.set_state("window-maximized", state == "zoomed")
            if state == "normal":
                self.cfg.set_state("window-geometry", self.root.geometry())
            self.cfg.set_state("open-tabs", self.tabs.open_keys())
            self.cfg.set_state("active-tab", self.tabs.active or "")
            self.cfg.set_state("open-popouts", list(self.popouts))
            for window in list(self.popouts.values()):
                window.close()  # saves its position
            self.cfg.save()
        finally:
            self.shutdown()
            self.root.destroy()
            log.info("CopyManager closed")

    def shutdown(self) -> None:
        """Stop everything that runs in the background. Safe to call more than once."""
        self._closing = True
        for job in (self._poll_job, self._hotkey_job, self._monitor_refresh_job):
            if job:
                try:
                    self.root.after_cancel(job)
                except Exception:
                    pass
        self._poll_job = self._hotkey_job = self._monitor_refresh_job = None
        self.popout_hotkey.stop()   # global-shortcut thread
        self.monitor_link.stop()    # clipboard monitor process (waits until it has exited)
        LOG_BUFFER.unsubscribe_all()

    def run(self) -> None:
        self.root.mainloop()


def claim_single_instance():
    """Allow only one running copy, so global shortcuts don't clash with ourselves.

    Returns a mutex handle to keep for the app's lifetime, or None if another
    copy is already running (that copy's window is brought to the front instead).
    """
    if sys.platform != "win32":
        return True
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, "Local\\CopyManager.SingleInstance")
    error = ctypes.get_last_error()
    # 183 = already exists. 5 = access denied, which means it exists but belongs to an
    # administrator copy of CopyManager that this (non-admin) one can't open.
    if handle and error != 183:
        return handle
    user32 = ctypes.windll.user32
    hwnd = next((h for h in (user32.FindWindowW(None, t) for t in WINDOW_TITLES) if h), 0)
    if hwnd:
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
    return None


def wants_admin() -> bool:
    """Settings > "Always run as administrator" (read straight from the file, before the app starts)."""
    try:
        return read_json(paths.SETTINGS_PATH).get("settings", {}).get("run-as-admin") is True
    except (OSError, ValueError, AttributeError):
        return False


def parse_args(argv: list[str]) -> dict[str, Any]:
    args: dict[str, Any] = {"after_pid": 0, "no_elevate": False}
    it = iter(argv)
    for arg in it:
        if arg == "--after-pid":
            args["after_pid"] = int(next(it, "0") or 0)
        elif arg == "--no-elevate":
            args["no_elevate"] = True
    return args


def main() -> None:
    args = parse_args(sys.argv[1:])
    if args["after_pid"]:
        elevation.wait_for_exit(args["after_pid"])  # relaunched: let the previous copy finish closing
    instance = claim_single_instance()
    if instance is None:
        return  # already running; the existing window was brought to the front
    note = None
    if wants_admin() and not args["no_elevate"] and not elevation.is_elevated():
        started, error = elevation.relaunch_elevated()
        if started:
            return  # the administrator copy takes over once this process has exited
        note = f"Started without administrator rights: {error}"
    try:
        App(startup_note=note).run()
    except Exception as exc:  # pythonw has no console, so show fatal errors in a dialog
        logging.getLogger("app").exception("Fatal error")
        messagebox.showerror("CopyManager", f"CopyManager failed to start:\n\n{exc}\n\nSee logs/app.log.")
        raise
    finally:
        logging.shutdown()  # flush and close the log files


if __name__ == "__main__":
    main()
