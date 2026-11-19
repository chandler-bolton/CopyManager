"""Writing items to the Windows clipboard with their formatting (HTML / RTF), not just plain text.

Tk's own clipboard only handles plain text, so this talks to the Win32 clipboard directly.
"""
from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Any

ALL, TEXT_ONLY, FORMAT_ONLY = "all", "text", "format"

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def has_formatting(item: dict[str, Any]) -> bool:
    return bool(item.get("html") or item.get("rtf"))


def _api():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.restype = wintypes.HANDLE
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    return user32, kernel32


def _put(user32, kernel32, fmt: int, data: bytes) -> bool:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
    if not handle:
        return False
    ptr = kernel32.GlobalLock(handle)
    ctypes.memmove(ptr, data, len(data))
    kernel32.GlobalUnlock(handle)
    if not user32.SetClipboardData(fmt, handle):
        kernel32.GlobalFree(handle)  # still ours if the clipboard didn't take it
        return False
    return True


def copy_item(item: dict[str, Any], mode: str = ALL, owner_hwnd: int | None = None) -> None:
    """Put an item on the clipboard.

    ALL          plain text + any HTML/RTF it was copied with (pastes exactly like the original copy)
    TEXT_ONLY    plain text only
    FORMAT_ONLY  HTML/RTF only, no plain-text fallback (pastes only into apps that take formatting)

    Raises OSError if the clipboard can't be opened or written.
    """
    text = item.get("text") or ""
    html = item.get("html") if mode != TEXT_ONLY else None
    rtf = item.get("rtf") if mode != TEXT_ONLY else None
    if mode == FORMAT_ONLY and not (html or rtf):
        raise ValueError("This item has no formatting to copy.")
    user32, kernel32 = _api()
    for _ in range(40):  # another app (or our monitor) may have it open for a moment
        if user32.OpenClipboard(owner_hwnd):
            break
        time.sleep(0.025)
    else:
        raise OSError("The clipboard is in use by another app. Try again.")
    try:
        user32.EmptyClipboard()
        ok = True
        if mode != FORMAT_ONLY:
            ok &= _put(user32, kernel32, CF_UNICODETEXT, (text + "\0").encode("utf-16-le"))
        if html:
            ok &= _put(user32, kernel32, user32.RegisterClipboardFormatW("HTML Format"),
                       html.encode("utf-8") + b"\0")
        if rtf:
            ok &= _put(user32, kernel32, user32.RegisterClipboardFormatW("Rich Text Format"),
                       rtf.encode("utf-8") + b"\0")
        if not ok:
            raise OSError("Windows refused part of the clipboard data.")
    finally:
        user32.CloseClipboard()


def is_supported() -> bool:
    return os.name == "nt"
