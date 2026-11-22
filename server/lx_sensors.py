"""Нагрузка и температуры на Linux: psutil + датчики ядра (hwmon), видеокарта AMD — из sysfs,
NVIDIA — через nvidia-smi. Ничего ставить не нужно (в отличие от LibreHardwareMonitor в Windows)."""
import glob
import logging
import os
import shutil
import subprocess
import time

import psutil

log = logging.getLogger("sensors")

SYS = "/sys"                    # подменяется в тестах
CPU_CHIPS = ("k10temp", "zenpower", "coretemp", "cpu_thermal", "acpitz")
CPU_LABELS = ("tctl", "tdie", "package id 0", "cpu")

psutil.cpu_percent(None)        # первый вызов всегда 0 — «прогреваем»
_gpu_dir = None
_gpu_checked = None
_cpu_file = None                # файл температуры ЦП (hwmon), найден один раз — _find_cpu_temp
_cpu_checked = None
_nv = {"t": float("-inf"), "v": (None, None)}


def _read(path):
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None


def _find_cpu_temp():
    """Файл температуры ЦП в hwmon — тот же выбор, что был через psutil.sensors_temperatures(): чип по CPU_CHIPS, датчик
    по CPU_LABELS, иначе первый у чипа. Ищем один раз: обход всех датчиков стоил ~10 мс на каждый опрос."""
    chips = {}
    for hw in sorted(glob.glob(os.path.join(SYS, "class", "hwmon", "hwmon*"))):
        name = (_read(os.path.join(hw, "name")) or "").lower()
        if name in CPU_CHIPS and name not in chips:
            files = sorted(glob.glob(os.path.join(hw, "temp*_input")),
                           key=lambda f: int("".join(ch for ch in os.path.basename(f) if ch.isdigit()) or 0))
            chips[name] = [((_read(f[:-len("_input")] + "_label") or "").lower(), f) for f in files]
    for chip in CPU_CHIPS:
        entries = chips.get(chip) or []
        for want in CPU_LABELS:
            for label, f in entries:
                if label == want:
                    return f
        if entries:
            return entries[0][1]
    return None


def _cpu_temp():
    global _cpu_file, _cpu_checked
    if _cpu_file is None and (_cpu_checked is None or time.monotonic() - _cpu_checked >= 60):
        _cpu_checked = time.monotonic()
        _cpu_file = _find_cpu_temp()
    if _cpu_file:
        v = _read(_cpu_file)
        if v and v.lstrip("-").isdigit():
            return int(v) / 1000
        _cpu_file = None                     # датчик пропал (выгрузили модуль) — поищем снова через минуту
    return None


def _amd_gpu():
    """Папка устройства самой «большой» видеокарты AMD (по объёму видеопамяти)."""
    global _gpu_dir, _gpu_checked
    if _gpu_dir is not None or (_gpu_checked is not None and time.monotonic() - _gpu_checked < 60):
        return _gpu_dir
    _gpu_checked = time.monotonic()
    best = None
    for dev in glob.glob(os.path.join(SYS, "class", "drm", "card[0-9]*", "device")):
        if _read(os.path.join(dev, "vendor")) != "0x1002" or _read(os.path.join(dev, "gpu_busy_percent")) is None:
            continue
        vram = int(_read(os.path.join(dev, "mem_info_vram_total")) or 0)
        if best is None or vram > best[0]:
            best = (vram, dev)
    _gpu_dir = best[1] if best else None
    if _gpu_dir:
        log.info("Видеокарта AMD: %s", os.path.realpath(_gpu_dir))
    return _gpu_dir


def _amd_temp(dev):
    for hw in sorted(glob.glob(os.path.join(dev, "hwmon", "hwmon*"))):
        temps = {}
        for f in glob.glob(os.path.join(hw, "temp*_input")):
            label = (_read(f.replace("_input", "_label")) or "").lower()
            v = _read(f)
            if v and v.lstrip("-").isdigit():
                temps[label] = int(v) / 1000
        for want in ("edge", "junction", ""):
            if want in temps:
                return temps[want]
        if temps:
            return next(iter(temps.values()))
    return None


def _nvidia():
    """(загрузка, температура) первой видеокарты NVIDIA; не чаще раза в 1.5 с."""
    if time.monotonic() - _nv["t"] < 1.5:
        return _nv["v"]
    _nv["t"] = time.monotonic()
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,temperature.gpu",
                            "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=3)
        load, temp = (float(x) for x in r.stdout.splitlines()[0].split(","))
        _nv["v"] = (load, temp)
    except Exception:
        _nv["v"] = (None, None)
    return _nv["v"]


def read():
    """Блокирующая функция — вызывать через run_in_executor."""
    out = {"cpu": psutil.cpu_percent(None), "ram": psutil.virtual_memory().percent,
           "gpu": None, "cpuT": _cpu_temp(), "gpuT": None}
    dev = _amd_gpu()
    if dev:
        busy = _read(os.path.join(dev, "gpu_busy_percent"))
        out["gpu"] = int(busy) if busy and busy.isdigit() else None
        out["gpuT"] = _amd_temp(dev)
    elif shutil.which("nvidia-smi"):
        out["gpu"], out["gpuT"] = _nvidia()
    return {k: (round(v) if isinstance(v, (int, float)) else v) for k, v in out.items()}
