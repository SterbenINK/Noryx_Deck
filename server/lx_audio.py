"""Звук на Linux (PipeWire / PulseAudio через libpulse): общая громкость, громкость приложений,
микрофон, устройство вывода. Вызывается только из одного потока (comthread.run) — pulsectl
не любит, когда его дёргают из разных потоков."""
import logging
import os
import sys

import i18n

log = logging.getLogger("audio")

OWN = ("claude_deck_sink", "claude_deck_mic")   # служебные устройства саундпада (виртуальный микрофон)
NICE = {"discord": "Discord", "vesktop": "Discord", "spotify": "Spotify", "firefox": "Firefox",
        "chrome": "Chrome", "google-chrome": "Chrome", "chromium": "Chromium", "brave": "Brave",
        "vivaldi-bin": "Vivaldi", "opera": "Opera", "steam": "Steam", "steamwebhelper": "Steam",
        "telegram-desktop": "Telegram", "telegram": "Telegram", "obs": "OBS", "vlc": "VLC", "mpv": "mpv",
        "java": "Minecraft", "youtube-music": "YouTube Music", "zoom": "Zoom", "teamspeak": "TeamSpeak"}
WINE = {"wine64-preloader", "wine-preloader", "wine64", "wine", "wineserver"}
SKIP = {"noryx", "claude deck", "plasmashell", "speech-dispatcher", "kwin_wayland", "ksmserver"}

_pulse = None
_last_err = {}


def _log_once(what):
    e = sys.exc_info()[1]
    text = f"{type(e).__name__}: {e}"
    if _last_err.get(what) != text:
        _last_err[what] = text
        log.warning("%s: %s", what, text)


def _p():
    """Соединение с PipeWire/PulseAudio; пропало (перезапуск pipewire) — переподключаемся."""
    global _pulse
    import pulsectl
    if _pulse is not None:
        try:
            _pulse.server_info()
            return _pulse
        except Exception:
            try:
                _pulse.close()
            except Exception:
                pass
            _pulse = None
    _pulse = pulsectl.Pulse("Noryx")
    return _pulse


def available():
    try:
        _p()
        return True
    except Exception:
        _log_once("Звук недоступен (нет PipeWire/PulseAudio)")
        return False


def _nice(props):
    binary = (props.get("application.process.binary") or "").lower()
    name = props.get("application.name") or ""
    if binary in NICE:
        return NICE[binary]
    if name.lower() in NICE:
        return NICE[name.lower()]
    if binary in WINE and name:                     # игра через Proton/Wine — показываем её имя
        return name[:-4] if name.lower().endswith(".exe") else name
    return name or binary


def _ours(props):
    return props.get("application.process.id") == str(os.getpid())


def _default_sink(p):
    name = p.server_info().default_sink_name
    return next((s for s in p.sink_list() if s.name == name), None)


def _default_source(p):
    name = p.server_info().default_source_name
    src = next((s for s in p.source_list() if s.name == name), None)
    if src is not None and src.monitor_of_sink != 0xFFFFFFFF:
        return None                                 # «монитор» колонок — не микрофон
    return src


def snapshot():
    if not available():
        return {"available": False}
    p = _p()
    out = {"available": True, "master": None, "micMuted": None, "sessions": [], "devices": []}
    try:
        sink = _default_sink(p)
        if sink is not None:
            out["master"] = round(min(1.0, sink.volume.value_flat) * 100)
        src = _default_source(p)
        if src is not None:
            out["micMuted"] = bool(src.mute)
        groups = {}
        for si in p.sink_input_list():
            props = si.proplist
            name = _nice(props)
            if not name or name.lower() in SKIP or _ours(props):
                continue
            v = round(min(1.0, si.volume.value_flat) * 100)
            g = groups.setdefault(name, {"id": name, "name": name, "vol": v})
            g["vol"] = max(g["vol"], v)
        out["sessions"] = sorted(groups.values(), key=lambda g: g["name"].lower())
        default = p.server_info().default_sink_name
        out["devices"] = sorted(({"id": s.name, "name": s.description or s.name, "default": s.name == default}
                                 for s in p.sink_list() if s.name not in OWN), key=lambda d: d["name"].lower())
        _last_err.pop("звук", None)
    except Exception:
        _log_once("звук")
    return out


def set_master(v):
    p = _p()
    sink = _default_sink(p)
    if sink is not None:
        p.volume_set_all_chans(sink, max(0.0, min(1.0, v / 100)))


def set_session(name, v):
    p = _p()
    level = max(0.0, min(1.0, v / 100))
    for si in p.sink_input_list():
        if _nice(si.proplist) == name and not _ours(si.proplist):
            p.volume_set_all_chans(si, level)


def toggle_mic():
    p = _p()
    src = _default_source(p)
    if src is None:
        raise RuntimeError("микрофон не найден")
    new = not bool(src.mute)
    p.mute(src, new)
    log.info("Микрофон: %s", i18n.tr("выкл", "вимк") if new else i18n.tr("вкл", "увімк"))
    return new


def set_default_output(device_id):
    """Сменить выход по умолчанию и перевести на него уже играющие программы (как в Windows)."""
    p = _p()
    sink = next((s for s in p.sink_list() if s.name == device_id), None)
    if sink is None:
        raise RuntimeError("устройство не найдено")
    p.sink_default_set(sink)
    for si in p.sink_input_list():
        if _ours(si.proplist) or si.sink == sink.index:
            continue
        try:
            p.sink_input_move(si.index, sink.index)
        except Exception as e:
            log.info("Не перевёл %s: %s", _nice(si.proplist), e)
    log.info("Выход по умолчанию: %s", sink.description)


# ---------- что играет на ПК: монитор выхода по умолчанию — для визуализатора ----------

class Loopback:
    """Что играет на ПК — для визуализатора: запись с монитора выхода по умолчанию (PipeWire / PulseAudio),
    моно float32 48 кГц → callback(block, rate) каждые 10 мс. Свой поток (lx_sound.PulseStream), pulsectl не нужен.
    Выход по умолчанию сменили — дек открывает захват заново (Viz.restart)."""

    def __init__(self, callback):
        import numpy as np

        import lx_sound
        rate = lx_sound.RATE
        self.stream = lx_sound.PulseStream(np, "@DEFAULT_MONITOR@", lambda buf, n, *_: callback(buf[:n, 0].copy(), rate),
                                           "in", i18n.tr("Визуализатор", "Візуалізатор"))

    @property
    def error(self):
        return None if self.stream.active else "stream closed"

    def close(self):
        self.stream.close()
