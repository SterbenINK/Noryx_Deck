"""Программы: поиск (меню «Пуск», рабочий стол, реестр, Steam), запуск, закрытие, иконки."""
import asyncio
import concurrent.futures
import glob
import hashlib
import io
import logging
import os
import re
import shlex
import subprocess
import sys
import threading
from pathlib import Path

import psutil

import i18n
from store import ROOT

log = logging.getLogger("apps")
logging.getLogger("icoextract").setLevel(logging.ERROR)

IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000 if IS_WIN else 0
DETACHED = 0x00000008 | 0x00000200 if IS_WIN else 0  # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
ICON_DIR = ROOT / "data" / "icons"
ICON_PX = 192  # на плитке иконка 64 px при масштабе ×2 — берём с запасом, чтобы была чёткой

# эти процессы дек никогда не закрывает
PROTECTED = {"explorer.exe", "spotify.exe", "python.exe", "pythonw.exe", "calc.exe",
             "calculatorapp.exe", "applicationframehost.exe", "svchost.exe", "dwm.exe",
             "youtube music.exe", "youtube-music.exe", "ytmdesktop.exe", "youtube music desktop app.exe"}
DOC_EXT = (".txt", ".chm", ".htm", ".html", ".url", ".pdf", ".rtf", ".md", ".log", ".ini", ".xml", ".json")
SKIP_WORDS = ("uninstall", "удал", "unins", "readme", "help", "справк", "website", "веб-сайт",
              "license", "лиценз", "documentation", "release notes", "what's new", "changelog",
              "redistributable", "runtime", "update", "setup", "repair")


# ---------- поток оболочки Windows (ShellExecute требует инициализированный COM) ----------

def _shell_init():
    if IS_WIN:
        import ctypes
        ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # COINIT_APARTMENTTHREADED


SHELL = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="shell", initializer=_shell_init)


def _startfile(path):
    if IS_WIN:
        SHELL.submit(os.startfile, path).result(timeout=30)
    else:
        log.info("[не Windows] startfile %s", path)


# ---------- ярлыки ----------

def _resolve_lnk(path):
    """Возвращает (target, args, icon_location). Чистый Python, без COM."""
    from lnk import read_lnk
    return read_lnk(str(path))


def proc_from(target, args=""):
    """Имя процесса, которое надо закрывать для этой программы."""
    if not target:
        return None
    base = os.path.basename(target).lower()
    if base == "update.exe" and "--processstart" in (args or "").lower():
        # Discord и другие Squirrel-приложения: Update.exe --processStart Discord.exe
        try:
            toks = shlex.split(args, posix=False)
            i = [t.lower() for t in toks].index("--processstart")
            return toks[i + 1].strip('"').lower()
        except Exception:
            return None
    if base.endswith(".exe"):
        return base
    return None


def _skip(name):
    low = name.lower()
    return any(w in low for w in SKIP_WORDS)


# ---------- источники программ ----------

def _lnk_dirs():
    out = []
    for env in ("ProgramData", "APPDATA"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    for env in ("USERPROFILE", "PUBLIC"):
        base = os.environ.get(env)
        if base:
            out.append(Path(base) / "Desktop")
            out.append(Path(base) / "OneDrive" / "Desktop")
            out.append(Path(base) / "OneDrive" / "Рабочий стол")
    return [p for p in out if p.exists()]


def _from_shortcuts(seen):
    for root in _lnk_dirs():
        for lnk in root.rglob("*.lnk"):
            name = lnk.stem
            if _skip(name) or name.lower() in seen:
                continue
            try:
                target, args, _icon = _resolve_lnk(lnk)
            except Exception:
                target, args = "", ""
            if target and not target.lower().endswith(".exe"):
                continue
            if target and IS_WIN and not os.path.exists(target):
                log.info("Пропускаю мёртвый ярлык «%s» → %s", name, target)
                continue
            seen[name.lower()] = {"label": name, "path": str(lnk), "target": target, "args": args,
                                  "proc": proc_from(target, args), "src": "ярлык"}


def _from_registry(seen):
    import winreg
    roots = [(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
             (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall")]
    for hive, base in roots:
        try:
            root = winreg.OpenKey(hive, base)
        except OSError:
            continue
        with root:
            i = 0
            while True:
                try:
                    sub = winreg.EnumKey(root, i)
                except OSError:
                    break
                i += 1
                try:
                    with winreg.OpenKey(root, sub) as k:
                        def v(n):
                            try:
                                return winreg.QueryValueEx(k, n)[0]
                            except OSError:
                                return None
                        name = v("DisplayName")
                        if not name or v("SystemComponent") == 1 or v("ParentKeyName") or _skip(name):
                            continue
                        if name.lower() in seen or re.match(r"^(kb\d+|security update|update for)", name.lower()):
                            continue
                        exe = None
                        icon = (v("DisplayIcon") or "").strip()
                        if icon:
                            icon = icon.split(",")[0].strip().strip('"')
                            if icon.lower().endswith(".exe") and os.path.exists(icon) and not _skip(os.path.basename(icon)):
                                exe = icon
                        if not exe:
                            loc = (v("InstallLocation") or "").strip().strip('"')
                            if loc and os.path.isdir(loc):
                                cands = [f for f in glob.glob(os.path.join(loc, "*.exe")) if not _skip(os.path.basename(f))]
                                if len(cands) == 1:
                                    exe = cands[0]
                        if exe:
                            seen[name.lower()] = {"label": name, "path": exe, "target": exe, "args": "",
                                                  "proc": os.path.basename(exe).lower(), "src": "реестр"}
                except OSError:
                    continue


def _from_startapps(seen):
    """Всё, что Windows показывает в «Пуске», включая приложения из Microsoft Store."""
    import json
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
           "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-StartApps | ConvertTo-Json -Compress"]
    r = subprocess.run(cmd, capture_output=True, timeout=30, creationflags=NO_WINDOW)
    data = json.loads(r.stdout.decode("utf-8-sig", "replace") or "[]")
    if isinstance(data, dict):
        data = [data]
    for a in data:
        name, appid = (a.get("Name") or "").strip(), (a.get("AppID") or "").strip()
        if not name or not appid or _skip(name) or name.lower() in seen:
            continue
        if appid.lower().startswith(("http:", "https:")) or appid.lower().endswith(DOC_EXT):
            continue
        exe = _known_folder_path(appid)
        if exe and exe.lower().endswith(".exe") and os.path.exists(exe):
            if _skip(os.path.basename(exe)):
                continue
            seen[name.lower()] = {"label": name, "path": exe, "target": exe, "args": "",
                                  "proc": os.path.basename(exe).lower(), "src": "Пуск"}
            continue
        seen[name.lower()] = {"label": name, "path": "shell:AppsFolder\\" + appid, "target": None,
                              "args": "", "proc": None, "src": "Store" if "!" in appid else "Пуск"}


KNOWN_FOLDERS = {
    "{6d809377-6af0-444b-8957-a3773f02200e}": os.environ.get("ProgramW6432") or os.environ.get("ProgramFiles", ""),
    "{905e63b6-c1bf-494e-b29c-65b732d3d21a}": os.environ.get("ProgramFiles", ""),
    "{7c5a40ef-a0fb-4bfc-874a-c0f2e0b9fa8e}": os.environ.get("ProgramFiles(x86)", ""),
    "{f38bf404-1d43-42f2-9305-67de0b28fc23}": os.environ.get("SystemRoot", ""),
    "{1ac14e77-02e7-4e5d-b744-2eb1ae5198b7}": os.path.join(os.environ.get("SystemRoot", ""), "System32"),
    "{d65231b0-b2f1-4857-a4ce-a8e7c6ea7d27}": os.path.join(os.environ.get("SystemRoot", ""), "SysWOW64"),
    "{3eb685db-65f9-4cf6-a03a-e3ef65729f3d}": os.environ.get("APPDATA", ""),
    "{f1b32785-6fba-4fcf-9d55-7b8e7f157091}": os.environ.get("LOCALAPPDATA", ""),
    "{5e6c858f-0e22-4760-9afe-ea3317b67173}": os.environ.get("USERPROFILE", ""),
    "{62ab5d82-fdc1-4dc3-a9dd-070d1d495d97}": os.environ.get("ProgramData", ""),
}


def _known_folder_path(appid):
    """'{GUID}\\Telegram Desktop\\Telegram.exe' → настоящий путь; обычный путь — как есть."""
    if re.match(r"^[a-zA-Z]:\\", appid):
        return appid
    m = re.match(r"^(\{[0-9a-fA-F-]{36}\})\\(.+)$", appid)
    if m:
        base = KNOWN_FOLDERS.get(m.group(1).lower())
        if base:
            return os.path.join(base, m.group(2))
    return None


_pkg_dirs = None
_pkg_lock = threading.Lock()


def _package_dir(family):
    """Папка установки Store-приложения по PackageFamilyName.
    Один вызов PowerShell на весь запуск; под замком, чтобы параллельные запросы иконок
    не увидели недозаполненный список (из-за этого раньше терялись иконки)."""
    global _pkg_dirs
    with _pkg_lock:
        if _pkg_dirs is None:
            import json
            dirs = {}
            try:
                cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command",
                       "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                       "Get-AppxPackage | Select-Object PackageFamilyName,InstallLocation | ConvertTo-Json -Compress"]
                r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
                data = json.loads(r.stdout.decode("utf-8-sig", "replace") or "[]")
                for d in data if isinstance(data, list) else [data]:
                    if d.get("PackageFamilyName") and d.get("InstallLocation"):
                        dirs[d["PackageFamilyName"].lower()] = d["InstallLocation"]
                log.info("Store-пакетов найдено: %d", len(dirs))
            except Exception:
                log.exception("Get-AppxPackage")
            _pkg_dirs = dirs
    return _pkg_dirs.get(family.lower())


def _store_logo(appid):
    """Лого Store-приложения из его AppxManifest.xml (самый крупный вариант)."""
    fam, _, app = appid.partition("!")
    d = _package_dir(fam)
    if not d:
        log.info("Store-пакет не найден: %s", fam)
        return None
    try:
        xml = open(os.path.join(d, "AppxManifest.xml"), encoding="utf-8", errors="replace").read()
    except OSError:
        return None
    logo = None
    for m in re.finditer(r"<Application\b[^>]*\bId=\"([^\"]+)\"(.*?)</Application>", xml, re.S):
        if m.group(1) == app or logo is None:
            v = re.search(r'Square44x44Logo="([^"]+)"', m.group(2)) or re.search(r'Square150x150Logo="([^"]+)"', m.group(2))
            if v:
                logo = v.group(1)
    if not logo:
        v = re.search(r"<Logo>([^<]+)</Logo>", xml)
        logo = v.group(1) if v else None
    if not logo:
        return None
    stem, ext = os.path.splitext(os.path.join(d, logo.replace("/", "\\")))
    cands = glob.glob(glob.escape(stem) + "*" + ext) + ([stem + ext] if os.path.exists(stem + ext) else [])
    cands = [c for c in cands if "contrast" not in c.lower() and "_light" not in c.lower()]
    if not cands:
        log.info("Store-лого не найдено: %s (%s)", appid, logo)
        return None
    return max(cands, key=lambda c: ("unplated" in c.lower(), os.path.getsize(c)))


def steam_root():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as k:
            return os.path.normpath(winreg.QueryValueEx(k, "SteamPath")[0])
    except Exception:
        return None


def steam_libs(root=None):
    """Папки steamapps всех библиотек Steam (основная + из libraryfolders.vdf)."""
    root = root or steam_root()
    if not root:
        return []
    libs = [os.path.join(root, "steamapps")]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        txt = open(vdf, encoding="utf-8", errors="replace").read()
        for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
            lib = os.path.join(m.group(1).replace("\\\\", "\\"), "steamapps")
            if lib not in libs:
                libs.append(lib)
    except OSError:
        pass
    return libs


def _from_steam(seen):
    for lib in steam_libs():
        for acf in glob.glob(os.path.join(lib, "appmanifest_*.acf")):
            try:
                t = open(acf, encoding="utf-8", errors="replace").read()
                appid = re.search(r'"appid"\s+"(\d+)"', t).group(1)
                name = re.search(r'"name"\s+"([^"]+)"', t).group(1)
            except Exception:
                continue
            if _skip(name) or "proton" in name.lower() or "steamworks" in name.lower() or appid == "228980":
                continue
            seen.setdefault(name.lower(), {"label": name, "path": f"steam://rungameid/{appid}", "target": None,
                                           "args": "", "proc": None, "src": "Steam", "appid": appid})


def _scan_installed():
    seen = {}
    for fn in (_from_shortcuts, _from_startapps, _from_steam, _from_registry):
        try:
            if IS_WIN or fn is _from_shortcuts:
                fn(seen)
        except Exception:
            log.exception("поиск программ: %s", fn.__name__)
    return sorted(seen.values(), key=lambda a: a["label"].lower())


_cache = None


async def list_installed(refresh=False):
    global _cache
    if _cache is None or refresh:
        if IS_WIN:
            _cache = await asyncio.get_running_loop().run_in_executor(None, _scan_installed)
        else:
            _cache = [{"label": n, "path": n, "target": n, "args": "", "proc": n.lower() + ".exe", "src": "демо"}
                      for n in ("Arduino IDE", "Discord", "Steam", "Telegram Desktop", "Spotify", "Prism Launcher")]
        log.info("Найдено программ: %d", len(_cache))
    return _cache


async def resolve_layouts(layouts):
    """Заполняет path/proc у кнопок, которые ещё не привязаны к программе."""
    installed = await list_installed()
    changed = False
    for items in layouts.values():
        for it in items:
            if it.get("kind") != "app":
                continue
            p = it.get("path") or ""
            if IS_WIN and p.lower().endswith(".lnk"):
                try:
                    tgt = _resolve_lnk(p)[0] if os.path.exists(p) else ""
                except Exception:
                    tgt = ""
                if not os.path.exists(p) or (tgt and not os.path.exists(tgt)):
                    log.info("Кнопка «%s»: ярлык мёртвый, ищу программу заново", it["label"])
                    it["path"], it["proc"] = None, None
                    changed = True
            if it.get("path") and it.get("proc"):
                continue
            if it.get("path") and not it.get("proc"):
                p = it["path"]
                if p.lower().endswith(".exe"):
                    it["proc"] = os.path.basename(p).lower()
                    changed = True
                    continue
                if p.startswith(("steam://", "shell:")):
                    continue
            match = (it.get("match") or it["label"]).lower()
            hit = next((a for a in installed if a["label"].lower() == match), None) or \
                next((a for a in installed if match in a["label"].lower()), None)
            if hit:
                it["path"], it["proc"] = hit["path"], hit["proc"]
                changed = True
                log.info("Кнопка «%s» → %s", it["label"], hit["path"])
            elif not it.get("path"):
                log.warning("Кнопка «%s»: программа не найдена — добавь её через «+» → «Выбрать .exe на ПК»", it["label"])
    return changed


def running_procs():
    names = set()
    for p in psutil.process_iter():
        try:
            n = p.name()
        except Exception:        # системный / уже завершившийся процесс — пропускаем
            continue
        if n:
            names.add(n.lower())
    return names


def _popen(exe, args=""):
    argv = [exe] + (shlex.split(args, posix=False) if args else [])
    argv = [a.strip('"') if i else a for i, a in enumerate(argv)]
    subprocess.Popen(argv, cwd=os.path.dirname(exe) or None, creationflags=DETACHED, close_fds=True)


def launch(item):
    """Запуск: ярлык → его exe напрямую; если не вышло — через оболочку Windows."""
    path = item.get("path")
    if not path:
        raise FileNotFoundError("путь не задан")
    log.info("Запуск: %s (%s)", item["label"], path)
    if not IS_WIN:
        log.info("[не Windows] запуск %s", path)
        return
    low = path.lower()
    if low.startswith("steam://") or "://" in low:
        _startfile(path)
        return
    if low.endswith(".lnk"):
        try:
            target, args, _ = _resolve_lnk(path)
        except Exception:
            target, args = "", ""
        if target and target.lower().endswith(".exe") and os.path.exists(target):
            try:
                _popen(target, args)
                return
            except OSError as e:
                log.info("Прямой запуск %s не удался (%s), пробую через ярлык", target, e)
        elif target:
            log.warning("Ярлык «%s» указывает на несуществующий файл: %s", item["label"], target)
        _startfile(path)
        return
    if low.endswith(".exe") and os.path.isabs(path):
        _popen(path, item.get("args", ""))
        return
    _startfile(path)


# ---------- окно уже запущенной программы: развернуть и вывести наверх ----------

SW_RESTORE = 9
SW_MINIMIZE = 6
VK_MENU = 0x12
KEYEVENTF_KEYUP = 0x0002
GW_OWNER = 4
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
DWMWA_CLOAKED = 14
DWM_CLOAKED_SHELL = 0x2      # скрыто оболочкой — окно на другом рабочем столе: его показать можно
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd"}   # рабочий стол и панель задач


class _Win:
    """user32 с типами. HWND — указатель: без restype ctypes обрезал бы его до int на 64 битах."""

    def __init__(self):
        import ctypes
        from ctypes import wintypes as W
        self.ct = ctypes
        self.u = u = ctypes.WinDLL("user32", use_last_error=True)
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        try:
            self.dwm = ctypes.WinDLL("dwmapi")
        except OSError:
            self.dwm = None
        H = W.HWND
        self.ENUM = ctypes.WINFUNCTYPE(W.BOOL, H, W.LPARAM)
        for name, args, res in (
                ("EnumWindows", [self.ENUM, W.LPARAM], W.BOOL),
                ("GetWindowThreadProcessId", [H, ctypes.POINTER(W.DWORD)], W.DWORD),
                ("IsWindowVisible", [H], W.BOOL),
                ("IsIconic", [H], W.BOOL),
                ("GetWindow", [H, W.UINT], H),
                ("GetWindowTextLengthW", [H], ctypes.c_int),
                ("GetClassNameW", [H, W.LPWSTR, ctypes.c_int], ctypes.c_int),
                ("ShowWindow", [H, ctypes.c_int], W.BOOL),
                ("SetForegroundWindow", [H], W.BOOL),
                ("GetForegroundWindow", [], H),
                ("BringWindowToTop", [H], W.BOOL),
                ("AttachThreadInput", [W.DWORD, W.DWORD, W.BOOL], W.BOOL),
                ("SendInput", [W.UINT, ctypes.c_void_p, ctypes.c_int], W.UINT),
                ("GetWindowTextW", [H, W.LPWSTR, ctypes.c_int], ctypes.c_int),
                ("GetWindowPlacement", [H, ctypes.c_void_p], W.BOOL),
                ("SetWindowPlacement", [H, ctypes.c_void_p], W.BOOL),
                ("SetWindowPos", [H, H, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, W.UINT], W.BOOL),
                ("SwitchToThisWindow", [H, W.BOOL], None),
                ("keybd_event", [W.BYTE, W.BYTE, W.DWORD, ctypes.c_size_t], None)):
            f = getattr(u, name)
            f.argtypes, f.restype = args, res
        self.exstyle = getattr(u, "GetWindowLongPtrW", None) or u.GetWindowLongW   # Ptr — только в 64 битах
        self.exstyle.argtypes, self.exstyle.restype = [H, ctypes.c_int], ctypes.c_ssize_t
        self.k.GetCurrentThreadId.argtypes, self.k.GetCurrentThreadId.restype = [], W.DWORD
        if self.dwm:
            self.dwm.DwmGetWindowAttribute.argtypes = [H, W.DWORD, ctypes.c_void_p, W.DWORD]
            self.dwm.DwmGetWindowAttribute.restype = ctypes.c_long

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [("dx", W.LONG), ("dy", W.LONG), ("mouseData", W.DWORD), ("dwFlags", W.DWORD),
                        ("time", W.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KEYBDINPUT(ctypes.Structure):
            _fields_ = [("wVk", W.WORD), ("wScan", W.WORD), ("dwFlags", W.DWORD), ("time", W.DWORD),
                        ("dwExtraInfo", ctypes.c_size_t)]

        class _U(ctypes.Union):
            _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", W.DWORD), ("u", _U)]
        self.INPUT = INPUT

        class WP(ctypes.Structure):
            _fields_ = [("length", W.UINT), ("flags", W.UINT), ("showCmd", W.UINT), ("ptMinPosition", W.POINT),
                        ("ptMaxPosition", W.POINT), ("rcNormalPosition", W.RECT)]
        self.WP = WP

    def _cloaked(self, hwnd):
        if not self.dwm:
            return False
        v = self.ct.c_int(0)
        if self.dwm.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, self.ct.byref(v), self.ct.sizeof(v)) != 0:
            return False
        return bool(v.value & ~DWM_CLOAKED_SHELL)

    def placement(self, hwnd):
        """(x, y, ширина, высота, развёрнуто, свёрнуто) — «обычное» место окна (как в WINDOWPLACEMENT)."""
        wp = self.WP()
        wp.length = self.ct.sizeof(wp)
        if not self.u.GetWindowPlacement(hwnd, self.ct.byref(wp)):
            return None
        r = wp.rcNormalPosition
        return r.left, r.top, r.right - r.left, r.bottom - r.top, wp.showCmd == 3, wp.showCmd == 2

    def place(self, hwnd, x, y, w, h, maximized):
        wp = self.WP()
        wp.length = self.ct.sizeof(wp)
        self.u.GetWindowPlacement(hwnd, self.ct.byref(wp))
        wp.rcNormalPosition.left, wp.rcNormalPosition.top = int(x), int(y)
        wp.rcNormalPosition.right, wp.rcNormalPosition.bottom = int(x + w), int(y + h)
        wp.showCmd = 3 if maximized else 1               # SW_SHOWMAXIMIZED / SW_SHOWNORMAL
        wp.flags = 0
        if self.u.SetWindowPlacement(hwnd, self.ct.byref(wp)):
            return True
        # не вышло (бывает с чужими окнами под Wine) — развернуть и подвинуть по-простому
        self.u.ShowWindow(hwnd, SW_RESTORE)
        ok = bool(self.u.SetWindowPos(hwnd, None, int(x), int(y), int(w), int(h), 0x0004 | 0x0010))   # NOZORDER | NOACTIVATE
        if maximized:
            self.u.ShowWindow(hwnd, 3)
        return ok

    def all_windows(self):
        """[(hwnd, pid, заголовок)] — все окна как в Alt+Tab, сверху вниз."""
        out, ct, u = [], self.ct, self.u
        pid = ct.c_ulong()
        cls = ct.create_unicode_buffer(64)

        def one(hwnd, _):
            try:
                if not hwnd or not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, GW_OWNER):
                    return True
                n = u.GetWindowTextLengthW(hwnd)
                if n <= 0 or self.exstyle(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                    return True
                u.GetClassNameW(hwnd, cls, 64)
                if cls.value in SHELL_CLASSES or self._cloaked(hwnd):
                    return True
                buf = ct.create_unicode_buffer(n + 1)
                u.GetWindowTextW(hwnd, buf, n + 1)
                u.GetWindowThreadProcessId(hwnd, ct.byref(pid))
                out.append((hwnd, pid.value, buf.value))
            except Exception:
                pass
            return True
        u.EnumWindows(self.ENUM(one), 0)
        return out

    def windows(self, pids):
        """Окна этих процессов, как в Alt+Tab: видимые (свёрнутые тоже), без владельца, с заголовком,
        не служебные. По порядку стопки: первое — то, с которым работали последним."""
        out = []
        if not pids:
            return out
        ct, u = self.ct, self.u
        pid = ct.c_ulong()
        cls = ct.create_unicode_buffer(64)

        def one(hwnd, _):
            try:
                if not hwnd or not u.IsWindowVisible(hwnd) or u.GetWindow(hwnd, GW_OWNER):
                    return True
                u.GetWindowThreadProcessId(hwnd, ct.byref(pid))
                if pid.value not in pids or u.GetWindowTextLengthW(hwnd) <= 0:
                    return True
                if self.exstyle(hwnd, GWL_EXSTYLE) & WS_EX_TOOLWINDOW:
                    return True
                u.GetClassNameW(hwnd, cls, 64)
                if cls.value not in SHELL_CLASSES and not self._cloaked(hwnd):
                    out.append(hwnd)
            except Exception:
                pass
            return True
        u.EnumWindows(self.ENUM(one), 0)
        return out

    def activate(self, hwnd):
        """Развернуть и сделать активным. Windows пускает наверх только того, от кого было последнее нажатие,
        а дек — фоновый процесс, поэтому способы по очереди, пока окно не станет активным:
        1) пустое событие мыши (мышь не двигается) — так делает PowerToys;
        2) через очередь ввода активного окна (AttachThreadInput);
        3) нажатие Alt (снимает блокировку смены фокуса — так делает AutoHotkey);
        4) SwitchToThisWindow — то же, что Alt+Tab;
        5) свернуть и развернуть — развёрнутое из панели задач окно Windows делает активным.
        True — окно стало активным."""
        u = self.u
        if u.IsIconic(hwnd):
            u.ShowWindow(hwnd, SW_RESTORE)
        if u.GetForegroundWindow() == hwnd:
            return True
        inp = self.INPUT()                  # type 0 = INPUT_MOUSE, всё по нулям
        u.SendInput(1, self.ct.byref(inp), self.ct.sizeof(inp))
        u.SetForegroundWindow(hwnd)
        if u.GetForegroundWindow() == hwnd:
            return True

        def attached():
            fg = u.GetForegroundWindow()
            me = self.k.GetCurrentThreadId()
            other = u.GetWindowThreadProcessId(fg, None) if fg else 0
            joined = bool(other and other != me and u.AttachThreadInput(me, other, True))
            try:
                u.BringWindowToTop(hwnd)
                u.SetForegroundWindow(hwnd)
            finally:
                if joined:
                    u.AttachThreadInput(me, other, False)

        def alt():
            u.keybd_event(VK_MENU, 0, 0, 0)
            try:
                u.SetForegroundWindow(hwnd)
            finally:
                u.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)

        def switch():
            u.SwitchToThisWindow(hwnd, True)

        def bounce():
            u.ShowWindow(hwnd, SW_MINIMIZE)
            u.ShowWindow(hwnd, SW_RESTORE)
            u.SetForegroundWindow(hwnd)
        for way in (attached, alt, switch, bounce):
            way()
            if u.GetForegroundWindow() == hwnd:
                return True
        return False

    def is_front(self, wins):
        """Одно из окон программы сейчас активное и не свёрнуто."""
        fg = self.u.GetForegroundWindow()
        return bool(fg) and fg in wins and not self.u.IsIconic(fg)

    def minimize(self, hwnd):
        self.u.ShowWindow(hwnd, SW_MINIMIZE)


_WIN = None


def _procs(proc):
    """PID процессов программы и, отдельно, их потомков из той же папки: окно Steam, например, рисует
    дочерний steamwebhelper.exe. У системных (из папки Windows) потомков не берём — у explorer.exe это всё подряд."""
    own, kids = set(), set()
    windir = os.path.normcase(os.environ.get("SystemRoot") or r"C:\Windows")
    for p in psutil.process_iter():          # без attrs: с ними ошибка одного процесса рвёт весь перебор
        try:
            if (p.name() or "").lower() != proc:
                continue
            own.add(p.pid)
            home = os.path.normcase(os.path.dirname(p.exe() or ""))
            if not home or home.startswith(windir):
                continue
            for c in p.children(recursive=True):
                try:
                    if os.path.normcase(c.exe() or "").startswith(home + os.sep):
                        kids.add(c.pid)
                except Exception:
                    continue
        except Exception:
            continue
    return own, kids - own


def show(item, toggle=False):
    """Уже запущенная программа: развернуть её окно из свёрнутого и вывести наверх → "shown".
    toggle (повторное нажатие плитки): окно и так наверху — свернуть его → "hidden".
    Окна нет (свёрнута в трей или ещё не успела открыть окно) — False: тогда дек запускает её ещё раз,
    и программа сама показывает окно."""
    global _WIN
    proc = (item.get("proc") or "").lower()
    if not IS_WIN or not proc:
        return False
    if _WIN is None:
        _WIN = _Win()
    own, kids = _procs(proc)
    wins = _WIN.windows(own) or _WIN.windows(kids)
    if not wins:
        return False
    if toggle and _WIN.is_front(wins):
        _WIN.minimize(_WIN.u.GetForegroundWindow())
        log.info("Сворачиваю окно: %s", item.get("label") or proc)
        return "hidden"
    on_top = _WIN.activate(wins[0])
    log.info("Показываю окно: %s%s", item.get("label") or proc,
             "" if on_top else i18n.tr(" (Windows didn't let it come to the front)", " (нагору Windows не пустила)"))
    return "shown"


# ---------- раскладки окон: запомнить, где стоят окна программ, и расставить так же ----------

def _win_obj():
    global _WIN
    if _WIN is None:
        _WIN = _Win()
    return _WIN


def grab_windows():
    """Окна сейчас (для «Запомнить раскладку»): [{proc, title, x, y, w, h, max}] — по одному на программу,
    верхнее в стопке. Дек и проводник не берём."""
    if not IS_WIN:
        return []
    w, out, seen = _win_obj(), [], set()
    names = {}
    for hwnd, pid, title in w.all_windows():
        if pid not in names:
            try:
                names[pid] = psutil.Process(pid).name().lower()
            except Exception:
                names[pid] = ""
        proc = names[pid]
        if not proc or proc in seen or proc in ("explorer.exe", "python.exe", "pythonw.exe", "applicationframehost.exe"):
            continue
        pl = w.placement(hwnd)
        if not pl:
            continue
        seen.add(proc)
        x, y, ww, hh, mx, _ = pl
        out.append({"proc": proc, "title": title[:80], "x": x, "y": y, "w": ww, "h": hh, "max": mx})
    return out


def place_window(proc, x, y, w, h, maximized):
    """Поставить окно программы на место (свёрнутое — развернуть). True — окно нашлось."""
    if not IS_WIN or not proc:
        return False
    win = _win_obj()
    own, kids = _procs(proc.lower())
    hs = win.windows(own) or win.windows(kids)
    if not hs:
        return False
    return win.place(hs[0], x, y, w, h, maximized)


def win_file_dialog(title, filters, multi=False, start=None):
    """Окно выбора файлов через PowerShell (WinForms) — для Python без tkinter (переносная сборка).
    filters: [("Программы", "*.exe;*.lnk"), ...]. Возвращает список путей (пустой — отмена)."""
    init = f"$d.InitialDirectory='{str(start).replace(chr(39), chr(39) * 2)}';" if start else ""
    flt = "|".join(f"{name}|{pat}" for name, pat in filters).replace("'", "''")
    ps = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
          "Add-Type -AssemblyName System.Windows.Forms;"
          "$f=New-Object System.Windows.Forms.Form -Property @{TopMost=$true;ShowInTaskbar=$false};"
          "$d=New-Object System.Windows.Forms.OpenFileDialog;"
          f"$d.Title='{title.replace(chr(39), chr(39) * 2)}';$d.Filter='{flt}';$d.Multiselect=${'true' if multi else 'false'};{init}"
          "if($d.ShowDialog($f) -eq 'OK'){$d.FileNames}")
    r = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", ps], capture_output=True,
                       creationflags=NO_WINDOW, timeout=900)
    out = r.stdout.decode("utf-8", "replace").replace("\ufeff", "")
    return [line.strip() for line in out.splitlines() if line.strip()]


def ask_files(title, filters, multi=False, start=None):
    """Окно выбора файлов на ПК: tkinter, а если его нет — стандартное окно Windows через PowerShell.
    start — папка, с которой открыть окно."""
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        return win_file_dialog(title, filters, multi, start)
    root = tkinter.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        types = [(name, pat.replace(";", " ")) for name, pat in filters]
        if multi:
            return list(filedialog.askopenfilenames(parent=root, title=title, filetypes=types, initialdir=start) or ())
        f = filedialog.askopenfilename(parent=root, title=title, filetypes=types, initialdir=start)
        return [f] if f else []
    finally:
        root.destroy()


def pick_exe():
    """Открывает на ПК стандартное окно выбора .exe (блокирует, вызывать в отдельном потоке)."""
    files = ask_files(i18n.tr("Noryx — choose an app", "Noryx — обери програму"),
                      [(i18n.tr("Programs", "Програми"), "*.exe;*.lnk;*.bat;*.cmd"), (i18n.tr("All files", "Усі файли"), "*.*")])
    f = files[0] if files else ""
    if not f:
        return None
    f = os.path.normpath(f)
    label = Path(f).stem
    target, args = f, ""
    if f.lower().endswith(".lnk"):
        try:
            target, args, _ = _resolve_lnk(f)
        except Exception:
            target = ""
    return {"label": label, "path": f, "target": target, "args": "", "proc": proc_from(target, args)}


async def close(proc, target=None):
    """Закрыть программу: сначала вежливо, через 4 с принудительно."""
    if not proc or proc in PROTECTED:
        return
    if proc not in running_procs():
        return
    log.info("Закрываю: %s", proc)
    if not IS_WIN:
        return
    if proc == "steam.exe":
        try:
            _startfile("steam://exit")
        except Exception:
            log.exception("Steam не закрылся штатно")
        return
    subprocess.run(["taskkill", "/IM", proc], capture_output=True, creationflags=NO_WINDOW)
    for _ in range(8):
        await asyncio.sleep(0.5)
        if proc not in running_procs():
            return
    subprocess.run(["taskkill", "/F", "/T", "/IM", proc], capture_output=True, creationflags=NO_WINDOW)


# ---------- иконки ----------

def _split_icon_loc(loc):
    """'file.dll,-123' → (file, -123); отрицательное число — ID ресурса, положительное — индекс."""
    f, sep, n = loc.rpartition(",")
    if not sep:
        return os.path.expandvars(loc.strip().strip('"')), 0
    try:
        return os.path.expandvars(f.strip().strip('"')), int(n)
    except ValueError:
        return os.path.expandvars(loc.strip().strip('"')), 0


def _icon_source(path):
    """Файл, из которого брать иконку: (путь, индекс/-ID)."""
    p = path
    low = p.lower()
    if low.startswith("steam://rungameid/"):
        appid = p.rsplit("/", 1)[-1]
        exe = _steam_game_exe(appid)
        if exe:
            return exe, 0
        root = steam_root()
        if root:      # запас: значок из кэша Steam — всего 32 px, на плитке мыльный
            cache = os.path.join(root, "appcache", "librarycache")
            for pat in (f"{appid}_icon.jpg", os.path.join(appid, "*.jpg")):
                hits = sorted(glob.glob(os.path.join(cache, pat)), key=os.path.getsize)
                if hits:
                    return hits[0], 0
        return None, 0
    if low.startswith("shell:appsfolder\\"):
        appid = p.split("\\", 1)[1]
        if "!" in appid:
            return _store_logo(appid), 0
        p = _known_folder_path(appid) or ""
        low = p.lower()
    if low.endswith(".msc"):
        return os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "mmc.exe"), 0
    if low.endswith(".lnk"):
        target, _args, icon = _resolve_lnk(p)
        if icon:
            f, idx = _split_icon_loc(icon)
            if f and os.path.exists(f):
                return f, idx
        return target, 0
    if p and not os.path.isabs(p):
        p = _which(p) or p
    return p, 0


# exe, в которых значка игры не ищем: установщики, отчёты о падениях, античиты, браузерные помощники
_NOT_GAME_EXE = re.compile(r"unins|setup|redist|vc_?redist|dxsetup|prereq|crash|report|easyanticheat|eac_|battleye|"
                           r"\bbe[a-z]*service|cefprocess|webhelper|helper|updater|uploader|unitycrashhandler|dotnet", re.I)


def _steam_install_dir(appid, libs=None):
    for lib in libs if libs is not None else steam_libs():
        try:
            t = open(os.path.join(lib, f"appmanifest_{appid}.acf"), encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        m = re.search(r'"installdir"\s+"([^"]+)"', t)
        if m:
            d = os.path.join(lib, "common", m.group(1))
            if os.path.isdir(d):
                return d
    return None


def _ico_side(path):
    """Самый большой размер значка в exe (0 — значка нет)."""
    from PIL import Image
    try:
        im = Image.open(_pe_icon(path, 0))
        return max(w for w, _h in im.ico.sizes())
    except Exception:
        return 0


def _steam_game_exe(appid, libs=None, depth=3, look=8):
    """exe игры Steam с самым крупным значком: у игр он 128–256 px, а в кэше Steam — 32 px."""
    d = _steam_install_dir(appid, libs)
    if not d:
        return None
    base = d.rstrip("\\/").count(os.sep)
    exes = []
    for dirpath, dirs, files in os.walk(d):
        if dirpath.count(os.sep) - base >= depth:
            dirs[:] = []
        dirs[:] = [x for x in dirs if not _NOT_GAME_EXE.search(x)]
        for f in files:
            if f.lower().endswith(".exe") and not _NOT_GAME_EXE.search(f):
                fp = os.path.join(dirpath, f)
                try:
                    exes.append((os.path.getsize(fp), fp))
                except OSError:
                    pass
    best, side = None, 0
    for _size, fp in sorted(exes, reverse=True)[:look]:      # крупные — скорее сама игра
        s = _ico_side(fp)
        if s > side:
            best, side = fp, s
        if side >= 128:
            break
    return best if side >= 48 else None


def _which(name):
    for d in os.environ.get("PATH", "").split(os.pathsep):
        f = os.path.join(d, name)
        if os.path.exists(f):
            return f
    return None


def _sniff(path):
    with open(path, "rb") as f:
        head = f.read(8)
    if head[:4] == b"\x00\x00\x01\x00":
        return "ico"          # в т.ч. «.exe»-иконки MSI-установщиков из C:\Windows\Installer
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:3] == b"\xff\xd8\xff":
        return "jpg"
    if head[:2] == b"MZ":
        return "pe"
    return None


def _pe_icon(path, idx):
    from icoextract import IconExtractor
    ex = IconExtractor(path)
    if idx < 0:
        try:
            return ex.get_icon(resource_id=-idx)
        except Exception:
            pass
    try:
        return ex.get_icon(num=max(0, idx))
    except Exception:
        return ex.get_icon(num=0)


def _mun_for(path):
    """С Windows 10 1903 иконки системных exe/dll лежат в SystemResources\\*.mun."""
    mun = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "SystemResources",
                       os.path.basename(path) + ".mun")
    return mun if os.path.exists(mun) else None


def _load_icon(src, idx):
    from PIL import Image
    kind = _sniff(src)
    if kind in ("ico", "png", "jpg"):
        return Image.open(src)
    if kind == "pe":
        try:
            return Image.open(_pe_icon(src, idx))
        except Exception:
            mun = _mun_for(src)
            if not mun:
                raise
            return Image.open(_pe_icon(mun, idx))
    raise ValueError("не картинка и не программа")


def _to_png(src, idx):
    from PIL import Image
    im = _load_icon(src, idx)
    if getattr(im, "format", "") == "ICO":
        try:
            sizes = sorted(im.ico.sizes())
            im = im.ico.getimage(sizes[-1])
        except Exception:
            pass
    im = im.convert("RGBA")
    bbox = im.getbbox()  # срезаем прозрачные поля (у Store-лого их много)
    if bbox and (bbox[2] - bbox[0]) > 8:
        im = im.crop(bbox)
    side = max(im.width, im.height)
    if im.width != im.height:
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        im = sq
    if im.width != ICON_PX:
        im = im.resize((ICON_PX, ICON_PX), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


_no_icon = set()
_legacy_checked = False


def _drop_legacy_icons():
    """Один раз за запуск: убрать кэш старого формата (иконки 96 px без размера в имени)."""
    global _legacy_checked
    if _legacy_checked:
        return
    _legacy_checked = True
    old = [f for f in ICON_DIR.glob("*.png") if "@" not in f.stem]
    for f in old:
        try:
            f.unlink()
        except OSError:
            pass
    if old:
        log.info("Кэш иконок обновлён под %d px (старых файлов: %d)", ICON_PX, len(old))


async def icon_png(path):
    if path in _no_icon:
        return None
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    _drop_legacy_icons()
    key = hashlib.sha1(path.lower().encode("utf-8")).hexdigest()[:16]
    # игры Steam: раньше брали 32 px из кэша Steam — «v2» берёт значок из exe игры заново
    cached = ICON_DIR / f"{key}@{ICON_PX}{'v2' if path.lower().startswith('steam://') else ''}.png"
    if cached.exists():
        return cached.read_bytes()
    if not IS_WIN:
        return None
    try:
        loop = asyncio.get_running_loop()
        src, idx = await loop.run_in_executor(None, _icon_source, path)
        if not src or not os.path.exists(src):
            _no_icon.add(path)
            return None
        png = await loop.run_in_executor(None, _to_png, src, idx)
        cached.write_bytes(png)
        return png
    except Exception as e:
        log.info("Иконка не извлеклась для %s: %s", path, e)
        _no_icon.add(path)
        return None
