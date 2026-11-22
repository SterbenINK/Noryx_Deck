"""Буфер обмена Windows (текст) для общего буфера ПК ↔ телефон. Пара — lx_clip.py.
seq() — номер изменения (дёшево, для опроса), get() — текст или None, set(text).
Что менеджеры паролей помечают «не показывать в истории» (форматы ExcludeClipboardContentFromMonitorProcessing,
CanIncludeInClipboardHistory = 0) — не берём."""
import ctypes
import sys
import time

IS_WIN = sys.platform == "win32"
CF_UNICODETEXT = 13
_fmt = {}


def _u():
    from ctypes import wintypes as W
    u = ctypes.WinDLL("user32", use_last_error=True)
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    u.GetClipboardSequenceNumber.restype = W.DWORD
    u.OpenClipboard.argtypes = [W.HWND]
    u.GetClipboardData.restype = W.HANDLE
    u.GetClipboardData.argtypes = [W.UINT]
    u.SetClipboardData.restype = W.HANDLE
    u.SetClipboardData.argtypes = [W.UINT, W.HANDLE]
    u.IsClipboardFormatAvailable.argtypes = [W.UINT]
    u.RegisterClipboardFormatW.restype = W.UINT
    u.RegisterClipboardFormatW.argtypes = [W.LPCWSTR]
    k.GlobalLock.restype = ctypes.c_void_p
    k.GlobalLock.argtypes = [W.HANDLE]
    k.GlobalUnlock.argtypes = [W.HANDLE]
    k.GlobalAlloc.restype = W.HANDLE
    k.GlobalAlloc.argtypes = [W.UINT, ctypes.c_size_t]
    k.GlobalFree.argtypes = [W.HANDLE]
    return u, k


def _open(u):
    for _ in range(10):                         # буфер может быть занят другой программой на миг
        if u.OpenClipboard(None):
            return True
        time.sleep(0.02)
    return False


def seq():
    if not IS_WIN:
        return 0
    return ctypes.WinDLL("user32").GetClipboardSequenceNumber()


def private():
    """Менеджер паролей пометил содержимое как секретное."""
    u, _ = _u()
    for name in ("ExcludeClipboardContentFromMonitorProcessing", "CanIncludeInClipboardHistory"):
        if name not in _fmt:
            _fmt[name] = u.RegisterClipboardFormatW(name)
        if _fmt[name] and u.IsClipboardFormatAvailable(_fmt[name]):
            return True
    return False


def get():
    if not IS_WIN:
        return None
    u, k = _u()
    if not u.IsClipboardFormatAvailable(CF_UNICODETEXT) or private():
        return None
    if not _open(u):
        return None
    try:
        h = u.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = k.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.wstring_at(p)
        finally:
            k.GlobalUnlock(h)
    finally:
        u.CloseClipboard()


def set(text):
    if not IS_WIN:
        return False
    u, k = _u()
    data = ctypes.create_unicode_buffer(str(text))
    size = ctypes.sizeof(data)
    h = k.GlobalAlloc(0x0002, size)             # GMEM_MOVEABLE
    if not h:
        return False
    p = k.GlobalLock(h)
    ctypes.memmove(p, data, size)
    k.GlobalUnlock(h)
    if not _open(u):
        k.GlobalFree(h)
        return False
    try:
        u.EmptyClipboard()
        if not u.SetClipboardData(CF_UNICODETEXT, h):
            k.GlobalFree(h)
            return False
        return True                             # память теперь принадлежит буферу обмена
    finally:
        u.CloseClipboard()
