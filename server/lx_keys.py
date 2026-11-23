"""Клавиатура на Linux: хоткеи через виртуальную клавиатуру uinput (работает и в Wayland),
блокировка экрана и скриншот — средствами рабочего стола."""
import fcntl
import logging
import os
import shutil
import struct
import subprocess
import threading
import time

import i18n
import lx_util as U

log = logging.getLogger("keys")

# коды клавиш Linux (linux/input-event-codes.h)
KEY = {
    "ctrl": 29, "control": 29, "lctrl": 29, "rctrl": 97, "shift": 42, "lshift": 42, "rshift": 54,
    "alt": 56, "lalt": 56, "ralt": 100, "altgr": 100, "win": 125, "super": 125, "meta": 125,
    "enter": 28, "return": 28, "esc": 1, "escape": 1, "tab": 15, "space": 57, "backspace": 14,
    "delete": 111, "del": 111, "insert": 110, "ins": 110, "home": 102, "end": 107, "pgup": 104, "pageup": 104,
    "pgdn": 109, "pagedown": 109, "up": 103, "down": 108, "left": 105, "right": 106,
    "printscreen": 99, "prtsc": 99, "print": 99, "pause": 119, "capslock": 58, "numlock": 69, "scrolllock": 70,
    "menu": 127, "apps": 127,               # клавиша контекстного меню на ПК-клавиатуре — KEY_COMPOSE
    "minus": 12, "-": 12, "equal": 13, "=": 13, "[": 26, "]": 27, ";": 39, "'": 40, "`": 41, "\\": 43,
    ",": 51, ".": 52, "/": 53,
    # цифровой блок
    "kpasterisk": 55, "kpminus": 74, "kpplus": 78, "kpdot": 83, "kpenter": 96, "kpslash": 98,
    "media_play": 164, "media_next": 163, "media_prev": 165, "media_stop": 166,
    "vol_mute": 113, "vol_down": 114, "vol_up": 115,
}
for _i, _c in enumerate("1234567890"):
    KEY[_c] = 2 + _i
for _c, _code in zip("0123456789", (82, 79, 80, 81, 75, 76, 77, 71, 72, 73)):
    KEY[f"kp{_c}"] = _code
for _row, _start in (("qwertyuiop", 16), ("asdfghjkl", 30), ("zxcvbnm", 44)):
    for _i, _c in enumerate(_row):
        KEY[_c] = _start + _i
for _i in range(1, 11):
    KEY[f"f{_i}"] = 58 + _i
KEY["f11"], KEY["f12"] = 87, 88
for _i in range(13, 25):
    KEY[f"f{_i}"] = 170 + _i

EV_SYN, EV_KEY, SYN_REPORT = 0, 1, 0
UI_SET_EVBIT, UI_SET_KEYBIT, UI_DEV_CREATE = 0x40045564, 0x40045565, 0x5501
EVENT = struct.Struct("llHHi")              # struct input_event на 64-битной системе

_fd = None
_lock = threading.Lock()
UINPUT = "/dev/uinput"
HELP = ("Хоткеи: нет доступа к /dev/uinput. Один раз выполни в папке дека: sudo ./system-setup.sh "
        "(или открой доступ правилом udev KERNEL==\"uinput\", TAG+=\"uaccess\") и перезайди в систему")


def _code(name):
    name = name.strip().lower()
    if name in KEY:
        return KEY[name]
    raise ValueError(f"Неизвестная клавиша: {name}")


def _device():
    """Виртуальная клавиатура «Noryx keyboard» — создаётся один раз и живёт, пока работает дек."""
    global _fd
    if _fd is not None:
        return _fd
    try:
        fd = os.open(UINPUT, os.O_WRONLY | os.O_NONBLOCK)
    except PermissionError:
        raise PermissionError(HELP) from None
    except FileNotFoundError:
        raise FileNotFoundError("Хоткеи: в системе нет /dev/uinput (модуль ядра uinput)") from None
    try:
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_SYN)
        for code in sorted(set(KEY.values())):
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        # struct uinput_user_dev: имя[80], input_id (bustype, vendor, product, version), ff_effects_max, abs[4][64]
        os.write(fd, struct.pack("80sHHHHi256i", b"Noryx keyboard", 0x06, 0x1d6b, 0xdec0, 1, 0, *([0] * 256)))
        fcntl.ioctl(fd, UI_DEV_CREATE)
    except Exception:
        os.close(fd)
        raise
    time.sleep(0.35)                        # рабочему столу нужно мгновение, чтобы подхватить новую клавиатуру
    _fd = fd
    log.info("Виртуальная клавиатура для хоткеев готова")
    return fd


def _emit(fd, typ, code, value):
    os.write(fd, EVENT.pack(0, 0, typ, code, value))


def _press(codes):
    with _lock:
        fd = _device()
        for c in codes:
            _emit(fd, EV_KEY, c, 1)
            _emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(0.012)
        for c in reversed(codes):
            _emit(fd, EV_KEY, c, 0)
            _emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(0.012)


def _codes(combo):
    return [_code(p) for p in combo.replace(" ", "").split("+") if p]


def known(name):
    """Есть ли такая клавиша (для проверки скриптов)."""
    try:
        _code(name)
        return True
    except ValueError:
        return False


def hotkey(combo):
    """'ctrl+shift+o' → нажать и отпустить."""
    _press(_codes(combo))
    log.info("Хоткей: %s", combo)


# ---------- для скриптов: зажать, отпустить, набрать текст ----------
def press(combo):
    """Зажать сочетание (шаг «держать»); отпустить — release()."""
    codes = _codes(combo)
    with _lock:
        fd = _device()
        for c in codes:
            _emit(fd, EV_KEY, c, 1)
            _emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(0.012)


def release(combo):
    codes = _codes(combo)
    with _lock:
        fd = _device()
        for c in reversed(codes):
            _emit(fd, EV_KEY, c, 0)
            _emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(0.012)


# текст через uinput — это нажатия клавиш, поэтому по раскладке US: латиница, цифры, знаки
_SHIFTED = {"!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7", "*": "8", "(": "9", ")": "0",
            "_": "-", "+": "=", "{": "[", "}": "]", ":": ";", '"': "'", "~": "`", "|": "\\", "<": ",", ">": ".",
            "?": "/"}


def _char(ch):
    if ch == "\n":
        return KEY["enter"], False
    if ch == "\t":
        return KEY["tab"], False
    if ch == " ":
        return KEY["space"], False
    if ch.isascii() and (ch.islower() or ch.isdigit() or ch in "-=[];'`\\,./"):
        return KEY.get(ch), False
    if ch.isascii() and ch.isupper():
        return KEY.get(ch.lower()), True
    if ch in _SHIFTED:
        return KEY[_SHIFTED[ch]], True
    return None, False


def type_text(text):
    """Набрать текст (шаг «текст»). Возвращает, сколько символов пропущено (не латиница)."""
    skipped = 0
    with _lock:
        fd = _device()
        for ch in str(text):
            if ch == "\r":
                continue
            code, shift = _char(ch)
            if code is None:
                skipped += 1
                continue
            if shift:
                _emit(fd, EV_KEY, KEY["shift"], 1)
                _emit(fd, EV_SYN, SYN_REPORT, 0)
            _emit(fd, EV_KEY, code, 1)
            _emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(0.006)
            _emit(fd, EV_KEY, code, 0)
            _emit(fd, EV_SYN, SYN_REPORT, 0)
            if shift:
                _emit(fd, EV_KEY, KEY["shift"], 0)
                _emit(fd, EV_SYN, SYN_REPORT, 0)
            time.sleep(0.006)
    log.info("Текст: %d символов%s", len(text), i18n.tr(", skipped ", ", пропущено ") + str(skipped) if skipped else "")
    return skipped


def tap(name):
    _press([_code(name)])


def lock_pc():
    """Блокировка экрана: сервис экранной заставки рабочего стола, затем logind."""
    try:
        U.dbus_call("org.freedesktop.ScreenSaver", "/ScreenSaver", "org.freedesktop.ScreenSaver", "Lock")
        log.info("Блокировка ПК")
        return
    except Exception as e:
        log.info("ScreenSaver.Lock: %s", e)
    for argv in (["loginctl", "lock-session"], ["xdg-screensaver", "lock"]):
        if shutil.which(argv[0]):
            r = subprocess.run(argv, capture_output=True, timeout=10)
            if r.returncode == 0:
                log.info("Блокировка ПК (%s)", argv[0])
                return
    raise RuntimeError("не получилось заблокировать экран")


def screenshot():
    """Скриншот области: Spectacle (KDE), иначе то, что найдётся."""
    for argv in (["spectacle", "-r"], ["flameshot", "gui"], ["gnome-screenshot", "-a", "-i"], ["ksnip", "-r"]):
        if shutil.which(argv[0]):
            U.spawn(argv)
            log.info("Скриншот: %s", argv[0])
            return
    raise RuntimeError("нет программы для скриншотов (Spectacle)")
