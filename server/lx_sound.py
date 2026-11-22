"""Потоки звука саундпада на Linux: PipeWire / PulseAudio через libpulse-simple (ctypes).

Подставляется в pad.Pad вместо PortAudio. Микширование то же (numpy, колбэки Out/VoiceIn),
здесь только «трубы»: поток пишет блоки по 10 мс в выбранное устройство или читает микрофон.

«В эфир» на Linux — свой виртуальный микрофон: служебный выход claude_deck_sink и источник
«Noryx Virtual Mic», который Discord видит как обычный микрофон. Создаётся, как только его
выбрали, и при каждом запуске дека (модули PipeWire/PulseAudio), живёт до выхода из системы.
"""
import ctypes
import logging
import threading
import time

log = logging.getLogger("pad")

RATE = 48000
BLOCK = 480
VIRTUAL_NAME = "Noryx Virtual Mic"        # так устройство называется в настройках саундпада
OWN_SINK, OWN_SRC = "claude_deck_sink", "claude_deck_mic"

PA_STREAM_PLAYBACK, PA_STREAM_RECORD = 1, 2
PA_SAMPLE_FLOAT32LE = 5
NONE = 0xFFFFFFFF


class _Spec(ctypes.Structure):
    _fields_ = [("format", ctypes.c_int), ("rate", ctypes.c_uint32), ("channels", ctypes.c_uint8)]


class _Attr(ctypes.Structure):
    _fields_ = [("maxlength", ctypes.c_uint32), ("tlength", ctypes.c_uint32), ("prebuf", ctypes.c_uint32),
                ("minreq", ctypes.c_uint32), ("fragsize", ctypes.c_uint32)]


_lib = _pa = None


def _libs():
    global _lib, _pa
    if _lib is None:
        lib = ctypes.CDLL("libpulse-simple.so.0")
        pa = ctypes.CDLL("libpulse.so.0")
        lib.pa_simple_new.restype = ctypes.c_void_p
        lib.pa_simple_new.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                                      ctypes.c_char_p, ctypes.POINTER(_Spec), ctypes.c_void_p,
                                      ctypes.POINTER(_Attr), ctypes.POINTER(ctypes.c_int)]
        for fn in (lib.pa_simple_write, lib.pa_simple_read):
            fn.restype = ctypes.c_int
            fn.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t, ctypes.POINTER(ctypes.c_int)]
        lib.pa_simple_free.restype = None
        lib.pa_simple_free.argtypes = [ctypes.c_void_p]
        pa.pa_strerror.restype = ctypes.c_char_p
        pa.pa_strerror.argtypes = [ctypes.c_int]
        _lib, _pa = lib, pa
    return _lib, _pa


class PulseStream:
    """Один поток: выход (2 канала) или вход (1 канал), float32, 48 кГц, блоки по 10 мс."""

    def __init__(self, np, device, callback, kind, title):
        lib, pa = _libs()
        self.np, self.callback, self.kind = np, callback, kind
        out = kind == "out"
        ch = 2 if out else 1
        frame = 4 * ch
        spec = _Spec(PA_SAMPLE_FLOAT32LE, RATE, ch)
        # выход: в запасе у сервера ~30 мс; вход: отдаём кусками по 10 мс
        attr = _Attr(NONE, 3 * BLOCK * frame if out else NONE, NONE, NONE, NONE if out else BLOCK * frame)
        err = ctypes.c_int(0)
        h = lib.pa_simple_new(None, b"Noryx", PA_STREAM_PLAYBACK if out else PA_STREAM_RECORD,
                              device.encode() if device else None, title.encode(), ctypes.byref(spec), None,
                              ctypes.byref(attr), ctypes.byref(err))
        if not h:
            raise OSError(pa.pa_strerror(err.value).decode("utf-8", "replace"))
        self.h = h
        self.active = True
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._run_out if out else self._run_in, name=f"pad-{kind}",
                                       daemon=True)
        self.thread.start()

    def _run_out(self):
        np, lib, (_, pa) = self.np, _lib, _libs()
        buf = np.zeros((BLOCK, 2), np.float32)
        err = ctypes.c_int(0)
        while not self._stop.is_set():
            try:
                self.callback(buf, BLOCK, None, None)
            except Exception:
                log.exception("Саундпад: колбэк выхода")
                buf[:] = 0
            if lib.pa_simple_write(self.h, buf.ctypes.data, buf.nbytes, ctypes.byref(err)) < 0:
                log.warning("Саундпад: выход оборвался (%s)", pa.pa_strerror(err.value).decode("utf-8", "replace"))
                break
        self.active = False

    def _run_in(self):
        np, lib, (_, pa) = self.np, _lib, _libs()
        buf = np.zeros((BLOCK, 1), np.float32)
        err = ctypes.c_int(0)
        while not self._stop.is_set():
            if lib.pa_simple_read(self.h, buf.ctypes.data, buf.nbytes, ctypes.byref(err)) < 0:
                log.warning("Саундпад: микрофон оборвался (%s)", pa.pa_strerror(err.value).decode("utf-8", "replace"))
                break
            try:
                self.callback(buf, BLOCK, None, None)
            except Exception:
                log.exception("Саундпад: колбэк микрофона")
        self.active = False

    def stop(self):
        self._stop.set()

    def close(self):
        self._stop.set()
        self.thread.join(2.0)
        if not self.thread.is_alive() and self.h:
            _lib.pa_simple_free(self.h)       # освобождаем, только когда поток точно вышел из write/read
            self.h = None


class PulseFactory:
    """Фабрика потоков для pad.Pad: devices/default/inputs — имена устройств, как в настройках."""

    def __init__(self, np=None):
        self.np = np              # numpy подгружаем при первом потоке — к тому времени он точно стоит
        self._pulse = None
        self._names = {}          # отображаемое имя выхода → имя устройства в PipeWire
        self._in_names = {}
        self._devices, self._inputs, self._default = [], [], None
        self._stamp = 0.0

    # ---------- устройства ----------
    def _p(self):
        import pulsectl
        if self._pulse is not None:
            try:
                self._pulse.server_info()
                return self._pulse
            except Exception:
                try:
                    self._pulse.close()
                except Exception:
                    pass
        self._pulse = pulsectl.Pulse("Noryx (саундпад)")
        return self._pulse

    def _refresh(self):
        if time.monotonic() - self._stamp < 1.0:
            return
        self._stamp = time.monotonic()
        try:
            p = self._p()
            info = p.server_info()
            names, devs, dflt = {}, [], None
            for s in p.sink_list():
                if s.name == OWN_SINK:
                    continue
                label = s.description or s.name
                if label in names or label == VIRTUAL_NAME:
                    label = f"{label} ({s.name})"
                names[label] = s.name
                devs.append(label)
                if s.name == info.default_sink_name:
                    dflt = label
            ins = {}
            for s in p.source_list():
                if s.monitor_of_sink != NONE or s.name == OWN_SRC:
                    continue
                label = s.description or s.name
                if label in ins:
                    label = f"{label} ({s.name})"
                ins[label] = s.name
            names[VIRTUAL_NAME] = OWN_SINK                  # свой виртуальный микрофон — всегда в списке
            self._names, self._in_names = names, ins
            self._devices, self._default, self._inputs = devs + [VIRTUAL_NAME], dflt, list(ins)
        except Exception as e:
            log.warning("Саундпад: список устройств — %s", e)

    @property
    def devices(self):
        self._refresh()
        return self._devices

    @property
    def default(self):
        self._refresh()
        return self._default

    @property
    def inputs(self):
        self._refresh()
        return self._inputs

    # ---------- виртуальный микрофон ----------
    def prepare(self, mic):
        """«В эфир» выбран свой виртуальный микрофон — он должен существовать уже сейчас."""
        if mic == VIRTUAL_NAME:
            self.ensure_virtual_mic()

    def ensure_virtual_mic(self):
        p = self._p()
        if not any(s.name == OWN_SINK for s in p.sink_list()):
            p.module_load("module-null-sink", f"sink_name={OWN_SINK} "
                          "sink_properties='device.description=\"Noryx (служебный выход)\" "
                          "priority.session=0 priority.driver=0'")
            log.info("Саундпад: создан служебный выход %s", OWN_SINK)
        if not any(s.name == OWN_SRC for s in p.source_list()):
            p.module_load("module-remap-source", f"master={OWN_SINK}.monitor source_name={OWN_SRC} "
                          f"source_properties='device.description=\"{VIRTUAL_NAME}\" "
                          "priority.session=0 priority.driver=0'")
            log.info("Саундпад: создан виртуальный микрофон «%s» — выбери его в Discord", VIRTUAL_NAME)
        self._stamp = 0.0

    # ---------- потоки ----------
    def __call__(self, name, callback, kind="out"):
        if self.np is None:
            import numpy
            self.np = numpy
        self._refresh()
        if kind == "in":
            dev = self._in_names.get(name)
            if dev is None:
                raise LookupError(f"Не найдено: {name}")
            return PulseStream(self.np, dev, callback, "in", "Саундпад — голос")
        dev = self._names.get(name)
        if dev is None:
            raise LookupError(f"Не найдено: {name}")
        if dev == OWN_SINK:
            self.ensure_virtual_mic()
        title = "Саундпад — в эфир" if dev == OWN_SINK else "Саундпад"
        return PulseStream(self.np, dev, callback, "out", title)
