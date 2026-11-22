"""Устройства ПК на Linux для окна «Устройства» (пара devices.py): мониторы (kscreen-doctor на KDE, иначе
/sys/class/drm), USB (/sys/bus/usb), геймпады (/proc/bus/input/devices, у кого есть js-обработчик)."""
import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger("devices")


def monitors():
    if shutil.which("kscreen-doctor"):
        try:
            r = subprocess.run(["kscreen-doctor", "-j"], capture_output=True, timeout=5)
            data = json.loads(r.stdout or b"{}")
            out = []
            for o in data.get("outputs", []):
                if not o.get("connected") or not o.get("enabled"):
                    continue
                mode = next((m for m in o.get("modes", []) if str(m.get("id")) == str(o.get("currentModeId"))), {})
                size = mode.get("size") or {}
                out.append({"name": o.get("name"), "w": size.get("width"), "h": size.get("height"),
                            "hz": round(mode.get("refreshRate") or 0), "x": (o.get("pos") or {}).get("x"),
                            "y": (o.get("pos") or {}).get("y"), "primary": bool(o.get("priority") == 1 or o.get("primary"))})
            if out:
                return out
        except Exception as e:
            log.debug("kscreen-doctor: %s", e)
    out = []
    for st in sorted(Path("/sys/class/drm").glob("card*-*/status")):
        try:
            if st.read_text().strip() != "connected":
                continue
            modes = (st.parent / "modes").read_text().split()
            w, h = (modes[0].split("x") + ["", ""])[:2] if modes else ("", "")
            out.append({"name": st.parent.name.split("-", 1)[1], "w": int(w) if w.isdigit() else None,
                        "h": int(re.sub(r"\D.*", "", h)) if h[:1].isdigit() else None, "hz": None, "primary": False})
        except OSError:
            continue
    return out


def usb():
    out, seen = [], set()
    for d in sorted(Path("/sys/bus/usb/devices").glob("*")):
        try:
            prod = (d / "product").read_text().strip()
        except OSError:
            continue
        maker = ""
        try:
            maker = (d / "manufacturer").read_text().strip()
        except OSError:
            pass
        if not prod or "Linux Foundation" in maker or prod.lower().endswith("host controller"):
            continue
        name = f"{maker} {prod}".strip() if maker and maker not in prod else prod
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "kind": "", "ok": True})
    return out


def pads():
    out = []
    try:
        blocks = Path("/proc/bus/input/devices").read_text(errors="replace").split("\n\n")
    except OSError:
        return out
    for b in blocks:
        h = re.search(r"^H: Handlers=(.*)$", b, re.M)
        n = re.search(r'^N: Name="(.*)"$', b, re.M)
        if h and n and re.search(r"\bjs\d+", h.group(1)):
            out.append({"name": n.group(1), "battery": None, "wired": None})
    return out


def snapshot():
    out = {"monitors": [], "usb": [], "pads": []}
    for k, fn in (("monitors", monitors), ("usb", usb), ("pads", pads)):
        try:
            out[k] = fn()
        except Exception as e:
            log.debug("%s: %s", k, e)
    return out
