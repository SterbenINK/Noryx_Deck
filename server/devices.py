"""Устройства ПК на Windows для окна «Устройства»: мониторы (разрешение, частота, основной), USB-устройства,
геймпады (XInput, заряд). Звук и телефоны дек знает и так. Вызывать в отдельном потоке: список USB
читается через PowerShell (Get-PnpDevice) — около секунды, поэтому он кэшируется на 15 с.
Пара — lx_devices.py (Linux)."""
import json
import logging
import subprocess
import sys
import time

log = logging.getLogger("devices")
IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000
_usb = (0.0, [])


def monitors():
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes as W

    class DD(ctypes.Structure):
        _fields_ = [("cb", W.DWORD), ("DeviceName", W.WCHAR * 32), ("DeviceString", W.WCHAR * 128),
                    ("StateFlags", W.DWORD), ("DeviceID", W.WCHAR * 128), ("DeviceKey", W.WCHAR * 128)]

    class DM(ctypes.Structure):          # DEVMODEW, вариант для дисплеев
        _fields_ = [("dmDeviceName", W.WCHAR * 32), ("dmSpecVersion", W.WORD), ("dmDriverVersion", W.WORD),
                    ("dmSize", W.WORD), ("dmDriverExtra", W.WORD), ("dmFields", W.DWORD),
                    ("dmPositionX", W.LONG), ("dmPositionY", W.LONG), ("dmDisplayOrientation", W.DWORD),
                    ("dmDisplayFixedOutput", W.DWORD), ("dmColor", W.SHORT), ("dmDuplex", W.SHORT),
                    ("dmYResolution", W.SHORT), ("dmTTOption", W.SHORT), ("dmCollate", W.SHORT),
                    ("dmFormName", W.WCHAR * 32), ("dmLogPixels", W.WORD), ("dmBitsPerPel", W.DWORD),
                    ("dmPelsWidth", W.DWORD), ("dmPelsHeight", W.DWORD), ("dmDisplayFlags", W.DWORD),
                    ("dmDisplayFrequency", W.DWORD), ("rest", W.DWORD * 8)]
    u = ctypes.WinDLL("user32")
    out, i = [], 0
    while True:
        ad = DD(cb=ctypes.sizeof(DD))
        if not u.EnumDisplayDevicesW(None, i, ctypes.byref(ad), 0):
            break
        i += 1
        if not ad.StateFlags & 1:                     # не подключён к рабочему столу
            continue
        mon = DD(cb=ctypes.sizeof(DD))
        name = ad.DeviceString
        if u.EnumDisplayDevicesW(ad.DeviceName, 0, ctypes.byref(mon), 0) and mon.DeviceString:
            name = mon.DeviceString
        dm = DM(dmSize=ctypes.sizeof(DM))
        if u.EnumDisplaySettingsW(ad.DeviceName, -1, ctypes.byref(dm)):    # ENUM_CURRENT_SETTINGS
            out.append({"name": name, "w": dm.dmPelsWidth, "h": dm.dmPelsHeight, "hz": dm.dmDisplayFrequency,
                        "x": dm.dmPositionX, "y": dm.dmPositionY, "primary": bool(ad.StateFlags & 4), "gpu": ad.DeviceString})
    return out


def usb():
    global _usb
    if not IS_WIN:
        return []
    if time.monotonic() - _usb[0] < 15:
        return _usb[1]
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
          "Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -like 'USB\\*' -and $_.FriendlyName } |"
          " Select-Object FriendlyName, Class, Status | ConvertTo-Json -Compress")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=20, creationflags=NO_WINDOW)
        data = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
        data = data if isinstance(data, list) else [data]
        seen, out = set(), []
        for d in data:
            n = d.get("FriendlyName") or ""
            if not n or n in seen or "Root Hub" in n or "Корневой" in n or "Generic USB Hub" in n:
                continue
            seen.add(n)
            out.append({"name": n, "kind": d.get("Class") or "", "ok": d.get("Status") == "OK"})
        _usb = (time.monotonic(), out)
    except Exception as e:
        log.debug("usb: %s", e)
    return _usb[1]


def pads():
    """Геймпады XInput (Xbox и совместимые): 4 слота, заряд — пусто / мало / средне / полный."""
    if not IS_WIN:
        return []
    import ctypes
    from ctypes import wintypes as W
    try:
        x = ctypes.WinDLL("xinput1_4")
    except OSError:
        try:
            x = ctypes.WinDLL("xinput9_1_0")
        except OSError:
            return []

    class ST(ctypes.Structure):
        _fields_ = [("packet", W.DWORD), ("buttons", W.WORD), ("lt", ctypes.c_ubyte), ("rt", ctypes.c_ubyte),
                    ("lx", W.SHORT), ("ly", W.SHORT), ("rx", W.SHORT), ("ry", W.SHORT)]

    class BAT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ubyte), ("level", ctypes.c_ubyte)]
    out = []
    for i in range(4):
        st = ST()
        if x.XInputGetState(i, ctypes.byref(st)) != 0:
            continue
        bat = None
        try:
            b = BAT()
            if x.XInputGetBatteryInformation(i, 0, ctypes.byref(b)) == 0 and b.type not in (0, 1):   # не «нет», не «проводной»
                bat = b.level                     # 0 пусто … 3 полный
        except AttributeError:
            pass
        out.append({"name": f"Xbox / XInput #{i + 1}", "battery": bat, "wired": bat is None})
    return out


def snapshot():
    out = {"monitors": [], "usb": [], "pads": []}
    for k, fn in (("monitors", monitors), ("usb", usb), ("pads", pads)):
        try:
            out[k] = fn()
        except Exception as e:
            log.debug("%s: %s", k, e)
    return out
