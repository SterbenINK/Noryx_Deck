"""Визуализатор звука в шапке дека: что играет на ПК (захват выхода — audio.Loopback / lx_audio.Loopback) →
спектр по полосам → телефонам до 30 раз в секунду. Цвета полос телефон берёт из обложки трека сам.

Захват идёт, только пока шапку кто-то видит: телефоны просят {"t":"viz","on":true/false} (экран погас, открыли
микшер, правка — «off»). Последний ушёл — захват держится ещё LINGER секунд и закрывается.
Кадр — строка, одна буква на полосу: 64 уровня алфавитом ALPHABET (меньше трафика, чем массив чисел).
Тишина — один нулевой кадр, дальше ничего не шлём, пока звук не вернётся.
"""
import asyncio
import logging
import threading
import time

log = logging.getLogger("viz")

BANDS = 48
FPS = 30
N = 2048                  # окно БПФ: при 48 кГц — 43 мс, шаг частот 23 Гц
FMIN, FMAX = 40.0, 16000.0
TILT = 3.0                # дБ на октаву: в музыке верхов меньше — выравниваем, иначе правая половина лежит
RANGE = 42.0              # сколько дБ ниже опорного уровня — уже ноль
FLOOR = -50.0             # опорный уровень не ниже: тихий шум не раздувается на всю высоту
FALL = 1.5                # опорный уровень сползает, дБ в секунду (стало тише — шапка подстраивается)
SILENT = -72.0            # громче нет ни одной полосы — тишина
STALE = 0.25              # столько секунд без новых сэмплов — тоже тишина (WASAPI в тишине ничего не шлёт)
LINGER = 3.0
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
ZERO = ALPHABET[0] * BANDS


def encode(levels):
    return "".join(ALPHABET[min(63, max(0, int(v * 63 + 0.5)))] for v in levels)


class Spectrum:
    """Последние N сэмплов → BANDS полос (FMIN…FMAX, по логарифму частоты), 0…1, с автоусилением:
    громко или тихо на ПК — шапка одинаково живая."""

    def __init__(self, bands=BANDS):
        import numpy as np
        self.np = np
        self.bands = bands
        self.win = np.hanning(N).astype(np.float32)
        self.ref = FLOOR
        self.rate = None

    def _layout(self, rate):
        np = self.np
        self.freqs = np.fft.rfftfreq(N, 1 / rate)
        edges = np.geomspace(FMIN, min(FMAX, rate / 2 * 0.92), self.bands + 1)
        self.centers = np.sqrt(edges[:-1] * edges[1:])
        self.lo = np.searchsorted(self.freqs, edges[:-1])
        self.hi = np.minimum(np.searchsorted(self.freqs, edges[1:]), len(self.freqs))
        self.tilt = TILT * np.log2(self.centers / 1000.0)
        self.rate = rate

    def __call__(self, x, rate, dt=1 / FPS):
        """x — N последних сэмплов (моно). → (полосы 0…1, самая громкая полоса в дБ)."""
        np = self.np
        if rate != self.rate:
            self._layout(rate)
        mag = np.abs(np.fft.rfft(x * self.win)) / (N / 4)          # синус в полную шкалу → 1.0 (0 дБ)
        vals = np.empty(self.bands, np.float32)
        for i in range(self.bands):
            lo, hi = self.lo[i], self.hi[i]
            # низкие полосы уже одного бина — значение между соседними бинами; остальные — самый громкий бин
            vals[i] = mag[lo:hi].max() if hi - lo >= 2 else np.interp(self.centers[i], self.freqs, mag)
        db = 20 * np.log10(vals + 1e-7) + self.tilt
        top = float(db.max())
        self.ref = max(FLOOR, top, self.ref - FALL * dt)
        lv = np.clip((db - (self.ref - RANGE)) / RANGE, 0, 1) ** 1.6     # степень — пики выразительнее
        return lv, top


class Viz:
    """Захват + спектр + рассылка подписчикам. opener(callback) → захват с .close() (audio.Loopback);
    send(frame) — корутина, разослать кадр (строку) тем, кто подписан (subs)."""

    def __init__(self, opener, send):
        self.opener, self.send = opener, send
        self.subs = set()
        self.task = None
        self.cap = None
        self.reopen = False
        self.lock = threading.Lock()
        self.ring = None
        self.rate = 48000
        self.fresh = 0.0
        self.error = None

    # из потока захвата
    def feed(self, block, rate):
        np = self.np
        with self.lock:
            if rate != self.rate:
                self.ring[:] = 0
                self.rate = rate
            n = len(block)
            if n >= len(self.ring):
                self.ring[:] = block[-len(self.ring):]
            elif n:
                self.ring = np.roll(self.ring, -n)
                self.ring[-n:] = block
            self.fresh = time.monotonic()

    def want(self, who, on):
        (self.subs.add if on else self.subs.discard)(who)
        if self.subs and (self.task is None or self.task.done()):
            self.task = asyncio.ensure_future(self.run())

    def restart(self):
        """Выход по умолчанию сменился — захват заново (на Linux монитор сам за ним не переходит)."""
        if self.cap is not None:
            self.reopen = True

    async def run(self):
        import numpy as np
        self.np = np
        loop = asyncio.get_running_loop()
        self.ring = np.zeros(N * 2, np.float32)
        spec = Spectrum()
        quiet, empty_since, retry_at = False, None, 0.0
        try:
            while True:
                now = time.monotonic()
                if not self.subs:
                    empty_since = empty_since or now
                    if now - empty_since > LINGER:
                        break
                else:
                    empty_since = None
                if self.reopen and self.cap is not None:
                    self.reopen = False
                    cap, self.cap = self.cap, None
                    await loop.run_in_executor(None, cap.close)
                    retry_at = 0.0
                if self.cap is None and now >= retry_at:
                    try:
                        self.cap = await loop.run_in_executor(None, self.opener, self.feed)
                        self.error = None
                        log.debug("Визуализатор: захват включён")
                    except Exception as e:
                        text = f"{type(e).__name__}: {e}"
                        if text != self.error:          # нет звука на ПК — пишем один раз, пробуем раз в 5 с
                            log.warning("Визуализатор: нет захвата звука (%s)", text)
                        self.error = text
                        retry_at = now + 5
                await asyncio.sleep(1 / FPS)
                with self.lock:
                    x, rate, fresh = self.ring[-N:].copy(), self.rate, self.fresh
                frame = ZERO
                if self.cap is not None and time.monotonic() - fresh < STALE:
                    levels, top = spec(x, rate)
                    if top >= SILENT:
                        frame = encode(levels)
                if frame == ZERO:                     # тишина (или всё ниже первой ступеньки) — один раз
                    if not quiet:
                        quiet = True
                        await self.send(ZERO)
                    continue
                quiet = False
                await self.send(frame)
        except Exception as e:
            log.warning("Визуализатор: %s", e)
        finally:
            cap, self.cap = self.cap, None
            if cap is not None:
                await loop.run_in_executor(None, cap.close)
            log.debug("Визуализатор: захват выключен")
            self.task = None
            if self.subs:                        # пока закрывали, снова попросили — заново
                self.task = asyncio.ensure_future(self.run())
