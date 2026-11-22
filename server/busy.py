"""Занята ли программа делом, которое нельзя оборвать: при смене режима такие дек не закрывает.

- пишет микрофон сейчас (голосовой чат Discord, запись, стрим): Windows — реестр «кто использует микрофон»
  (CapabilityAccessManager, LastUsedTimeStop = 0, пока пишет), Linux — `pactl` (кто записывает с источников);
- Steam, пока запущена игра из его библиотеки (процесс из …/steamapps/common/…, и Proton — по командной строке).

Только стандартная библиотека и psutil; любой сбой — «не знаю» (пусто), а не ошибка смены режима.
"""
import json
import logging
import os
import subprocess
import sys

log = logging.getLogger("busy")

STEAM = {"steam.exe", "steam", "steamwebhelper.exe", "steamwebhelper"}
_MIC_KEY = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"


def _norm(name):
    return os.path.basename(str(name).replace("#", "\\").replace("\\", "/")).lower()


def mic_users():
    """Имена процессов (как в apps.running_procs: строчными), которые прямо сейчас пишут микрофон."""
    try:
        return _mic_windows() if sys.platform == "win32" else _mic_linux()
    except Exception as e:                          # нет pactl / реестр недоступен — просто не знаем
        log.debug("микрофон: %s", e)
        return set()


def _mic_windows():
    import winreg
    out = set()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _MIC_KEY) as root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(root, sub) as k:
                    stop, _ = winreg.QueryValueEx(k, "LastUsedTimeStop")
                    start, _ = winreg.QueryValueEx(k, "LastUsedTimeStart")
            except OSError:
                continue
            if stop == 0 and start:                 # начал и ещё не закончил — пишет сейчас
                out.add(_norm(sub))
    return out


def _mic_linux():
    r = subprocess.run(["pactl", "-f", "json", "list", "source-outputs"], capture_output=True, text=True, timeout=3)
    if r.returncode:
        return set()
    out = set()
    for so in json.loads(r.stdout or "[]"):
        p = so.get("properties") or {}
        if str(p.get("stream.capture.sink")).lower() == "true" or str(p.get("stream.monitor")).lower() == "true":
            continue                                 # захват монитора выхода (визуализатор) — не микрофон
        name = p.get("application.process.binary") or p.get("application.name")
        if name:
            out.add(str(name).lower())
    return out


def steam_game():
    """Запущена ли игра из библиотеки Steam (имя её процесса или "")."""
    try:
        import psutil
    except ImportError:
        return ""
    for p in psutil.process_iter(["name", "exe", "cmdline"]):
        try:
            where = " ".join([p.info.get("exe") or ""] + (p.info.get("cmdline") or [])[:3])
        except Exception:
            continue
        w = where.replace("\\", "/").lower()
        if "/steamapps/common/" in w and "steamworks shared" not in w and (p.info.get("name") or "").lower() not in STEAM:
            return p.info.get("name") or "?"
    return ""


def reasons(procs, mic=None, game=None):
    """{процесс: почему его не закрывать} для данных процессов. mic / game — для проверок (иначе спросим систему)."""
    procs = {p for p in procs if p}
    if not procs:
        return {}
    mic = mic_users() if mic is None else mic
    out = {}
    for p in procs:
        if p.lower() in mic:
            out[p] = "mic"
    if procs & STEAM:
        g = steam_game() if game is None else game
        if g:
            for p in procs & STEAM:
                out.setdefault(p, "game")
    return out
