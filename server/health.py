"""Состояние самого дека (вкладка «Дек» в окне «Диагностика»): сколько работает, сколько ест процесс и каждый
поток по имени (плагины держат свои потоки: «screen2», «ddc», «satisfactory»…), время обработки сообщений
с телефонов, задержка цикла событий, история ошибок (WARNING и выше из лога), отчёт для разработчика (zip).
Только стандартная библиотека и psutil (он и так есть)."""
import collections
import io
import json
import logging
import os
import platform
import sys
import threading
import time
import zipfile
from pathlib import Path

import psutil

ERRORS_KEEP = 120
SLOW_MS = 250             # сообщение дольше — в лог (обработчик держит весь дек)
SECRET_KEYS = {"pluginCfg", "token", "secret", "key", "keys", "password"}


class ErrorTap(logging.Handler):
    """Предупреждения и ошибки из лога — в ленту (последние ERRORS_KEEP)."""

    def __init__(self, keep):
        super().__init__(level=logging.WARNING)
        self.keep = keep

    def emit(self, rec):
        try:
            msg = rec.getMessage()
        except Exception:
            msg = str(rec.msg)
        self.keep.append({"ts": rec.created, "level": rec.levelname, "src": rec.name, "msg": msg[:500]})


class Health:
    def __init__(self):
        self.t0 = time.time()
        self.msgs = {}                                     # вид → [сколько, всего мс, самое долгое мс]
        self.errors = collections.deque(maxlen=ERRORS_KEEP)
        self.lag = {"now": 0.0, "max": 0.0}               # задержка цикла событий, мс
        self.tap = ErrorTap(self.errors)
        self.proc = psutil.Process(os.getpid())
        self.cpu_prev = None                               # (время, {поток: время ЦП})
        try:
            self.proc.cpu_percent(None)
        except Exception:
            pass

    def attach(self, logger=None):
        (logger or logging.getLogger()).addHandler(self.tap)

    # ---------- сообщения и цикл событий ----------
    def took(self, kind, ms):
        s = self.msgs.setdefault(str(kind)[:40], [0, 0.0, 0.0])
        s[0] += 1
        s[1] += ms
        s[2] = max(s[2], ms)

    async def watch_loop(self, asyncio):
        """Раз в секунду: насколько позже проснулись, чем просили — столько дек был занят чем-то долгим."""
        while True:
            t = time.monotonic()
            await asyncio.sleep(1.0)
            lag = max(0.0, (time.monotonic() - t - 1.0) * 1000)
            self.lag["now"] = round(lag, 1)
            self.lag["max"] = round(max(self.lag["max"], lag), 1)

    # ---------- процесс и потоки ----------
    def threads(self):
        """ЦП по потокам (по имени Python-потока; одинаковые имена — вместе), % одного ядра за прошлый замер."""
        names = {t.native_id: t.name for t in threading.enumerate() if getattr(t, "native_id", None)}
        now, cur = time.monotonic(), {}
        try:
            for th in self.proc.threads():
                n = names.get(th.id, "")
                n = n if n else "native"
                n = "deck" if n == "MainThread" else n.split("-")[0] if n.startswith(("ThreadPoolExecutor", "asyncio")) else n
                cur[n] = cur.get(n, 0.0) + th.user_time + th.system_time
        except Exception:
            return []
        out = []
        prev = self.cpu_prev
        dt = max(0.2, now - prev[0]) if prev else 1.0
        for n, t in cur.items():                       # первый замер — ещё не с чем сравнить: 0 %, но список уже есть
            out.append({"name": n, "cpu": round(max(0.0, t - prev[1].get(n, t)) / dt * 100, 1) if prev else 0.0, "total": round(t, 1)})
        self.cpu_prev = (now, cur)
        return sorted(out, key=lambda x: (-x["cpu"], -x["total"]))[:14]

    def process(self):
        p = self.proc
        out = {"pid": p.pid, "cpu": 0.0, "rss": 0, "threads": 0, "handles": None}
        try:
            with p.oneshot():
                out["cpu"] = round(p.cpu_percent(None) / (psutil.cpu_count() or 1), 1)
                out["rss"] = p.memory_info().rss
                out["threads"] = p.num_threads()
                out["handles"] = p.num_handles() if hasattr(p, "num_handles") else p.num_fds()
        except Exception:
            pass
        return out

    def snapshot(self, deck=None, extra=None):
        msgs = sorted(({"t": k, "n": v[0], "avg": round(v[1] / v[0], 1) if v[0] else 0, "max": round(v[2], 1)}
                       for k, v in self.msgs.items()), key=lambda x: -x["max"])[:16]
        out = {"t": "health", "up": round(time.time() - self.t0), "started": self.t0, "proc": self.process(),
               "threads": self.threads(), "msgs": msgs, "lag": dict(self.lag),
               "errors": list(self.errors)[-40:][::-1], "py": platform.python_version(), "os": f"{platform.system()} {platform.release()}"}
        if extra:
            out.update(extra)
        return out


def redact(v):
    """Конфиг для отчёта: без настроек плагинов, токенов, ключей, путей к личным файлам — только форма."""
    if isinstance(v, dict):
        return {k: ("…" if k in SECRET_KEYS or any(s in k.lower() for s in ("token", "secret", "pass")) else redact(x))
                for k, x in v.items()}
    if isinstance(v, list):
        return [redact(x) for x in v[:50]]
    return v


def tail(path, limit=400_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            n = f.tell()
            f.seek(max(0, n - limit))
            return f.read().decode("utf-8", "replace")
    except OSError:
        return ""


def report(root, snap, cfg, version):
    """Отчёт для разработчика: logs/reports/deck-report-<дата>.zip — состояние, конец логов, конфиг без секретов."""
    folder = Path(root) / "logs" / "reports"
    folder.mkdir(parents=True, exist_ok=True)
    name = time.strftime("deck-report-%Y%m%d-%H%M%S.zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("state.json", json.dumps(dict(snap, version=version, argv=sys.argv[:3]), ensure_ascii=False, indent=1, default=str))
        z.writestr("config.json", json.dumps(redact(cfg), ensure_ascii=False, indent=1, default=str))
        for log in ("server.log", "update.log"):
            z.writestr(log, tail(Path(root) / "logs" / log))
    (folder / name).write_bytes(buf.getvalue())
    for old in sorted(folder.glob("deck-report-*.zip"))[:-10]:        # храним 10 последних
        try:
            old.unlink()
        except OSError:
            pass
    return folder / name
