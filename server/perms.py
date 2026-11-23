"""Разрешения и версия API плагинов.

Плагин заявляет, что ему нужно и под какую версию API он написан:

    API = 2
    PERMS = ["network", "input"]

Дек показывает это на странице «Плагины» и перед первым включением спрашивает согласия. Плюс сам смотрит в код
плагина (импорты, вызовы api.script): что код явно использует, но не заявлено, тоже видно и тоже требует согласия.

ЧЕСТНО: плагин — обычный Python в процессе дека. Разрешения — это описание и согласие пользователя, а не замок: от
честного, но неаккуратного плагина они уберегут (api.script без «input» не работает, новый плагин не стартует без
согласия, обновлённый плагин с новыми нуждами останавливается до согласия), от нарочно вредного — нет.
"""
import ast
from pathlib import Path

import i18n

API_VERSION = 2          # версия API плагинов этого дека (2 — живой поток stream: api.frame / viewers / input)
API_MIN = 1              # плагины с версией ниже уже не запускаются

# ключ → (en, uk, описание en, описание uk, риск: 2 — высокий); английский — основа и ключ пакета языка
PERMS = {
    "input":   ("Keyboard and mouse", "Клавіатура й миша", "press keys, click, deck scripts",
                "натискати клавіші, клікати, скрипти деку", 2),
    "launch":  ("Launching apps", "Запуск програм", "launch programs and commands on the PC",
                "запускати програми й команди на ПК", 2),
    "native":  ("System calls", "Системні виклики", "call Windows / system libraries directly (ctypes)",
                "напряму викликати Windows / системні бібліотеки (ctypes)", 2),
    "screen":  ("Screen", "Екран", "capture the PC screen", "знімати картинку з екрана ПК", 2),
    "files":   ("Files", "Файли", "read and write files outside its folder", "читати й писати файли поза своєю текою", 1),
    "network": ("Network", "Мережа", "network and internet connections", "з'єднання мережею та з інтернетом", 1),
    "devices": ("Devices", "Пристрої", "COM / USB / Bluetooth, monitors, Arduino", "COM / USB / Bluetooth, монітори, Arduino", 1),
    "audio":   ("Sound", "Звук", "access to the audio system", "доступ до аудіосистеми", 1),
    "windows": ("Windows", "Вікна", "manage app windows", "керувати вікнами програм", 1),
    "system":  ("System info", "Відомості про систему", "read load, temperatures, processes",
                "читати навантаження, температури, процеси", 0),
}
ORDER = list(PERMS)

# что видно в коде → разрешение (импорт модуля или вызов)
SNIFF_MODULES = {
    "network": ["socket", "urllib", "http", "requests", "aiohttp", "websocket", "websockets", "ssl", "ftplib",
                "smtplib", "httpx", "paho"],
    "launch": ["subprocess", "webbrowser", "pty"],
    "devices": ["serial", "usb", "hid", "bluetooth", "bleak", "smbus", "pyudev"],
    "audio": ["sounddevice", "pyaudio", "soundcard", "pycaw", "pulsectl", "simpleaudio", "winsound", "pygame.mixer"],
    "windows": ["win32gui", "pywinauto", "pygetwindow", "ewmh", "Xlib"],
    "system": ["psutil", "wmi", "GPUtil", "pynvml", "cpuinfo"],
    "input": ["pynput", "keyboard", "mouse", "pyautogui", "pydirectinput"],
    "screen": ["mss", "PIL.ImageGrab", "dxcam", "d3dshot", "pyscreenshot"],
    "native": ["ctypes", "cffi", "win32api", "comtypes", "pythoncom", "winreg", "_winapi"],
}
SNIFF_CALLS = {               # атрибут вызова → разрешение
    "script": "input",        # api.script(...) — скрипты дека (клавиши, шаги сценариев)
    "system": "launch", "popen": "launch", "startfile": "launch", "execv": "launch", "execvp": "launch",
    "spawnl": "launch", "spawnv": "launch",
}
# устаревшие методы api: имя → (с какой версии API устарел, чем заменить ru, uk); обращение отмечается у плагина
DEPRECATED = {}


def about(key, lang):
    p = PERMS.get(key)
    return (p[3] if lang == "uk" else i18n.PACK.get(p[2], p[2])) if p else ""


def label(key, lang):
    p = PERMS.get(key)
    return (p[1] if lang == "uk" else i18n.PACK.get(p[0], p[0])) if p else key


def clean_perms(v):
    """PERMS плагина → (известные, в порядке ORDER; непонятные имена)."""
    items = v if isinstance(v, (list, tuple, set)) else [v] if isinstance(v, str) else []
    names = {str(x).strip().lower() for x in items if isinstance(x, str)}
    return [k for k in ORDER if k in names], sorted(n for n in names if n not in PERMS)[:10]


def _mod_hit(name):
    out = []
    for perm, mods in SNIFF_MODULES.items():
        for m in mods:
            if name == m or name.startswith(m + "."):
                out.append((perm, m))
    return out


def sniff(files):
    """Что видно в коде плагина: {разрешение: [откуда, ...]} — импорты и вызовы во всех его .py."""
    found = {}

    def add(perm, src):
        lst = found.setdefault(perm, [])
        if src not in lst and len(lst) < 5:
            lst.append(src)
    for f in files:
        try:
            tree = ast.parse(Path(f).read_text(encoding="utf-8"), str(f))
        except (OSError, SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            if isinstance(n, ast.Import):
                for a in n.names:
                    for perm, m in _mod_hit(a.name):
                        add(perm, m)
            elif isinstance(n, ast.ImportFrom) and n.module and not n.level:
                for perm, m in _mod_hit(n.module):
                    add(perm, m)
                for a in n.names:
                    for perm, m in _mod_hit(f"{n.module}.{a.name}"):
                        add(perm, m)
            elif isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in SNIFF_CALLS:
                obj = n.func.value
                base = obj.id if isinstance(obj, ast.Name) else obj.attr if isinstance(obj, ast.Attribute) else ""
                attr = n.func.attr
                if attr == "script" and "api" not in base.lower():
                    continue              # чужие .script(...) не считаем — только api.script
                if attr != "script" and base != "os":
                    continue              # system / popen / startfile — только os.*
                add(SNIFF_CALLS[attr], f"{base}.{attr}()")
    return {k: found[k] for k in ORDER if k in found}


def check_api(v):
    """API плагина → (номер, состояние): ok / old (ниже API_MIN — не запускаем) / new (новее дека) / none."""
    if v is None:
        return 1, "none"
    try:
        n = int(v)
    except (TypeError, ValueError):
        return 1, "none"
    if n > API_VERSION:
        return n, "new"
    if n < API_MIN:
        return n, "old"
    return n, "ok"
