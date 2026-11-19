"""Reusable widgets: hover helper, icon buttons, tooltips, scroll frame, search box."""
from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .theme import Theme


# ---------------------------------------------------------------- helpers
def is_descendant(widget, ancestor) -> bool:
    while widget is not None:
        if widget is ancestor:
            return True
        widget = getattr(widget, "master", None)
    return False


def widget_under_pointer(widget: tk.Misc):
    try:
        x, y = widget.winfo_pointerxy()
        return widget.winfo_containing(x, y)
    except (KeyError, tk.TclError):  # e.g. pointer over a Tcl-only widget like a combobox popdown
        return None


def bind_hover(container: tk.Widget, on_enter: Callable[[], None], on_leave: Callable[[], None]) -> None:
    """Hover for a whole widget tree: moving between children doesn't count as leaving.
    Call after the container's children have been created."""
    state = {"inside": False}

    def enter(_e=None):
        if not state["inside"]:
            state["inside"] = True
            on_enter()

    def check():
        if not container.winfo_exists():
            return
        w = widget_under_pointer(container)
        if (w is None or not is_descendant(w, container)) and state["inside"]:
            state["inside"] = False
            on_leave()

    def leave(_e=None):
        try:
            container.after(15, check)
        except tk.TclError:
            pass

    def bind_tree(w):
        w.bind("<Enter>", enter, add="+")
        w.bind("<Leave>", leave, add="+")
        for child in w.winfo_children():
            bind_tree(child)

    bind_tree(container)


def install_mousewheel(root: tk.Tk) -> None:
    """Route mouse-wheel events to the ScrollFrame under the pointer."""
    def on_wheel(event):
        steps = -int(event.delta / 120) or (-1 if event.delta > 0 else 1)
        w = widget_under_pointer(root)
        while w is not None:
            if isinstance(w, (tk.Text, tk.Listbox)):
                return None  # these scroll themselves
            if isinstance(w, ScrollFrame):
                w.scroll_units(steps * 3)
                return None
            w = w.master
        return None

    root.bind_all("<MouseWheel>", on_wheel, add="+")
    # Don't let the wheel change combobox/spinbox values when you're just scrolling the page.
    for cls in ("TCombobox", "TSpinbox"):
        root.bind_class(cls, "<MouseWheel>", lambda e: (on_wheel(e), "break")[1])


# ---------------------------------------------------------------- tooltip
class Tooltip:
    def __init__(self, widget: tk.Widget, theme: "Theme", text: str | Callable[[], str], delay: int = 450):
        self.widget, self.theme, self.text, self.delay = widget, theme, text, delay
        self._job: str | None = None
        self._tip: tk.Toplevel | None = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._hide()
        self._job = self.widget.after(self.delay, self._show)

    def _show(self):
        self._job = None
        text = self.text() if callable(self.text) else self.text
        if not text or not self.widget.winfo_exists():
            return
        p = self.theme.p
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.attributes("-topmost", True)
        border = tk.Frame(tip, bg=p["border"], padx=1, pady=1)
        border.pack()
        tk.Label(border, text=text, bg=p["tooltip_bg"], fg=p["fg"], font=self.theme.fonts["small"],
                 padx=self.theme.px(8), pady=self.theme.px(4), justify="left").pack()
        x, y = self.widget.winfo_pointerxy()
        tip.wm_geometry(f"+{x + 12}+{y + 18}")
        self._tip = tip

    def _hide(self, _e=None):
        if self._job:
            try:
                self.widget.after_cancel(self._job)
            except tk.TclError:
                pass
            self._job = None
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None


# ---------------------------------------------------------------- icon button
class IconButton(tk.Label):
    """A flat, hoverable glyph button."""

    def __init__(self, master, theme: "Theme", icon: str, command: Callable[[], None] | None = None,
                 tooltip: str | Callable[[], str] | None = None, bg: str | None = None,
                 fg: str | None = None, hover_bg: str | None = None, hover_fg: str | None = None,
                 font=None, padx: int = 5, pady: int = 3):
        p = theme.p
        self._bg = bg or p["bg"]
        self._fg = fg or p["fg_muted"]
        self._hover_bg = hover_bg or p["button_hover"]
        self._hover_fg = hover_fg or p["fg_strong"]
        self._hover = False
        self.command = command
        super().__init__(master, text=theme.glyph(icon), font=font or theme.fonts["icon_small"],
                         bg=self._bg, fg=self._fg, padx=padx, pady=pady, cursor="hand2", bd=0)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<ButtonRelease-1>", self._on_click)
        if tooltip:
            Tooltip(self, theme, tooltip)

    def _on_enter(self, _e=None):
        self._hover = True
        self._paint()

    def _on_leave(self, _e=None):
        self._hover = False
        self._paint()

    def _on_click(self, _e=None):
        if self._hover and self.command:
            self.command()

    def set_base(self, bg: str | None = None, fg: str | None = None, hover_bg: str | None = None) -> None:
        if bg is not None:
            self._bg = bg
        if fg is not None:
            self._fg = fg
        if hover_bg is not None:
            self._hover_bg = hover_bg
        self._paint()

    def _paint(self):
        if self.winfo_exists():
            self.configure(bg=self._hover_bg if self._hover else self._bg,
                           fg=self._hover_fg if self._hover else self._fg)


# ---------------------------------------------------------------- scroll frame
class ScrollFrame(tk.Frame):
    """Vertically scrollable area. Put children in `.inner`."""

    def __init__(self, master, theme: "Theme", bg: str):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0, yscrollincrement=theme.px(20))
        self.vbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview,
                                  style="Slim.Vertical.TScrollbar")
        self.canvas.configure(yscrollcommand=self.vbar.set)
        self.vbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._update_region)
        self.canvas.bind("<Configure>", self._on_canvas_configure)

    def _on_canvas_configure(self, event):
        self.canvas.itemconfigure(self._window, width=event.width)
        self._update_region()

    def _update_region(self, _e=None):
        height = max(self.inner.winfo_reqheight(), self.canvas.winfo_height())
        self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(), height))

    def scroll_units(self, n: int) -> None:
        first, last = self.canvas.yview()
        if first <= 0 and last >= 1:
            return
        self.canvas.yview_scroll(n, "units")

    def scroll_to_top(self) -> None:
        self.canvas.yview_moveto(0)


# ---------------------------------------------------------------- search box
class SearchBox(tk.Frame):
    def __init__(self, master, theme: "Theme", placeholder: str, on_change: Callable[[str], None]):
        p = theme.p
        super().__init__(master, bg=p["input_bg"], highlightthickness=1,
                         highlightbackground=p["border"], highlightcolor=p["border"])
        self.theme, self.on_change = theme, on_change
        self._job: str | None = None
        tk.Label(self, text=theme.glyph("search"), font=theme.fonts["icon_small"],
                 bg=p["input_bg"], fg=p["fg_muted"]).pack(side="left", padx=(theme.px(8), theme.px(4)))
        self.var = tk.StringVar()
        self.entry = tk.Entry(self, textvariable=self.var, bg=p["input_bg"], fg=p["fg"],
                              insertbackground=p["fg"], relief="flat", bd=0, highlightthickness=0,
                              selectbackground=p["selection"], selectforeground=p["fg_strong"],
                              font=theme.fonts["base"])
        self.entry.pack(side="left", fill="x", expand=True, ipady=theme.px(5))
        self.clear_btn = IconButton(self, theme, "close", command=self.clear, tooltip="Clear filter (Esc)",
                                    bg=p["input_bg"], padx=theme.px(6))
        self.placeholder = tk.Label(self, text=placeholder, bg=p["input_bg"], fg=p["fg_muted"],
                                    font=theme.fonts["base"], cursor="xterm")
        self.placeholder.bind("<Button-1>", lambda _e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda _e: self.configure(highlightbackground=p["accent"],
                                                               highlightcolor=p["accent"]))
        self.entry.bind("<FocusOut>", lambda _e: self.configure(highlightbackground=p["border"],
                                                                highlightcolor=p["border"]))
        self.entry.bind("<Escape>", self._on_escape)
        self.var.trace_add("write", self._changed)
        self._sync()

    def clear(self) -> None:
        self.var.set("")

    def _on_escape(self, _e=None):
        if self.var.get():
            self.clear()
            return "break"  # first Esc clears the filter; the next one reaches the window
        return None

    def get(self) -> str:
        return self.var.get()

    def focus(self) -> None:
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    def _sync(self):
        if self.var.get():
            self.placeholder.place_forget()
            if not self.clear_btn.winfo_ismapped():
                self.clear_btn.pack(side="right", padx=(0, self.theme.px(4)))
        else:
            self.placeholder.place(in_=self.entry, x=1, rely=0.5, anchor="w")
            self.clear_btn.pack_forget()

    def _changed(self, *_):
        self._sync()
        if self._job:
            self.after_cancel(self._job)
        self._job = self.after(120, lambda: self.on_change(self.var.get()))
