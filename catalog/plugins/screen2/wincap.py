"""Windows для «Другого экрана»: мониторы, снимок одного монитора (GDI BitBlt + курсор), касания (InjectTouchInput)
и мышь (SendInput) — всё через ctypes, без своих библиотек. Координаты — настоящие пиксели: поток, который зовёт эти
функции, сначала делает dpi_thread() (иначе на мониторе с масштабом 125 % Windows подсовывает «уменьшенные»).
"""
import ctypes
import ctypes.wintypes as W

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("rcMonitor", W.RECT), ("rcWork", W.RECT), ("dwFlags", W.DWORD),
                ("szDevice", W.WCHAR * 32)]


class DISPLAY_DEVICEW(ctypes.Structure):
    _fields_ = [("cb", W.DWORD), ("DeviceName", W.WCHAR * 32), ("DeviceString", W.WCHAR * 128), ("StateFlags", W.DWORD),
                ("DeviceID", W.WCHAR * 128), ("DeviceKey", W.WCHAR * 128)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", W.DWORD), ("biWidth", W.LONG), ("biHeight", W.LONG), ("biPlanes", W.WORD),
                ("biBitCount", W.WORD), ("biCompression", W.DWORD), ("biSizeImage", W.DWORD),
                ("biXPelsPerMeter", W.LONG), ("biYPelsPerMeter", W.LONG), ("biClrUsed", W.DWORD), ("biClrImportant", W.DWORD)]


class CURSORINFO(ctypes.Structure):
    _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hCursor", W.HANDLE), ("ptScreenPos", W.POINT)]


class ICONINFO(ctypes.Structure):
    _fields_ = [("fIcon", W.BOOL), ("xHotspot", W.DWORD), ("yHotspot", W.DWORD), ("hbmMask", W.HBITMAP),
                ("hbmColor", W.HBITMAP)]


class POINTER_INFO(ctypes.Structure):
    _fields_ = [("pointerType", ctypes.c_uint32), ("pointerId", ctypes.c_uint32), ("frameId", ctypes.c_uint32),
                ("pointerFlags", ctypes.c_uint32), ("sourceDevice", W.HANDLE), ("hwndTarget", W.HWND),
                ("ptPixelLocation", W.POINT), ("ptHimetricLocation", W.POINT), ("ptPixelLocationRaw", W.POINT),
                ("ptHimetricLocationRaw", W.POINT), ("dwTime", W.DWORD), ("historyCount", ctypes.c_uint32),
                ("InputData", ctypes.c_int32), ("dwKeyStates", W.DWORD), ("PerformanceCount", ctypes.c_uint64),
                ("ButtonChangeType", ctypes.c_int)]


class POINTER_TOUCH_INFO(ctypes.Structure):
    _fields_ = [("pointerInfo", POINTER_INFO), ("touchFlags", ctypes.c_uint32), ("touchMask", ctypes.c_uint32),
                ("rcContact", W.RECT), ("rcContactRaw", W.RECT), ("orientation", ctypes.c_uint32), ("pressure", ctypes.c_uint32)]


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD), ("time", W.DWORD),
                ("dwExtraInfo", ctypes.c_size_t)]


class _INPUTU(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("_pad", ctypes.c_byte * 32)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", W.DWORD), ("u", _INPUTU)]


MONITORENUMPROC = ctypes.WINFUNCTYPE(W.BOOL, W.HMONITOR, W.HDC, ctypes.POINTER(W.RECT), W.LPARAM)
user32.EnumDisplayMonitors.argtypes = [W.HDC, ctypes.POINTER(W.RECT), MONITORENUMPROC, W.LPARAM]
user32.GetMonitorInfoW.argtypes = [W.HMONITOR, ctypes.POINTER(MONITORINFOEXW)]
user32.EnumDisplayDevicesW.argtypes = [W.LPCWSTR, W.DWORD, ctypes.POINTER(DISPLAY_DEVICEW), W.DWORD]
user32.GetDC.argtypes = [W.HWND]
user32.GetDC.restype = W.HDC
user32.ReleaseDC.argtypes = [W.HWND, W.HDC]
user32.GetCursorInfo.argtypes = [ctypes.POINTER(CURSORINFO)]
user32.GetIconInfo.argtypes = [W.HANDLE, ctypes.POINTER(ICONINFO)]
user32.DrawIconEx.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, W.HANDLE, ctypes.c_int, ctypes.c_int, W.UINT, W.HBRUSH, W.UINT]
user32.GetSystemMetrics.argtypes = [ctypes.c_int]
user32.SendInput.argtypes = [W.UINT, ctypes.POINTER(INPUT), ctypes.c_int]
gdi32.CreateCompatibleDC.argtypes = [W.HDC]
gdi32.CreateCompatibleDC.restype = W.HDC
gdi32.CreateDIBSection.argtypes = [W.HDC, ctypes.POINTER(BITMAPINFOHEADER), W.UINT, ctypes.POINTER(ctypes.c_void_p), W.HANDLE, W.DWORD]
gdi32.CreateDIBSection.restype = W.HBITMAP
gdi32.SelectObject.argtypes = [W.HDC, W.HGDIOBJ]
gdi32.SelectObject.restype = W.HGDIOBJ
gdi32.BitBlt.argtypes = [W.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.HDC, ctypes.c_int, ctypes.c_int, W.DWORD]
gdi32.DeleteObject.argtypes = [W.HGDIOBJ]
gdi32.DeleteDC.argtypes = [W.HDC]
gdi32.GdiFlush.argtypes = []

SRCCOPY = 0x00CC0020
CURSOR_SHOWING = 1
DI_NORMAL = 3
MONITORINFOF_PRIMARY = 1


def dpi_thread():
    """Этот поток — в настоящих пикселях (per-monitor DPI v2)."""
    try:
        f = user32.SetThreadDpiAwarenessContext
        f.argtypes, f.restype = [ctypes.c_void_p], ctypes.c_void_p
        f(ctypes.c_void_p(-4))
    except (AttributeError, OSError):
        pass


def adapters():
    """\\\\.\\DISPLAY1 → название видеоадаптера (у виртуального монитора — «Virtual Display Driver» и т. п.)."""
    out, i = {}, 0
    while i < 32:
        dd = DISPLAY_DEVICEW()
        dd.cb = ctypes.sizeof(dd)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        out[dd.DeviceName] = dd.DeviceString
        i += 1
    return out


def monitors():
    """Мониторы рабочего стола: [{dev, name, adapter, x, y, w, h, primary}] в настоящих пикселях."""
    dpi_thread()
    out, ad = [], adapters()

    def cb(h, hdc, rc, lp):
        mi = MONITORINFOEXW()
        mi.cbSize = ctypes.sizeof(mi)
        if user32.GetMonitorInfoW(h, ctypes.byref(mi)):
            mon = DISPLAY_DEVICEW()
            mon.cb = ctypes.sizeof(mon)
            name = mon.DeviceString if user32.EnumDisplayDevicesW(mi.szDevice, 0, ctypes.byref(mon), 0) else ""
            r = mi.rcMonitor
            out.append({"dev": mi.szDevice, "name": name, "adapter": ad.get(mi.szDevice, ""), "x": r.left, "y": r.top,
                        "w": r.right - r.left, "h": r.bottom - r.top, "primary": bool(mi.dwFlags & MONITORINFOF_PRIMARY)})
        return True
    user32.EnumDisplayMonitors(None, None, MONITORENUMPROC(cb), 0)
    return out


class Grabber:
    """Снимок прямоугольника рабочего стола в свой DIB (BGRX сверху вниз) + курсор поверх."""

    def __init__(self, x, y, w, h):
        dpi_thread()
        self.x, self.y, self.w, self.h = x, y, w, h
        self.scr = user32.GetDC(None)
        self.mem = gdi32.CreateCompatibleDC(self.scr)
        bi = BITMAPINFOHEADER()
        bi.biSize, bi.biWidth, bi.biHeight, bi.biPlanes, bi.biBitCount = ctypes.sizeof(bi), w, -h, 1, 32
        self.bits = ctypes.c_void_p()
        self.bmp = gdi32.CreateDIBSection(self.scr, ctypes.byref(bi), 0, ctypes.byref(self.bits), None, 0)
        if not self.bmp or not self.bits.value:
            self.close()
            raise OSError("CreateDIBSection: %d" % ctypes.get_last_error())
        self.old = gdi32.SelectObject(self.mem, self.bmp)

    def grab(self, cursor=True):
        if not gdi32.BitBlt(self.mem, 0, 0, self.w, self.h, self.scr, self.x, self.y, SRCCOPY):
            raise OSError("BitBlt: %d" % ctypes.get_last_error())
        if cursor:
            self._cursor()
        gdi32.GdiFlush()
        return ctypes.string_at(self.bits.value, self.w * self.h * 4)

    def _cursor(self):
        ci = CURSORINFO()
        ci.cbSize = ctypes.sizeof(ci)
        if not user32.GetCursorInfo(ctypes.byref(ci)) or not (ci.flags & CURSOR_SHOWING) or not ci.hCursor:
            return
        px, py = ci.ptScreenPos.x - self.x, ci.ptScreenPos.y - self.y
        if not (-64 < px < self.w and -64 < py < self.h):
            return
        ii = ICONINFO()
        hx = hy = 0
        if user32.GetIconInfo(ci.hCursor, ctypes.byref(ii)):
            hx, hy = ii.xHotspot, ii.yHotspot
            for b in (ii.hbmMask, ii.hbmColor):
                if b:
                    gdi32.DeleteObject(b)
        user32.DrawIconEx(self.mem, px - hx, py - hy, ci.hCursor, 0, 0, 0, None, DI_NORMAL)

    def close(self):
        if getattr(self, "mem", None):
            if getattr(self, "old", None):
                gdi32.SelectObject(self.mem, self.old)
            gdi32.DeleteDC(self.mem)
            self.mem = None
        if getattr(self, "bmp", None):
            gdi32.DeleteObject(self.bmp)
            self.bmp = None
        if getattr(self, "scr", None):
            user32.ReleaseDC(None, self.scr)
            self.scr = None


# ---------- касания: настоящий сенсорный ввод Windows (прокрутка пальцем, удержание — правый клик) ----------
PT_TOUCH = 2
PF_INRANGE, PF_INCONTACT, PF_CANCELED = 0x2, 0x4, 0x8000
PF_DOWN, PF_UPDATE, PF_UP = 0x10000, 0x20000, 0x40000
TOUCH_MASK_CONTACTAREA, TOUCH_MASK_PRESSURE = 0x1, 0x4
TOUCH_FEEDBACK_DEFAULT = 1


class Touch:
    def __init__(self):
        dpi_thread()
        f = user32.InitializeTouchInjection
        f.argtypes, f.restype = [ctypes.c_uint32, W.DWORD], W.BOOL
        if not f(1, TOUCH_FEEDBACK_DEFAULT):
            raise OSError("InitializeTouchInjection: %d" % ctypes.get_last_error())
        self.inj = user32.InjectTouchInput
        self.inj.argtypes, self.inj.restype = [ctypes.c_uint32, ctypes.POINTER(POINTER_TOUCH_INFO)], W.BOOL
        self.down = False

    def send(self, kind, x, y):
        t = POINTER_TOUCH_INFO()
        p = t.pointerInfo
        p.pointerType, p.pointerId = PT_TOUCH, 0
        p.ptPixelLocation.x, p.ptPixelLocation.y = int(x), int(y)
        if kind == "down":
            p.pointerFlags = PF_DOWN | PF_INRANGE | PF_INCONTACT
        elif kind == "move":
            p.pointerFlags = PF_UPDATE | PF_INRANGE | PF_INCONTACT
        elif kind == "up":
            p.pointerFlags = PF_UP
        else:                                   # cancel — палец «не считается» (второй палец: щипок на деке)
            p.pointerFlags = PF_UP | PF_CANCELED
        t.touchMask = TOUCH_MASK_CONTACTAREA | TOUCH_MASK_PRESSURE
        t.rcContact = W.RECT(int(x) - 2, int(y) - 2, int(x) + 2, int(y) + 2)
        t.pressure = 32000
        if kind in ("up", "cancel") and not self.down:
            return True
        ok = bool(self.inj(1, ctypes.byref(t)))
        self.down = kind in ("down", "move") and (ok or self.down)
        return ok


# ---------- мышь (если сенсорный ввод не доступен или выбран в настройках) ----------
INPUT_MOUSE = 0
ME_MOVE, ME_LDOWN, ME_LUP, ME_WHEEL, ME_VIRTUALDESK, ME_ABSOLUTE = 0x1, 0x2, 0x4, 0x800, 0x4000, 0x8000


class Mouse:
    def __init__(self):
        dpi_thread()

    def send(self, kind, x, y, wheel=0):
        vx, vy = user32.GetSystemMetrics(76), user32.GetSystemMetrics(77)      # SM_XVIRTUALSCREEN / Y
        vw, vh = max(1, user32.GetSystemMetrics(78) - 1), max(1, user32.GetSystemMetrics(79) - 1)
        i = INPUT()
        i.type = INPUT_MOUSE
        i.u.mi.dx, i.u.mi.dy = int((x - vx) * 65535 / vw), int((y - vy) * 65535 / vh)
        fl = ME_MOVE | ME_ABSOLUTE | ME_VIRTUALDESK
        fl |= {"down": ME_LDOWN, "up": ME_LUP, "cancel": ME_LUP}.get(kind, 0)
        if wheel:
            fl |= ME_WHEEL
            i.u.mi.mouseData = ctypes.c_uint32(int(wheel)).value
        i.u.mi.dwFlags = fl
        return user32.SendInput(1, ctypes.byref(i), ctypes.sizeof(INPUT)) == 1
