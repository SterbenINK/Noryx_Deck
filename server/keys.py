"""Эмуляция клавиатуры (хоткеи, медиаклавиши) через WinAPI."""
import ctypes
import logging
import sys
import threading
import time

log = logging.getLogger("keys")

IS_WIN = sys.platform == "win32"
_lock = threading.Lock()      # скрипты и кнопки жмут из разных потоков — нажатия не перемешиваются
KEYUP = 0x0002
EXTENDED = 0x0001

VK = {
    "ctrl": 0x11, "control": 0x11, "shift": 0x10, "alt": 0x12, "win": 0x5B, "super": 0x5B, "meta": 0x5B,
    "lctrl": 0xA2, "rctrl": 0xA3, "lshift": 0xA0, "rshift": 0xA1, "lalt": 0xA4, "ralt": 0xA5, "altgr": 0xA5,
    "enter": 0x0D, "esc": 0x1B, "escape": 0x1B, "tab": 0x09, "space": 0x20,
    "backspace": 0x08, "delete": 0x2E, "del": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pgup": 0x21, "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "printscreen": 0x2C, "prtsc": 0x2C, "pause": 0x13,
    "media_play": 0xB3, "media_next": 0xB0, "media_prev": 0xB1, "media_stop": 0xB2,
    "vol_mute": 0xAD, "vol_down": 0xAE, "vol_up": 0xAF,
    "return": 0x0D, "ins": 0x2D, "pageup": 0x21, "pagedown": 0x22, "print": 0x2C,
    "capslock": 0x14, "numlock": 0x90, "scrolllock": 0x91, "menu": 0x5D, "apps": 0x5D,
    # символы (раскладка US — те же физические клавиши)
    "-": 0xBD, "minus": 0xBD, "=": 0xBB, "equal": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
    "`": 0xC0, "\\": 0xDC, ",": 0xBC, ".": 0xBE, "/": 0xBF,
    # цифровой блок; Enter цифрового блока — тот же VK_RETURN, но с флагом «расширенная»
    "kpasterisk": 0x6A, "kpplus": 0x6B, "kpminus": 0x6D, "kpdot": 0x6E, "kpslash": 0x6F, "kpenter": 0x0D,
}
for i in range(10):
    VK[f"kp{i}"] = 0x60 + i
for i in range(1, 25):
    VK[f"f{i}"] = 0x6F + i
EXT_KEYS = {0x5B, 0x2E, 0x2D, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0xB3, 0xB0, 0xB1, 0xB2, 0xAD, 0xAE, 0xAF,
            0xA3, 0xA5, 0x5D, 0x90, 0x6F}
EXT_NAMES = {"kpenter"}     # тот же код, что у обычной клавиши, но расширенная
MODS = {"ctrl", "control", "shift", "alt", "win", "super", "meta", "lctrl", "rctrl", "lshift", "rshift", "lalt", "ralt", "altgr"}


def _vk(name):
    """Имя клавиши → (код VK, расширенная ли)."""
    name = name.strip().lower()
    if name in VK:
        return VK[name], VK[name] in EXT_KEYS or name in EXT_NAMES
    if len(name) == 1 and name.isalnum():
        return ord(name.upper()), False
    raise ValueError(f"Неизвестная клавиша: {name}")


def _key(key, up=False):
    vk, ext = key
    if not IS_WIN:
        log.info("[не Windows] key %s %s", hex(vk), "up" if up else "down")
        return
    flags = (KEYUP if up else 0) | (EXTENDED if ext else 0)
    ctypes.windll.user32.keybd_event(vk, 0, flags, 0)


def _combo(combo):
    return [_vk(p) for p in combo.replace(" ", "").split("+") if p]


def known(name):
    """Есть ли такая клавиша (для проверки скриптов)."""
    try:
        _vk(name)
        return True
    except ValueError:
        return False


def hotkey(combo):
    """'ctrl+shift+o' → нажать и отпустить."""
    keys = _combo(combo)
    with _lock:
        for k in keys:
            _key(k)
            time.sleep(0.01)
        for k in reversed(keys):
            _key(k, up=True)
            time.sleep(0.01)
    log.info("Хоткей: %s", combo)


def tap(name):
    k = _vk(name)
    with _lock:
        _key(k)
        _key(k, up=True)


# ---------- для скриптов: зажать, отпустить, набрать текст ----------
def press(combo):
    """Зажать сочетание (шаг «держать»); отпустить — release()."""
    keys = _combo(combo)
    with _lock:
        for k in keys:
            _key(k)
            time.sleep(0.01)


def release(combo):
    keys = _combo(combo)
    with _lock:
        for k in reversed(keys):
            _key(k, up=True)
            time.sleep(0.01)


INPUT_KEYBOARD, UNICODE = 1, 0x0004


def _send_unicode(units):
    """SendInput с KEYEVENTF_UNICODE: любые буквы как есть, раскладка не важна."""
    from ctypes import wintypes

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                    ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]

    arr = (INPUT * (len(units) * 2))()
    for i, u in enumerate(units):
        for j, flags in enumerate((UNICODE, UNICODE | KEYUP)):
            arr[i * 2 + j].type = INPUT_KEYBOARD
            arr[i * 2 + j].u.ki = KEYBDINPUT(0, u, flags, 0, 0)
    sent = ctypes.windll.user32.SendInput(len(arr), arr, ctypes.sizeof(INPUT))
    if sent != len(arr):
        raise OSError(f"SendInput: отправлено {sent} из {len(arr)} (окно с правами администратора?)")


def type_text(text):
    """Набрать текст (шаг «текст»). Enter и Tab — настоящими клавишами. Возвращает, сколько символов пропущено."""
    chunk = []

    def flush():
        if chunk:
            if IS_WIN:
                with _lock:
                    _send_unicode(chunk)
            else:
                log.info("[не Windows] текст %d символов", len(chunk))
            chunk.clear()

    for ch in str(text):
        if ch in "\r\n\t":
            flush()
            if ch != "\r":
                tap("enter" if ch == "\n" else "tab")
            continue
        data = ch.encode("utf-16-le")
        chunk.extend(int.from_bytes(data[i:i + 2], "little") for i in range(0, len(data), 2))
    flush()
    log.info("Текст: %d символов", len(text))
    return 0


def lock_pc():
    if IS_WIN:
        ctypes.windll.user32.LockWorkStation()
    log.info("Блокировка ПК")


def screenshot():
    hotkey("win+shift+s")
