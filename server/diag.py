"""Подробная диагностика ПК для окна «Диагностика» на телефоне (общая для Windows и Linux, psutil):
память и файл подкачки, диски, сеть (скорость сейчас), программы, больше всех нагружающие ЦП и память.
Звук — у телефона и так есть (сообщение audio). Вызывать в отдельном потоке: обход процессов — десятки мс."""
import concurrent.futures
import logging
import os
import time

import psutil

SKIP_FS = {"squashfs", "tmpfs", "devtmpfs", "overlay", "proc", "sysfs", "efivarfs", "fuse.portal", "nsfs", "ramfs"}
log = logging.getLogger("diag")
POOL = concurrent.futures.ThreadPoolExecutor(max_workers=4, thread_name_prefix="diag-disk")
IDLE = {"system idle process", "idle", "system", "registry", "memory compression"}


def _swap_win():
    """Файл подкачки без счётчиков производительности: «предел выделения» минус ОЗУ — размер подкачки,
    занятое сверх ОЗУ — сколько в ней сейчас. Только Windows."""
    import ctypes
    from ctypes import wintypes as W

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", W.DWORD), ("dwMemoryLoad", W.DWORD), ("ullTotalPhys", ctypes.c_uint64),
                    ("ullAvailPhys", ctypes.c_uint64), ("ullTotalPageFile", ctypes.c_uint64), ("ullAvailPageFile", ctypes.c_uint64),
                    ("ullTotalVirtual", ctypes.c_uint64), ("ullAvailVirtual", ctypes.c_uint64), ("ullAvailExtendedVirtual", ctypes.c_uint64)]
    m = MS(dwLength=ctypes.sizeof(MS))
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m)):
        raise OSError("GlobalMemoryStatusEx")
    total = max(0, m.ullTotalPageFile - m.ullTotalPhys)
    used = max(0, (m.ullTotalPageFile - m.ullAvailPageFile) - (m.ullTotalPhys - m.ullAvailPhys))
    used = min(used, total)
    return {"total": total, "used": used, "pct": round(100 * used / total) if total else 0}


class Diag:
    def __init__(self):
        self.procs = {}           # pid → psutil.Process: cpu_percent считается между двумя вызовами
        self.net = None           # (байт принято, отправлено, время) прошлого замера
        self.cores = psutil.cpu_count() or 1

    def _disks(self):
        out, seen = [], set()
        try:                               # сам список дисков на Windows тоже может ждать отвалившийся сетевой диск
            parts = POOL.submit(psutil.disk_partitions, False).result(timeout=3)
        except concurrent.futures.TimeoutError:
            raise TimeoutError("список дисков не отвечает 3 с")
        for p in parts:
            opts = (p.opts or "").lower()
            # сетевой диск, который не отвечает, или привод без диска могут думать минутами — их не трогаем
            if p.fstype in SKIP_FS or "loop" in p.device or p.device in seen or "cdrom" in opts or "remote" in opts:
                continue
            if p.mountpoint.startswith(("/snap", "/boot", "/run", "/var/lib", "/sys", "/proc")):
                continue
            try:
                u = POOL.submit(psutil.disk_usage, p.mountpoint).result(timeout=1.5)
            except Exception:              # не ответил за 1,5 с, отключённая флешка — пропускаем
                continue
            if u.total < 1 << 30:
                continue
            seen.add(p.device)
            out.append({"name": p.mountpoint.rstrip("\\") or p.mountpoint, "total": u.total, "used": u.used, "free": u.free,
                        "pct": round(u.percent)})
        return out

    def _net(self):
        c = psutil.net_io_counters()
        now = time.monotonic()
        down = up = None
        if self.net:
            dt = max(0.2, now - self.net[2])
            down, up = (c.bytes_recv - self.net[0]) / dt, (c.bytes_sent - self.net[1]) / dt
        self.net = (c.bytes_recv, c.bytes_sent, now)
        return {"down": down, "up": up, "recv": c.bytes_recv, "sent": c.bytes_sent}

    def _top(self):
        groups, alive = {}, set()
        me = os.getpid()
        for p in psutil.process_iter():
            pid = p.pid
            if pid == me:
                continue
            alive.add(pid)
            q = self.procs.get(pid)
            if q is None:
                q = self.procs[pid] = p
                try:
                    q.cpu_percent(None)        # первый замер — ноль, считается со следующего
                except Exception:
                    pass
            try:
                name = q.name()
                if name.lower() in IDLE:
                    continue
                cpu = q.cpu_percent(None) / self.cores
                rss = q.memory_info().rss
            except Exception:
                continue
            g = groups.setdefault(name, {"name": name, "cpu": 0.0, "ram": 0, "n": 0})
            g["cpu"] += cpu
            g["ram"] += rss
            g["n"] += 1
        for pid in list(self.procs):
            if pid not in alive:
                del self.procs[pid]
        gs = list(groups.values())
        for g in gs:
            g["cpu"] = round(g["cpu"], 1)
        return (sorted(gs, key=lambda g: -g["cpu"])[:6], sorted(gs, key=lambda g: -g["ram"])[:6])

    def snapshot(self):
        """Каждая часть — сама по себе: что-то не получилось — остальное всё равно приходит на телефон."""
        out = {"t": "diag", "ram": {"total": 0, "used": 0, "pct": 0}, "swap": {"total": 0, "used": 0, "pct": 0},
               "disks": [], "net": {"down": None, "up": None, "recv": 0, "sent": 0}, "topCpu": [], "topRam": [],
               "cpuNow": 0, "procs": 0, "ts": time.time(), "errors": []}

        def part(name, fn):
            try:
                fn()
            except Exception as e:
                out["errors"].append(f"{name}: {type(e).__name__}: {e}")
                log.warning("Диагностика, %s: %s", name, e)

        def mem():
            vm = psutil.virtual_memory()
            out["ram"] = {"total": vm.total, "used": vm.total - vm.available, "pct": round(vm.percent)}

        def swap():
            try:
                sw = psutil.swap_memory()
                out["swap"] = {"total": sw.total, "used": sw.used, "pct": round(sw.percent)}
            except Exception:
                # на Windows psutil берёт подкачку из счётчиков производительности, а они бывают выключены
                # («PdhAddEnglishCounterW failed») — тогда считаем сами по GlobalMemoryStatusEx
                out["swap"] = _swap_win()

        def top():
            out["topCpu"], out["topRam"] = self._top()
            out["procs"] = len(self.procs)
        t0 = time.monotonic()
        part("memory", mem)
        part("swap", swap)
        part("disks", lambda: out.update(disks=self._disks()))
        part("net", lambda: out.update(net=self._net()))
        part("processes", top)
        part("cpu", lambda: out.update(cpuNow=psutil.cpu_percent(None)))
        out["took"] = round(time.monotonic() - t0, 2)
        if out["took"] > 5:
            log.warning("Диагностика: замер шёл %s с", out["took"])
        return out
