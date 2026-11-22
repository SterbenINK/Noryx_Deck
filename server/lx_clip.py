"""Буфер обмена Linux (текст), пара clip.py: KDE — Klipper по D-Bus, иначе wl-paste / wl-copy (Wayland)
или xclip (X11). seq() — номер изменения (здесь — хэш текста), get(), set(text)."""
import hashlib
import os
import shutil
import subprocess

import lx_util as U

_last = None


def _klipper(member, sig="", body=()):
    return U.dbus_call("org.kde.klipper", "/klipper", "org.kde.klipper.klipper", member, sig, body, timeout=1.5)


def get():
    try:
        return str(_klipper("getClipboardContents")[0])
    except Exception:
        pass
    tools = (["wl-paste", "--no-newline", "--type", "text"],) if os.environ.get("WAYLAND_DISPLAY") else ()
    tools += (["xclip", "-selection", "clipboard", "-o"],)
    for argv in tools:
        if shutil.which(argv[0]):
            try:
                r = subprocess.run(argv, capture_output=True, timeout=2)
                if r.returncode == 0:
                    return r.stdout.decode("utf-8", "replace")
            except Exception:
                continue
    return None


def seq():
    t = get()
    return hashlib.md5((t or "").encode("utf-8", "replace")).hexdigest() if t is not None else 0


def set(text):
    try:
        _klipper("setClipboardContents", "s", [str(text)])
        return True
    except Exception:
        pass
    tools = (["wl-copy"],) if os.environ.get("WAYLAND_DISPLAY") else ()
    tools += (["xclip", "-selection", "clipboard", "-i"],)
    for argv in tools:
        if shutil.which(argv[0]):
            try:
                p = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                     start_new_session=True)
                p.stdin.write(str(text).encode("utf-8"))
                p.stdin.close()
                return True
            except Exception:
                continue
    return False
