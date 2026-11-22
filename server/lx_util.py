"""Общее для Linux-версии: папки XDG, «открыть программой по умолчанию», запуск в фоне, язык системы,
вызовы D-Bus из обычного (не asyncio) кода."""
import asyncio
import logging
import os
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger("linux")

HOME = Path.home()


def config_home():
    return Path(os.environ.get("XDG_CONFIG_HOME") or HOME / ".config")


def data_home():
    return Path(os.environ.get("XDG_DATA_HOME") or HOME / ".local" / "share")


def data_dirs():
    """Где искать .desktop-файлы и иконки: свои, системные и flatpak — даже если сессия
    не прописала flatpak в XDG_DATA_DIRS."""
    dirs = [data_home()]
    env = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
    dirs += [Path(d) for d in env.split(":") if d]
    dirs += [data_home() / "flatpak" / "exports" / "share", Path("/var/lib/flatpak/exports/share"),
             Path("/usr/local/share"), Path("/usr/share")]
    out, seen = [], set()
    for d in dirs:
        key = os.path.normpath(str(d))
        if key not in seen:
            seen.add(key)
            out.append(Path(key))
    return out


def clean_env():
    """Окружение для запускаемых программ: без следов нашего Python."""
    env = dict(os.environ)
    for k in ("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "PYTHONUNBUFFERED"):
        env.pop(k, None)
    return env


def spawn(argv, cwd=None):
    """Запустить отдельно от дека: своя сессия, без наших stdin/stdout — переживёт перезапуск дека."""
    return subprocess.Popen([str(a) for a in argv], cwd=cwd or str(HOME), env=clean_env(),
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True, close_fds=True)


def open_path(target):
    """Открыть файл, папку или ссылку программой по умолчанию."""
    for tool in (["xdg-open"], ["kde-open"], ["gio", "open"]):
        if shutil.which(tool[0]):
            spawn(tool + [str(target)])
            return True
    log.warning("Нечем открыть %s: нет xdg-open", target)
    return False


def locale_tags():
    """['uk_UA', 'uk'] — для локализованных имён программ (Name[uk]=…)."""
    lang = os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    lang = lang.split(".")[0].split("@")[0]
    if not lang or lang in ("C", "POSIX"):
        return []
    return [lang, lang.split("_")[0]] if "_" in lang else [lang]


def desktops():
    """Текущее окружение рабочего стола: {'kde'} / {'gnome'} …"""
    return {d.strip().lower() for d in (os.environ.get("XDG_CURRENT_DESKTOP") or "").split(":") if d.strip()}


def terminal_argv(argv):
    """Команда, которой запустить argv в окне терминала."""
    for term, flag in (("konsole", "-e"), ("gnome-terminal", "--"), ("kgx", "--"), ("xfce4-terminal", "-x"),
                       ("alacritty", "-e"), ("kitty", None), ("foot", None), ("xterm", "-e")):
        if shutil.which(term):
            return [term] + ([flag] if flag else []) + list(argv)
    return list(argv)


def dbus_call(dest, path, iface, member, signature="", body=(), timeout=3.0):
    """Один вызов метода на сеансовой шине из синхронного кода (свой поток и свой цикл событий).
    Возвращает тело ответа; ошибку — исключением."""
    result = {}

    def run():
        async def go():
            from dbus_fast import Message, MessageType
            from dbus_fast.aio import MessageBus
            bus = await MessageBus().connect()
            try:
                reply = await asyncio.wait_for(bus.call(Message(
                    destination=dest, path=path, interface=iface, member=member,
                    signature=signature, body=list(body))), timeout)
                if reply.message_type == MessageType.ERROR:
                    raise RuntimeError(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
                return reply.body
            finally:
                bus.disconnect()
        try:
            result["v"] = asyncio.run(go())
        except Exception as e:
            result["e"] = e

    th = threading.Thread(target=run, name="dbus-call", daemon=True)
    th.start()
    th.join(timeout + 2)
    if "e" in result:
        raise result["e"]
    if "v" not in result:
        raise TimeoutError(f"D-Bus {dest} {member}: нет ответа")
    return result["v"]
