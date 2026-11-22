"""Нагрузка и температуры: psutil + LibreHardwareMonitor (его веб-сервер на 127.0.0.1:8085).

Датчики узнаём по SensorId из data.json LHM (например /intelcpu/0/temperature/6,
/gpu-amd/0/load/0), а числа берём из RawValue — без разбора строк вида «45,0 °C».
"""
import json
import logging
import re
import time
import urllib.request

import psutil

log = logging.getLogger("sensors")
LHM_URL = "http://127.0.0.1:8085/data.json"
RETRY_AFTER = 15          # если LHM не отвечает — не долбим его каждые 1.5 с
_num = re.compile(r"-?\d+(?:[.,]\d+)?")
_down_until = 0.0
_warned = False

psutil.cpu_percent(None)  # первый вызов всегда 0 — «прогреваем»

CPU_PREFIXES = ("/intelcpu/", "/amdcpu/", "/cpu/")
GPU_PREFIXES = ("/gpu-amd/", "/gpu-nvidia/", "/atigpu/", "/nvidiagpu/")   # дискретные; /gpu-intel/ — встройка, мимо
CPU_TEMP_ORDER = ["cpu package", "core (tctl/tdie)", "core (tctl)", "core average", "core max", "cpu cores"]


def _num_of(leaf):
    raw = leaf.get("RawValue")
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _num.search(str(leaf.get("Value") or ""))
    return float(m.group(0).replace(",", ".")) if m else None


def _leaves(node):
    kids = node.get("Children") or []
    if not kids:
        if node.get("SensorId"):
            yield node
        return
    for k in kids:
        yield from _leaves(k)


def _lhm():
    global _down_until, _warned
    if time.monotonic() < _down_until:
        return {}
    try:
        with urllib.request.urlopen(LHM_URL, timeout=1.5) as r:
            tree = json.loads(r.read().decode("utf-8", "replace"))
    except Exception as e:
        _down_until = time.monotonic() + RETRY_AFTER
        if not _warned:
            log.warning("LibreHardwareMonitor не отвечает на %s (%s) — температур не будет, "
                        "проверю ещё раз через %d с", LHM_URL, e, RETRY_AFTER)
            _warned = True
        return {}
    if _warned:
        log.info("LibreHardwareMonitor на связи — температуры есть")
        _warned = False
    res, cpu_rank = {}, None
    for leaf in _leaves(tree):
        sid = (leaf.get("SensorId") or "").lower()
        typ = (leaf.get("Type") or "").lower()
        name = (leaf.get("Text") or "").lower()
        v = _num_of(leaf)
        if v is None:
            continue
        if sid.startswith(CPU_PREFIXES):
            if typ == "temperature" and name in CPU_TEMP_ORDER:
                rank = CPU_TEMP_ORDER.index(name)
                if cpu_rank is None or rank < cpu_rank:
                    res["cpuT"], cpu_rank = v, rank
            elif typ == "load" and name == "cpu total":
                res["cpu"] = v
        elif sid.startswith(GPU_PREFIXES):
            if typ == "load" and name in ("gpu core", "d3d 3d") and "gpu" not in res:
                res["gpu"] = v
            elif typ == "temperature" and name == "gpu core":
                res["gpuT"] = v
    return res


def read():
    """Блокирующая функция — вызывать через run_in_executor."""
    out = {"cpu": psutil.cpu_percent(None), "ram": psutil.virtual_memory().percent,
           "gpu": None, "cpuT": None, "gpuT": None}
    out.update({k: v for k, v in _lhm().items() if v is not None})
    return {k: (round(v) if isinstance(v, (int, float)) else v) for k, v in out.items()}
