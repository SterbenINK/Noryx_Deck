"""DDC/CI — настройки монитора по кабелю (яркость, контраст, цвета…) для плагина «Монитор».

Windows — dxva2.dll напрямую через ctypes (без библиотек): GetPhysicalMonitorsFromHMONITOR → хендл,
GetVCPFeatureAndVCPFeatureReply — прочитать (текущее и максимум), SetVCPFeature — записать.
Linux — утилита ddcutil (sudo dnf install ddcutil; доступ к /dev/i2c-* — группа i2c).

Все вызовы медленные (50–100 мс и больше) — звать только из рабочего потока плагина, не из потока дека."""
import re
import shutil
import subprocess
import sys


class DDCError(Exception):
    pass


def model_of(caps):
    """Строка возможностей монитора → модель: «(prot(monitor)type(LCD)model(C27F390)…» → «C27F390»."""
    m = re.search(r"model\(([^)]*)\)", caps or "")
    return m.group(1).strip() if m else ""


def backend():
    if sys.platform == "win32":
        try:
            return WinDDC()
        except (OSError, AttributeError) as e:          # нет dxva2 / нужных функций (Wine, урезанная Windows)
            raise DDCError(f"dxva2.dll без DDC/CI: {e}") from e
    if sys.platform.startswith("linux"):
        return LinuxDDC()
    raise DDCError("DDC/CI: только Windows и Linux")


# ---------------- Windows ----------------
class WinDDC:
    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self.ct, self.wt = ctypes, wintypes
        self.user32 = ctypes.WinDLL("user32")
        self.dxva2 = ctypes.WinDLL("dxva2")

        class PHYS(ctypes.Structure):
            _fields_ = [("h", wintypes.HANDLE), ("desc", wintypes.WCHAR * 128)]
        self.PHYS = PHYS
        # типы аргументов: хендлы — во всю ширину указателя (без них ctypes обрежет до 32 бит)
        H, D, PD = wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
        dx = self.dxva2
        dx.GetNumberOfPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, PD]
        dx.GetPhysicalMonitorsFromHMONITOR.argtypes = [wintypes.HMONITOR, D, ctypes.POINTER(PHYS)]
        dx.DestroyPhysicalMonitors.argtypes = [D, ctypes.POINTER(PHYS)]
        dx.GetCapabilitiesStringLength.argtypes = [H, PD]
        dx.CapabilitiesRequestAndCapabilitiesReply.argtypes = [H, ctypes.c_char_p, D]
        dx.GetVCPFeatureAndVCPFeatureReply.argtypes = [H, ctypes.c_ubyte, ctypes.c_void_p, PD, PD]
        dx.SetVCPFeature.argtypes = [H, ctypes.c_ubyte, D]
        for f in ("GetNumberOfPhysicalMonitorsFromHMONITOR", "GetPhysicalMonitorsFromHMONITOR", "DestroyPhysicalMonitors",
                  "GetCapabilitiesStringLength", "CapabilitiesRequestAndCapabilitiesReply", "GetVCPFeatureAndVCPFeatureReply",
                  "SetVCPFeature"):
            getattr(dx, f).restype = wintypes.BOOL
        self.handles = []           # [(PHYS-массив, n)] — закрыть в close()
        self.mons = {}              # key → HANDLE

    def monitors(self):
        """[{"key", "name"}] — физические мониторы, которые отвечают по DDC/CI."""
        ct, wt = self.ct, self.wt
        self.close()
        hmons = []
        Proc = ct.WINFUNCTYPE(wt.BOOL, wt.HMONITOR, wt.HDC, ct.POINTER(wt.RECT), wt.LPARAM)
        cb = Proc(lambda h, dc, r, lp: hmons.append(h) or True)
        self.user32.EnumDisplayMonitors(None, None, cb, 0)
        out = []
        for hm in hmons:
            n = wt.DWORD()
            if not self.dxva2.GetNumberOfPhysicalMonitorsFromHMONITOR(hm, ct.byref(n)) or not n.value:
                continue
            arr = (self.PHYS * n.value)()
            if not self.dxva2.GetPhysicalMonitorsFromHMONITOR(hm, n.value, arr):
                continue
            self.handles.append((arr, n.value))
            for p in arr:
                name = self.caps_model(p.h) or p.desc or "Monitor"
                key = f"{len(out) + 1}:{name}"
                self.mons[key] = p.h or 0
                out.append({"key": key, "name": name})
        return out

    def caps_model(self, h):
        ct, wt = self.ct, self.wt
        try:
            ln = wt.DWORD()
            if not self.dxva2.GetCapabilitiesStringLength(h, ct.byref(ln)) or not ln.value:
                return ""
            buf = ct.create_string_buffer(ln.value)
            if not self.dxva2.CapabilitiesRequestAndCapabilitiesReply(h, buf, ln.value):
                return ""
            return model_of(buf.value.decode("ascii", "replace"))
        except OSError:
            return ""

    def get(self, key, code):
        """(текущее, максимум) или None — монитор этот код не знает / не ответил."""
        ct, wt = self.ct, self.wt
        if key not in self.mons:                     # хендл 0 — настоящий (первый монитор), ctypes даёт его как None
            raise DDCError(f"нет монитора {key}")
        h = self.mons[key] or 0
        cur, mx = wt.DWORD(), wt.DWORD()
        if not self.dxva2.GetVCPFeatureAndVCPFeatureReply(h, code, None, ct.byref(cur), ct.byref(mx)):
            return None
        return cur.value, mx.value

    def set(self, key, code, value):
        if key not in self.mons:                     # хендл 0 — настоящий (первый монитор), ctypes даёт его как None
            raise DDCError(f"нет монитора {key}")
        h = self.mons[key] or 0
        return bool(self.dxva2.SetVCPFeature(h, code, int(value)))

    def close(self):
        for arr, n in self.handles:
            try:
                self.dxva2.DestroyPhysicalMonitors(n, arr)
            except OSError:
                pass
        self.handles, self.mons = [], {}


# ---------------- Linux (ddcutil) ----------------
class LinuxDDC:
    def __init__(self, run=None):
        self.run = run or self._run
        self.mons = {}              # key → номер дисплея ddcutil
        if run is None and not shutil.which("ddcutil"):
            raise DDCError("нет ddcutil: sudo dnf install ddcutil (или apt), доступ к /dev/i2c-* — группа i2c")

    @staticmethod
    def _run(args, timeout=10):
        r = subprocess.run(["ddcutil", *args], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout + r.stderr

    def monitors(self):
        code, out = self.run(["detect", "--terse"], 30)
        self.mons, res = {}, []
        disp = None
        for line in out.splitlines():
            m = re.match(r"\s*Display\s+(\d+)", line)
            if m:
                disp = int(m.group(1))
                continue
            m = re.match(r"\s*Monitor:\s*(.*)", line)
            if m and disp is not None:
                parts = m.group(1).split(":")
                name = (parts[1] if len(parts) > 1 and parts[1] else parts[0]).strip() or f"Display {disp}"
                key = f"{len(res) + 1}:{name}"
                self.mons[key] = disp
                res.append({"key": key, "name": name})
                disp = None
        return res

    def get(self, key, code):
        d = self.mons.get(key)
        if d is None:
            raise DDCError(f"нет монитора {key}")
        rc, out = self.run(["-d", str(d), "getvcp", f"{code:02x}", "--brief"])
        m = re.search(r"VCP\s+[0-9A-Fa-f]+\s+C\s+(\d+)\s+(\d+)", out)
        if rc != 0 or not m:
            return None
        return int(m.group(1)), int(m.group(2))

    def set(self, key, code, value):
        d = self.mons.get(key)
        if d is None:
            raise DDCError(f"нет монитора {key}")
        rc, _ = self.run(["-d", str(d), "setvcp", f"{code:02x}", str(int(value))])
        return rc == 0

    def close(self):
        pass
