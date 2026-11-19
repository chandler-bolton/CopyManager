"""Colors, fonts, icon glyphs and ttk styling."""
from __future__ import annotations

import sys
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk

PALETTES: dict[str, dict] = {
    "dark": {
        "dark": True,
        "bg": "#1e1e1e", "surface": "#252526", "surface_hover": "#2a2d2e", "surface_active": "#37373d",
        "tabbar": "#252526", "tab": "#2d2d2d", "tab_hover": "#323233", "tab_active": "#1e1e1e",
        "fg": "#cccccc", "fg_muted": "#9d9d9d", "fg_strong": "#ffffff",
        "accent": "#0078d4", "accent_hover": "#1a8ae6", "accent_fg": "#ffffff",
        "border": "#3c3c3c", "row": "#252526", "row_hover": "#2d2e30", "row_flash": "#0e4a7a",
        "input_bg": "#313131", "button": "#3a3d41", "button_hover": "#45494e", "button_fg": "#ffffff",
        "danger": "#f14c4c", "warning": "#cca700", "info": "#4fc1ff", "selection": "#264f78",
        "statusbar": "#007acc", "statusbar_hover": "#1f8ad2",
        "statusbar_paused": "#b35c00", "statusbar_paused_hover": "#c96d0c", "statusbar_fg": "#ffffff",
        "statusbar_admin": "#b3261e", "statusbar_admin_hover": "#c9372c",
        "badge_bg": "#0e639c", "badge_fg": "#ffffff", "badge_off": "#4d4d4d",
        "scroll_thumb": "#424242", "scroll_thumb_hover": "#555555", "tooltip_bg": "#2b2b2b",
    },
    "light": {
        "dark": False,
        "bg": "#ffffff", "surface": "#f3f3f3", "surface_hover": "#e8e8e8", "surface_active": "#e4e6f1",
        "tabbar": "#f3f3f3", "tab": "#ececec", "tab_hover": "#e3e3e3", "tab_active": "#ffffff",
        "fg": "#3b3b3b", "fg_muted": "#6e6e6e", "fg_strong": "#000000",
        "accent": "#005fb8", "accent_hover": "#0258a8", "accent_fg": "#ffffff",
        "border": "#e0e0e0", "row": "#f8f8f8", "row_hover": "#efefef", "row_flash": "#cce4f7",
        "input_bg": "#ffffff", "button": "#e5e5e5", "button_hover": "#d6d6d6", "button_fg": "#1f1f1f",
        "danger": "#c42b1c", "warning": "#bf8803", "info": "#0070c1", "selection": "#add6ff",
        "statusbar": "#005fb8", "statusbar_hover": "#1a6fc2",
        "statusbar_paused": "#b35c00", "statusbar_paused_hover": "#c96d0c", "statusbar_fg": "#ffffff",
        "statusbar_admin": "#b3261e", "statusbar_admin_hover": "#c9372c",
        "badge_bg": "#005fb8", "badge_fg": "#ffffff", "badge_off": "#b4b4b4",
        "scroll_thumb": "#c8c8c8", "scroll_thumb_hover": "#a8a8a8", "tooltip_bg": "#f8f8f8",
    },
}

ICON_FONTS = ("Segoe Fluent Icons", "Segoe MDL2 Assets")
MONO_FONTS = ("Cascadia Mono", "Cascadia Code", "Consolas", "Courier New")

# name -> (icon-font glyph, plain-text fallback)
GLYPHS = {
    "menu": ("", "≡"),
    "collapse": ("", "«"),
    "history": ("", "↺"),
    "pin": ("", "•"),
    "unpin": ("", "○"),
    "settings": ("", "⚙"),
    "preferences": ("", "≣"),
    "output": ("", ">"),
    "chevron-down": ("", "▾"),
    "chevron-right": ("", "▸"),
    "close": ("", "×"),
    "copy": ("", "⧉"),
    "delete": ("", "✕"),
    "search": ("", "⌕"),
    "pause": ("", "‖"),
    "play": ("", "▶"),
    "clear": ("", "⊘"),
    "folder": ("", "▤"),
    "popout": ("", "↗"),
    "shield": ("", "⛨"),
}


class Theme:
    def __init__(self, root: tk.Tk, name: str, font_family: str, font_size: int):
        self.root = root
        families = set(tkfont.families(root))
        self.families = sorted(f for f in families if not f.startswith("@"))
        self.icon_family = next((f for f in ICON_FONTS if f in families), None)
        self.mono_family = next((f for f in MONO_FONTS if f in families), "Courier New")
        self.scale = max(1.0, root.winfo_fpixels("1i") / 96.0)
        self.fonts: dict[str, tkfont.Font] = {}
        self.font_size = font_size
        self.set_fonts(font_family, font_size)
        self.set_palette(name)

    # ---------- helpers ----------
    def px(self, value: float) -> int:
        """Scale a 96-DPI pixel value for the current display."""
        return int(round(value * self.scale))

    def glyph(self, name: str) -> str:
        icon, fallback = GLYPHS[name]
        return icon if self.icon_family else fallback

    # ---------- fonts ----------
    def set_fonts(self, family: str, size: int) -> None:
        if family not in self.families:
            family = "Segoe UI" if "Segoe UI" in self.families else tkfont.nametofont("TkDefaultFont").actual("family")
        size = max(6, min(int(size), 40))
        self.font_size = size
        icon_family = self.icon_family or family
        specs = {
            "base": (family, size, "normal"),
            "small": (family, max(6, size - 1), "normal"),
            "caption": (family, max(6, size - 2), "bold"),
            "bold": (family, size, "bold"),
            "heading": (family, size + 2, "bold"),
            "title": (family, size + 8, "bold"),
            "mono": (self.mono_family, size, "normal"),
            "icon": (icon_family, size + 3, "normal"),
            "icon_small": (icon_family, size, "normal"),
            "icon_hero": (icon_family, size * 4, "normal"),
        }
        for key, (fam, sz, weight) in specs.items():
            if key in self.fonts:
                self.fonts[key].configure(family=fam, size=sz, weight=weight)
            else:
                self.fonts[key] = tkfont.Font(self.root, name=f"cm_{key}", family=fam,
                                              size=sz, weight=weight, exists=False)
        # Default for widgets created without an explicit font. (Not option_add("*Font"),
        # because that would also override the fonts set by the ttk styles.)
        for name in ("TkDefaultFont", "TkTextFont"):
            tkfont.nametofont(name).configure(family=family, size=size)

    # ---------- colors ----------
    def set_palette(self, name: str) -> None:
        self.name = name if name in PALETTES else "dark"
        self.p = PALETTES[self.name]
        self._apply_ttk_style()

    def _apply_ttk_style(self) -> None:
        p, f, px = self.p, self.fonts, self.px
        root = self.root
        root.configure(bg=p["bg"])
        s = ttk.Style(root)
        s.theme_use("clam")

        s.configure(".", background=p["bg"], foreground=p["fg"], font=f["base"],
                    bordercolor=p["border"], darkcolor=p["bg"], lightcolor=p["bg"],
                    troughcolor=p["bg"], focuscolor=p["accent"], fieldbackground=p["input_bg"],
                    selectbackground=p["selection"], selectforeground=p["fg_strong"],
                    insertcolor=p["fg"])
        s.configure("TFrame", background=p["bg"])
        s.configure("TLabel", background=p["bg"], foreground=p["fg"])
        s.configure("Muted.TLabel", foreground=p["fg_muted"])
        s.configure("Error.TLabel", foreground=p["danger"])
        s.configure("Bold.TLabel", font=f["bold"], foreground=p["fg_strong"])
        s.configure("Heading.TLabel", font=f["heading"], foreground=p["fg_strong"])
        s.configure("Title.TLabel", font=f["title"], foreground=p["fg_strong"])

        # Buttons
        s.configure("TButton", background=p["button"], foreground=p["button_fg"],
                    bordercolor=p["button"], lightcolor=p["button"], darkcolor=p["button"],
                    focuscolor=p["button"], padding=(px(12), px(4)), relief="flat")
        s.map("TButton",
              background=[("disabled", p["bg"]), ("pressed", p["border"]), ("active", p["button_hover"])],
              bordercolor=[("focus", p["accent"])],
              foreground=[("disabled", p["fg_muted"])])
        s.configure("Accent.TButton", background=p["accent"], foreground=p["accent_fg"],
                    bordercolor=p["accent"], lightcolor=p["accent"], darkcolor=p["accent"],
                    focuscolor=p["accent"])
        s.map("Accent.TButton",
              background=[("disabled", p["button"]), ("pressed", p["accent"]), ("active", p["accent_hover"])],
              bordercolor=[("disabled", p["button"])],
              lightcolor=[("disabled", p["button"])],
              darkcolor=[("disabled", p["button"])],
              foreground=[("disabled", p["fg_muted"])])

        # Check buttons
        s.configure("TCheckbutton", background=p["bg"], foreground=p["fg"], focuscolor=p["bg"],
                    indicatorbackground=p["input_bg"], indicatorforeground=p["accent_fg"],
                    upperbordercolor=p["fg_muted"], lowerbordercolor=p["fg_muted"],
                    indicatorsize=px(15), indicatormargin=(0, px(2), px(8), px(2)))
        s.map("TCheckbutton",
              background=[("active", p["bg"])],
              indicatorbackground=[("selected", p["accent"]), ("active", p["input_bg"])],
              upperbordercolor=[("selected", p["accent"])],
              lowerbordercolor=[("selected", p["accent"])])

        # Inputs
        for style in ("TEntry", "TSpinbox", "TCombobox"):
            s.configure(style, fieldbackground=p["input_bg"], foreground=p["fg"],
                        background=p["button"], bordercolor=p["border"],
                        lightcolor=p["input_bg"], darkcolor=p["input_bg"],
                        arrowcolor=p["fg"], insertcolor=p["fg"], padding=px(4))
            s.map(style,
                  bordercolor=[("focus", p["accent"])],
                  lightcolor=[("focus", p["accent"])],
                  background=[("active", p["button_hover"])])
        s.configure("TSpinbox", arrowsize=px(12))
        s.configure("TCombobox", arrowsize=px(14))
        s.map("TCombobox",
              fieldbackground=[("readonly", p["input_bg"])],
              foreground=[("readonly", p["fg"])],
              selectbackground=[("readonly", p["input_bg"])],
              selectforeground=[("readonly", p["fg"])],
              bordercolor=[("focus", p["accent"])],
              background=[("active", p["button_hover"]), ("readonly", p["button"])])
        root.option_add("*TCombobox*Listbox.background", p["input_bg"])
        root.option_add("*TCombobox*Listbox.foreground", p["fg"])
        root.option_add("*TCombobox*Listbox.selectBackground", p["accent"])
        root.option_add("*TCombobox*Listbox.selectForeground", p["accent_fg"])
        root.option_add("*TCombobox*Listbox.font", f["base"])

        # Slim, arrow-less scrollbar
        s.layout("Slim.Vertical.TScrollbar", [
            ("Vertical.Scrollbar.trough", {"sticky": "ns", "children": [
                ("Vertical.Scrollbar.thumb", {"expand": "1", "sticky": "nswe"})]})])
        s.configure("Slim.Vertical.TScrollbar", troughcolor=p["bg"], background=p["scroll_thumb"],
                    bordercolor=p["bg"], lightcolor=p["scroll_thumb"], darkcolor=p["scroll_thumb"],
                    arrowsize=px(10), width=px(10), gripcount=0, relief="flat")
        s.map("Slim.Vertical.TScrollbar",
              background=[("pressed", p["scroll_thumb_hover"]), ("active", p["scroll_thumb_hover"])],
              lightcolor=[("pressed", p["scroll_thumb_hover"]), ("active", p["scroll_thumb_hover"])],
              darkcolor=[("pressed", p["scroll_thumb_hover"]), ("active", p["scroll_thumb_hover"])])

    def apply_titlebar(self, window: tk.Misc | None = None) -> None:
        """Match a window's Windows title bar to the theme (Windows 10 1809+ / 11)."""
        if sys.platform != "win32":
            return
        try:
            import ctypes
            hwnd = ctypes.windll.user32.GetParent((window or self.root).winfo_id())
            value = ctypes.c_int(1 if self.p["dark"] else 0)
            for attr in (20, 19):  # DWMWA_USE_IMMERSIVE_DARK_MODE (19 on older builds)
                if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        hwnd, attr, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                    break
            # SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_FRAMECHANGED: repaint the frame now
            ctypes.windll.user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0004 | 0x0020)
        except Exception:
            pass
