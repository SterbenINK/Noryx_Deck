"""Саундпад: кнопки со звуками на отдельном экране дека.

Звук играет «себе» (наушники / колонки) и, если включено, «в эфир» — в виртуальный кабель,
который Discord слушает как микрофон. В тот же кабель дек подмешивает голос с микрофона
гарнитуры: Discord получает голос + звуки одним потоком.

Звуки — файлы в папке sounds (mp3, wav, ogg, flac, opus). Порядок — по имени файла;
номер в начале («01 …», «02_…») задаёт порядок и на кнопке не показывается.

Весь звук живёт в отдельном потоке «pad»: там PortAudio (sounddevice) и декодирование (soundfile).
Микширование — в колбэке PortAudio на numpy, свой поток на каждый выход.
"""
import logging
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import i18n

log = logging.getLogger("pad")

ROOT = Path(__file__).resolve().parent.parent
SOUNDS = ROOT / "sounds"
EXTS = {".mp3", ".wav", ".ogg", ".flac", ".opus", ".aif", ".aiff"}
RATE = 48000                  # все звуки приводим к 48 кГц — родная частота почти всех устройств Windows
IDLE_CLOSE = 30.0             # отпустить звуковые устройства после стольких секунд тишины
REFRESH_EVERY = 60.0          # перечитать список устройств (смена «по умолчанию», воткнутая гарнитура)
CACHE_FRAMES = 20_000_000     # кэш раскодированных звуков: ~160 МБ
TARGET_RMS = 0.18             # выравнивание громкости: средний уровень ≈ −15 dBFS
MAX_BOOST = 4.0               # тихие звуки поднимаем не больше чем в 4 раза (+12 дБ)
BLOCK = 480                   # 10 мс при 48 кГц — размер блока во всех потоках
VOICE_BUFFER = 0.04           # сколько голоса держим в запасе (~40 мс) — на заминки Python
VIRTUAL = re.compile(r"cable|vb-audio|voicemeeter|virtual|steam streaming", re.I)
# на Linux вместо PortAudio — PipeWire через libpulse (lx_sound), sounddevice там не нужен
NEEDS = ("numpy", "sounddevice", "soundfile") if sys.platform == "win32" else ("numpy", "soundfile")
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


def ensure_packages():
    """numpy / sounddevice / soundfile ставятся сами при первом запуске."""
    missing = []
    for mod in NEEDS:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        return True
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":
        py = py.with_name("python.exe")
    log.info("Для саундпада ставлю: %s", ", ".join(missing))
    try:
        r = subprocess.run([str(py), "-m", "pip", "install", *missing], capture_output=True,
                           timeout=600, creationflags=NO_WINDOW)
        if r.returncode:
            log.warning("pip install не удался:\n%s", (r.stdout + r.stderr).decode("utf-8", "replace")[-1500:])
    except Exception as e:
        log.warning("pip install: %s", e)
    import importlib
    importlib.invalidate_caches()
    try:
        for mod in NEEDS:
            __import__(mod)
        log.info("Библиотеки звука установлены")
        return True
    except ImportError:
        return False


def natural_key(s):
    return [int(t) if t.isdigit() else t.casefold() for t in re.split(r"(\d+)", s)]


def display_name(stem):
    name = re.sub(r"^\d+[\s._-]+", "", stem).strip() or stem
    return re.sub(r"[_]+", " ", name).strip()


class Voice:
    __slots__ = ("sid", "data", "pos", "gain")

    def __init__(self, sid, data, gain):
        self.sid, self.data, self.pos, self.gain = sid, data, 0, gain


class Ring:
    """Кольцевой буфер (моно float32): микрофон пишет, выход «в эфир» читает."""

    def __init__(self, np, size):
        self.np, self.size = np, size
        self.buf = np.zeros(size, np.float32)
        self.r = self.n = 0
        self.lock = threading.Lock()

    def count(self):
        return self.n

    def write(self, x):
        with self.lock:
            m = len(x)
            if m > self.size:
                x, m = x[-self.size:], self.size
            over = self.n + m - self.size
            if over > 0:                          # переполнение — выкидываем самое старое
                self.r = (self.r + over) % self.size
                self.n -= over
            w = (self.r + self.n) % self.size
            k = min(m, self.size - w)
            self.buf[w:w + k] = x[:k]
            if k < m:
                self.buf[:m - k] = x[k:]
            self.n += m

    def read(self, m):
        out = self.np.zeros(m, self.np.float32)
        with self.lock:
            k = min(m, self.n)
            j = min(k, self.size - self.r)
            out[:j] = self.buf[self.r:self.r + j]
            if j < k:
                out[j:k] = self.buf[:k - j]
            self.r = (self.r + k) % self.size
            self.n -= k
        return out

    def drop(self, m):
        with self.lock:
            m = min(m, self.n)
            self.r = (self.r + m) % self.size
            self.n -= m


class VoiceIn:
    """Голос с микрофона гарнитуры → кольцевой буфер → подмешивается в выход «в эфир».
    Часы микрофона и кабеля чуть расходятся — подстраиваемся, пропуская или повторяя
    по одному отсчёту (на слух незаметно)."""

    def __init__(self, pad):
        self.pad = pad
        self.stream = None
        self.device = None
        self.rate = RATE
        self.ring = None
        self.level = 0.0
        self.error = None
        self.prefill = True

    def callback(self, indata, frames, time_info, status):
        x = indata[:, 0]
        ring = self.ring
        if ring is not None:
            ring.write(x)
        pk = float(self.pad.np.max(self.pad.np.abs(x))) if frames else 0.0
        self.level = max(pk, self.level * 0.8)

    def block(self, frames, rate):
        np, ring = self.pad.np, self.ring
        if ring is None or self.stream is None or rate != self.rate:
            return None
        target = max(int(VOICE_BUFFER * self.rate), 2 * frames)
        fill = ring.count()
        if self.prefill:                          # копим запас, прежде чем отдавать голос
            if fill < target:
                return None
            self.prefill = False
        if fill < frames:                         # микрофон запнулся — отдаём что есть и снова копим
            self.prefill = True
            return ring.read(frames)
        if fill > 3 * target:                     # выход долго стоял — сбрасываем лишнее, задержка не растёт
            ring.drop(fill - target)
            fill = target
        if fill > target + frames:                # микрофон чуть спешит — один отсчёт пропускаем
            return np.delete(ring.read(frames + 1), frames // 2)
        if fill < target - frames // 2 and frames > 2:   # чуть отстаёт — один отсчёт повторяем
            x = ring.read(frames - 1)
            return np.insert(x, frames // 2, x[frames // 2])
        return ring.read(frames)


class Out:
    """Один выход («себе» или «в эфир»): поток PortAudio и звучащие в нём голоса."""

    def __init__(self, pad, role):
        self.pad, self.role = pad, role
        self.stream = None
        self.device = None        # имя устройства, на котором открыт поток
        self.rate = RATE
        self.voices = []
        self.lock = threading.Lock()
        self.error = None
        self.level = 0.0

    def callback(self, outdata, frames, time_info, status):
        np = self.pad.np
        buf = np.zeros((frames, 2), np.float32)
        ended = False
        with self.lock:
            keep = []
            for v in self.voices:
                n = min(frames, len(v.data) - v.pos)
                if n > 0:
                    buf[:n] += v.data[v.pos:v.pos + n] * v.gain
                    v.pos += n
                if v.pos < len(v.data):
                    keep.append(v)
                else:
                    ended = True
            self.voices = keep
        buf *= self.pad.volume(self.role)
        if self.role == "mic":                    # голос с гарнитуры — в тот же поток, что и звуки
            v = self.pad.vin.block(frames, self.rate)
            if v is not None:
                buf += (v * self.pad.voice_gain())[:, None]
        np.clip(buf, -1.0, 1.0, out=buf)
        outdata[:] = buf
        if self.role == "mic":
            pk = float(np.max(np.abs(buf))) if frames else 0.0
            self.level = max(pk, self.level * 0.8)
        if ended:
            self.pad.q.put(("ended", None))

    def playing(self):
        with self.lock:
            return {v.sid: v.pos / self.rate for v in self.voices}


class Pad:
    def __init__(self, cfg, on_update, stream_factory=None):
        self.cfg = cfg                    # store.cfg (padMe, padMic, padVolMe, padVolMic, padNorm, padSolo)
        self.on_update = on_update        # (dict) — из потока саундпада
        self.stream_factory = stream_factory  # для тестов
        self.q = queue.Queue()
        self.np = self.sd = self.sf = None
        self.status = "install"
        self.sounds = []                  # [{id, name, dur, bad}]
        self.paths = {}                   # id → Path
        self.info = {}                    # id → (mtime, size, dur)
        self.cache = {}                   # (id, rate) → (mtime, data, norm_gain)
        self.cache_order = []
        self.dir_mtime = None
        self.outs = {"me": Out(self, "me"), "mic": Out(self, "mic")}
        self.last_sound = 0.0
        self.last_refresh = 0.0
        self.devices, self.default = [], None
        self.inputs = []                  # микрофоны (для голоса «в эфир»)
        self.vin = VoiceIn(self)
        self.voice_retry = 0.0
        self.meter_until = 0.0
        self.last_meter = 0.0
        self.last_state = None
        self.warm = []                    # что раскодировать заранее, пока никто не жмёт
        SOUNDS.mkdir(exist_ok=True)
        threading.Thread(target=self._run, name="pad", daemon=True).start()

    # ---------- вызывается из сервера ----------
    def play(self, sid):
        self.q.put(("play", sid))

    def stop(self):
        self.q.put(("stop", None))

    def scan(self):
        self.q.put(("scan", None))

    def refresh_devices(self, force=False):
        self.q.put(("devices", force))

    def meter(self, on):
        self.q.put(("meter", on))

    def config_changed(self):
        self.q.put(("cfg", None))

    def voice_gain(self):
        try:
            return max(0.0, min(2.0, float(self.cfg.get("padVoiceVol", 100)) / 100.0))
        except (TypeError, ValueError):
            return 1.0

    def volume(self, role):
        key = "padVolMe" if role == "me" else "padVolMic"
        try:
            return max(0.0, min(1.0, float(self.cfg.get(key, 80)) / 100.0))
        except (TypeError, ValueError):
            return 0.8

    # ---------- поток ----------
    def _push(self, msg):
        try:
            self.on_update(msg)
        except Exception:
            log.exception("pad push")

    def _run(self):
        self._push_list()                         # «ставлю библиотеки…», пока идёт установка
        if not ensure_packages():
            self.status = "nolib"
            self._push_list()
            while self.q.get()[0] != "scan":      # «Повторить» на деке — ещё попытка установки
                pass
            self.status = "install"
            self._push_list()
            if not ensure_packages():
                self.status = "nolib"
                self._push_list()
                return
        import numpy
        import soundfile
        self.np, self.sf = numpy, soundfile
        if self.stream_factory is None:
            try:
                import sounddevice
                self.sd = sounddevice
            except Exception as e:                 # нет PortAudio / нет звуковых устройств
                log.warning("sounddevice: %s", e)
        self.status = "ok"
        self._refresh_devices(force=True)
        self._prepare()
        self._scan()
        while True:
            metering = time.monotonic() < self.meter_until
            try:
                cmd, arg = self.q.get_nowait() if self.warm else self.q.get(timeout=0.15 if metering else 1.0)
            except queue.Empty:
                cmd, arg = None, None
                if self.warm:                     # очередь пуста — раскодируем следующий звук впрок
                    try:
                        self._load(self.warm.pop(0), RATE)
                    except Exception as e:
                        log.info("Не раскодировался звук: %s", e)
            try:
                if cmd == "play":
                    self._toggle(arg)
                elif cmd == "stop":
                    self._stop_all()
                elif cmd == "scan":
                    self._scan()
                elif cmd == "devices":
                    if arg:                       # «Обновить устройства»: всё закрыть и перечитать
                        self._close_all()
                    self._refresh_devices(force=True)
                    self._push_list()
                elif cmd == "cfg":
                    self._apply_cfg()
                    self._prepare()
                elif cmd == "meter":
                    self.meter_until = time.monotonic() + 15.0 if arg else 0.0
                self._ensure_voice()
                if cmd in ("play", "stop", "ended", "cfg", "devices"):
                    self._push_state()
                self._idle()
                if time.monotonic() < self.meter_until and time.monotonic() - self.last_meter >= 0.14:
                    self.last_meter = time.monotonic()
                    self._push({"t": "padMeter", "voice": round(self.vin.level, 4),
                                "out": round(self.outs["mic"].level, 4), "live": self.vin.stream is not None})
            except Exception:
                log.exception("Саундпад")

    # ---------- библиотека ----------
    def _scan(self):
        files = []
        try:
            for p in SOUNDS.rglob("*"):
                if p.is_file() and p.suffix.lower() in EXTS and not any(part.startswith(".") for part in p.parts):
                    files.append(p)
        except OSError as e:
            log.warning("Папка звуков: %s", e)
        files.sort(key=lambda p: natural_key(str(p.relative_to(SOUNDS))))
        sounds, paths = [], {}
        for p in files:
            sid = p.relative_to(SOUNDS).as_posix()
            paths[sid] = p
            try:
                st = p.stat()
                cached = self.info.get(sid)
                if cached and cached[:2] == (st.st_mtime, st.st_size):
                    dur = cached[2]
                else:
                    dur = float(self.sf.info(str(p)).duration)
                    self.info[sid] = (st.st_mtime, st.st_size, dur)
                bad = False
            except Exception as e:
                log.info("Не читается %s: %s", p.name, e)
                dur, bad = 0.0, True
            sounds.append({"id": sid, "name": display_name(p.stem), "dur": round(dur, 2), "bad": bad})
        self.sounds, self.paths = sounds, paths
        try:
            self.dir_mtime = self._dir_stamp()
        except OSError:
            pass
        self._push_list()
        self.warm = [s["id"] for s in sounds if not s["bad"]]   # первое нажатие — без задержки

    def _dir_stamp(self):
        """Меняется, когда в папке звуков что-то добавили, удалили или переименовали."""
        stamp = [SOUNDS.stat().st_mtime]
        for d in SOUNDS.iterdir():
            if d.is_dir():
                stamp.append(d.stat().st_mtime)
        return tuple(stamp)

    def check_folder(self):
        """Дёргается сервером раз в несколько секунд: папку поменяли — пересканировать."""
        try:
            if self.status == "ok" and self.dir_mtime is not None and self._dir_stamp() != self.dir_mtime:
                self.q.put(("scan", None))
        except OSError:
            pass

    def _load(self, sid, rate):
        """Раскодировать звук в стерео float32 нужной частоты (с кэшем)."""
        np = self.np
        p = self.paths.get(sid)
        if p is None:
            return None
        try:
            mtime = p.stat().st_mtime
        except OSError:
            return None                           # файл удалили — пересканируем при следующей проверке
        key = (sid, rate)
        hit = self.cache.get(key)
        if hit and hit[0] == mtime:
            return hit
        data, sr = self.sf.read(str(p), dtype="float32", always_2d=True)
        if data.shape[1] == 1:
            data = np.repeat(data, 2, axis=1)
        elif data.shape[1] > 2:
            data = data[:, :2]
        if sr != rate and len(data) > 1:
            n = max(1, int(round(len(data) * rate / sr)))
            t = np.linspace(0.0, len(data) - 1, n)
            src = np.arange(len(data), dtype=np.float64)
            data = np.stack([np.interp(t, src, data[:, c]) for c in range(2)], axis=1).astype(np.float32)
        data = np.ascontiguousarray(data, dtype=np.float32)
        peak = float(np.max(np.abs(data))) if len(data) else 0.0
        rms = float(np.sqrt(np.mean(np.square(data, dtype=np.float64)))) if len(data) else 0.0
        norm = 1.0
        if rms > 1e-5:
            norm = min(TARGET_RMS / rms, MAX_BOOST)
            if peak > 0:
                norm = min(norm, 0.98 / peak)
        entry = (mtime, data, norm)
        self.cache[key] = entry
        if key in self.cache_order:
            self.cache_order.remove(key)
        self.cache_order.append(key)
        while sum(len(self.cache[k][1]) for k in self.cache_order) > CACHE_FRAMES and len(self.cache_order) > 1:
            self.cache.pop(self.cache_order.pop(0), None)
        return entry

    # ---------- устройства ----------
    def _wasapi(self):
        if not self.sd:
            return None
        for i, h in enumerate(self.sd.query_hostapis()):
            if "WASAPI" in h["name"]:
                return i
        return None

    def _refresh_devices(self, force=False):
        if self.stream_factory is not None:       # тесты: устройства задаёт подделка
            self.devices = list(getattr(self.stream_factory, "devices", []))
            self.default = getattr(self.stream_factory, "default", None)
            self.inputs = list(getattr(self.stream_factory, "inputs", []))
            return
        if not self.sd:
            self.devices, self.default = [], None
            return
        if not force and time.monotonic() - self.last_refresh < REFRESH_EVERY:
            return
        if any(o.stream for o in self.outs.values()) or self.vin.stream is not None:
            return                                 # перечитывать можно только когда ничего не открыто
        try:
            self.sd._terminate()
            self.sd._initialize()
        except Exception as e:
            log.info("PortAudio: %s", e)
        self.last_refresh = time.monotonic()
        try:
            devs = self.sd.query_devices()
            hi = self._wasapi()
            if hi is not None:
                h = self.sd.query_hostapis(hi)
                idx = list(h["devices"])
                dflt = h["default_output_device"]
            else:
                idx = list(range(len(devs)))
                dflt = self.sd.default.device[1] if self.sd.default.device else -1
            self.devices = [devs[i]["name"] for i in idx if devs[i]["max_output_channels"] > 0]
            self.inputs = [devs[i]["name"] for i in idx if devs[i]["max_input_channels"] > 0]
            self.default = devs[dflt]["name"] if dflt is not None and dflt >= 0 else None
        except Exception as e:
            log.warning("Список устройств: %s", e)
            self.devices, self.default = [], None

    def _prepare(self):
        """Linux: свой виртуальный микрофон «в эфир» создаётся сразу при выборе и при запуске дека,
        а не при первом звуке — иначе Discord не найдёт его в списке микрофонов."""
        prep = getattr(self.stream_factory, "prepare", None)
        if prep:
            try:
                prep(self._device_for("mic"))
            except Exception as e:
                log.warning("Саундпад: виртуальный микрофон — %s", e)

    def _device_for(self, role):
        want = self.cfg.get("padMe" if role == "me" else "padMic", "default" if role == "me" else "off")
        if not want or want == "off":
            return None
        if want == "default":
            return self.default or (self.devices[0] if self.devices else None)
        return want

    def _open(self, out, name):
        if self.stream_factory:
            try:
                out.stream = self.stream_factory(name, out.callback)
            except Exception as e:
                out.stream, out.error = None, str(e) if str(e).startswith("Not found") else f"Didn't open {name}: {e}"
                log.warning("Саундпад: %s", out.error)
                return False
            out.device, out.rate, out.error = name, RATE, None
            log.info("Саундпад: %s → %s", i18n.tr("for me", "собі") if out.role == "me" else i18n.tr("on air", "в ефір"), name)
            return True
        sd = self.sd
        if not sd:
            out.error = "No sound system"
            return False
        hi = self._wasapi()
        idx = None
        for i in (sd.query_hostapis(hi)["devices"] if hi is not None else range(len(sd.query_devices()))):
            if sd.query_devices(i)["name"] == name and sd.query_devices(i)["max_output_channels"] > 0:
                idx = i
                break
        if idx is None:
            out.error = f"Not found: {name}"
            return False
        tries = []
        if hi is not None:
            tries.append((RATE, sd.WasapiSettings(auto_convert=True)))
        tries.append((int(sd.query_devices(idx)["default_samplerate"]), None))
        err = None
        for rate, extra in tries:
            try:
                s = sd.OutputStream(device=idx, samplerate=rate, channels=2, dtype="float32", latency=0.08,
                                    blocksize=BLOCK, callback=out.callback, extra_settings=extra)
                s.start()
                out.stream, out.device, out.rate, out.error = s, name, rate, None
                log.info("Саундпад: %s → %s (%d Гц)", i18n.tr("for me", "собі") if out.role == "me" else i18n.tr("on air", "в ефір"), name, rate)
                return True
            except Exception as e:
                err = e
        out.error = f"Didn't open {name}: {err}"
        log.warning("Саундпад: %s", out.error)
        return False

    def _voice_device(self):
        name = self.cfg.get("padVoice", "off")
        if not name or name == "off":
            return None
        if VIRTUAL.search(name):                  # голос из кабеля в тот же кабель — петля, не даём
            self.vin.error = "Голос нельзя брать с виртуального устройства"
            return None
        return name

    def _voice_wanted(self):
        return self._device_for("mic") is not None and self._voice_device() is not None

    def _open_in(self, name):
        vin = self.vin
        self._close_in()
        vin.ring = Ring(self.np, RATE)            # секунда запаса — с головой
        vin.prefill = True
        if self.stream_factory:
            try:
                vin.stream = self.stream_factory(name, vin.callback, "in")
            except Exception as e:
                vin.stream, vin.error = None, str(e) if str(e).startswith("Not found") else f"The mic didn't open {name}: {e}"
                log.warning("Саундпад: %s", vin.error)
                return False
            vin.device, vin.rate, vin.error = name, RATE, None
            log.info("Саундпад: голос ← %s", name)
            return True
        sd = self.sd
        if not sd:
            vin.error = "No sound system"
            return False
        hi = self._wasapi()
        idx = None
        for i in (sd.query_hostapis(hi)["devices"] if hi is not None else range(len(sd.query_devices()))):
            if sd.query_devices(i)["name"] == name and sd.query_devices(i)["max_input_channels"] > 0:
                idx = i
                break
        if idx is None:
            vin.error = f"Not found: {name}"
            return False
        tries = [(RATE, sd.WasapiSettings(auto_convert=True))] if hi is not None else []
        tries.append((RATE, None))
        err = None
        for rate, extra in tries:
            try:
                s = sd.InputStream(device=idx, samplerate=rate, channels=1, dtype="float32", latency="low",
                                   blocksize=BLOCK, callback=vin.callback, extra_settings=extra)
                s.start()
                vin.stream, vin.device, vin.rate, vin.error = s, name, rate, None
                log.info("Саундпад: голос ← %s (%d Гц)", name, rate)
                return True
            except Exception as e:
                err = e
        vin.error = f"The mic didn't open {name}: {err}"
        log.warning("Саундпад: %s", vin.error)
        return False

    def _close_in(self):
        vin = self.vin
        if vin.stream is not None:
            try:
                vin.stream.stop()
                vin.stream.close()
            except Exception:
                pass
        vin.stream, vin.device, vin.ring, vin.level = None, None, None, 0.0

    def _close_all(self):
        for o in self.outs.values():
            self._close(o)
        self._close_in()

    def _ensure_voice(self):
        """Голос «в эфир» работает постоянно, пока он включён: Discord слушает кабель всё время."""
        vin = self.vin
        if not self._voice_wanted():
            if vin.stream is not None:
                self._close_in()
            if self.cfg.get("padVoice", "off") in ("off", "", None):
                vin.error = None
            return
        now = time.monotonic()
        if now < self.voice_retry:
            return
        alive = lambda st: st is not None and getattr(st, "active", True)
        out = self.outs["mic"]
        if out.stream is not None and not alive(out.stream):
            log.info("Саундпад: поток «в эфир» остановился — переоткрываю")
            self._close(out)
        if vin.stream is not None and not alive(vin.stream):
            log.info("Саундпад: микрофон отвалился — переоткрываю")
            self._close_in()
        ok = True
        if out.stream is None or out.device != self._device_for("mic"):
            ok = self._ensure_out("mic") is not None
        if ok and (vin.stream is None or vin.device != self._voice_device()):
            ok = self._open_in(self._voice_device())
        if not ok:
            self.voice_retry = now + 5.0
            if any(e and e.startswith("Not found") for e in (out.error, vin.error)):
                self._close_all()                 # устройство пропало/появилось — перечитать список
                self._refresh_devices(force=True)
                self._push_list()
            self._push_state()

    def _close(self, out):
        if out.stream is not None:
            try:
                out.stream.stop()
                out.stream.close()
            except Exception:
                pass
        out.stream, out.device = None, None
        with out.lock:
            out.voices = []

    def _ensure_out(self, role):
        out = self.outs[role]
        name = self._device_for(role)
        if name is None:
            if out.stream:
                self._close(out)
            out.error = None
            return None
        if out.stream is not None and out.device == name:
            return out
        self._close(out)
        if not any(o.stream for o in self.outs.values()):
            self._refresh_devices()
            name = self._device_for(role)
            if name is None:
                return None
        return out if self._open(out, name) else None

    def _apply_cfg(self):
        """Сменили устройство — закрываем потоки, при следующем звуке откроются нужные."""
        for role, out in self.outs.items():
            name = self._device_for(role)
            if out.stream is not None and out.device != name:
                self._close(out)
        if self.vin.stream is not None and self.vin.device != self._voice_device():
            self._close_in()
        self.voice_retry = 0.0

    # ---------- воспроизведение ----------
    def _toggle(self, sid):
        if sid not in self.paths:
            return
        for o in self.outs.values():
            if o.error and not o.error.startswith("Didn't open"):
                o.error = None
        if any(sid in o.playing() for o in self.outs.values()):
            for o in self.outs.values():
                with o.lock:
                    o.voices = [v for v in o.voices if v.sid != sid]
            return
        if self.cfg.get("padSolo"):
            self._stop_all(keep_streams=True)
        started = False
        for role in ("me", "mic"):
            out = self._ensure_out(role)
            if not out:
                continue
            try:
                entry = self._load(sid, out.rate)
            except Exception as e:
                log.info("Не читается %s: %s", sid, e)
                out.error, entry = f"Не читается: {Path(sid).name}", None
            if not entry:
                continue
            _, data, norm = entry
            gain = norm if self.cfg.get("padNorm", True) else 1.0
            with out.lock:
                out.voices.append(Voice(sid, data, gain))
            started = True
        if started:
            self.last_sound = time.monotonic()
        elif not any(o.error for o in self.outs.values()):
            self.outs["me"].error = "Не выбрано ни одного выхода"

    def _stop_all(self, keep_streams=True):
        for o in self.outs.values():
            with o.lock:
                o.voices = []

    def _idle(self):
        busy = any(o.playing() for o in self.outs.values())
        if busy:
            self.last_sound = time.monotonic()
        elif time.monotonic() - self.last_sound > IDLE_CLOSE:
            voice = self._voice_wanted()
            for role, o in self.outs.items():
                if o.stream is not None and not (role == "mic" and voice):
                    self._close(o)

    # ---------- в клиент ----------
    def _push_list(self):
        self._push({"t": "padList", "status": self.status, "sounds": self.sounds,
                    "devices": self.devices, "default": self.default, "inputs": self.inputs,
                    "folder": str(SOUNDS)})

    def _push_state(self):
        playing = {}
        for o in self.outs.values():
            for sid, pos in o.playing().items():
                playing[sid] = round(max(pos, playing.get(sid, 0.0)), 2)
        errors = {r: o.error for r, o in self.outs.items() if o.error}
        if self.vin.error:
            errors["voice"] = self.vin.error
        state = {"t": "padState", "playing": playing, "errors": errors}
        if state != self.last_state or playing:
            self.last_state = state
            self._push(state)


def pick_files():
    """Окно выбора звуков на ПК (блокирует — вызывать в отдельном потоке). Копирует в папку sounds."""
    if sys.platform == "win32":
        from apps import ask_files
    else:
        from lx_apps import ask_files
    files = ask_files(i18n.tr("Noryx — choose sounds", "Noryx — обери звуки"),
                      [(i18n.tr("Sounds", "Звуки"), ";".join("*" + e for e in sorted(EXTS))),
                       (i18n.tr("All files", "Усі файли"), "*.*")], multi=True)
    added = []
    for f in files or ():
        src = Path(f)
        if src.suffix.lower() not in EXTS:
            continue
        try:
            if src.resolve().parent == SOUNDS.resolve():
                continue
        except OSError:
            pass
        dst = SOUNDS / src.name
        n = 2
        while dst.exists():
            dst = SOUNDS / f"{src.stem} ({n}){src.suffix}"
            n += 1
        shutil.copy2(src, dst)
        added.append(display_name(dst.stem))
    return added


def open_folder(path=None):
    """Открыть папку на ПК (по умолчанию — папку звуков; ещё — папку плагинов)."""
    path = path or SOUNDS
    path.mkdir(exist_ok=True)
    if sys.platform == "win32":
        os.startfile(str(path))
    else:
        import lx_util
        lx_util.open_path(path)
