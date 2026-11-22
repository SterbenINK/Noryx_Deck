"""Собирает Noryx-linux.tar.gz — Linux-версию дека для друга.

Внутри папка Noryx/: сервер (общие модули + lx_*), клиент, APK, скетч лампы, скрипты.
Без Windows-модулей и без личного: android/ (ключ подписи), data/, logs/, .venv/, sounds/.
"""
import io
import json
import sys
import tarfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent         # packaging/linux: start.sh, README и т. п.
DECK = HERE.parent.parent                       # корень репозитория
EXTRA = HERE
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else DECK / "dist" / "Noryx-linux.tar.gz"
TOP = "Noryx"
sys.path.insert(0, str(DECK / "server"))
import update  # noqa: E402  (только стандартная библиотека)

# версия сборки (коммит git или то, что передало обновление) — по ней дек понимает, есть ли новее на GitHub
VERSION = update.build_version(DECK)

# модули только для Windows — их Linux-пары лежат в lx_*.py; остальное (общее и lx_*) — в архив
WINDOWS_ONLY = {"apps.py", "audio.py", "clip.py", "devices.py", "keys.py", "lnk.py", "media.py", "sensors.py", "tray.py"}
SERVER = sorted(p.name for p in (DECK / "server").glob("*.py") if p.name not in WINDOWS_ONLY)
assert {"deck.py", "store.py", "lx_keys.py", "lx_tray.py", "script.py", "update.py"} <= set(SERVER), SERVER
FILES = [  # (путь в архиве, источник, права)
    ("start.sh", EXTRA / "start.sh", 0o755),
    ("stop.sh", EXTRA / "stop.sh", 0o755),
    ("system-setup.sh", EXTRA / "system-setup.sh", 0o755),
    ("phone.sh", DECK / "phone.sh", 0o755),            # телефон-киоск (server/phone.py)
    ("README.txt", EXTRA / "README.txt", 0o644),
    ("README_UA.txt", EXTRA / "README_UA.txt", 0o644),
    ("requirements-linux.txt", EXTRA / "requirements-linux.txt", 0o644),
    ("Noryx.apk", DECK / "Noryx.apk", 0o644),
    *[(f"server/{n}", DECK / "server" / n, 0o644) for n in SERVER],
    ("client/index.html", DECK / "client" / "index.html", 0o644),
    # плагины: встроенная лампа, образец и описание (свои плагины пользователя обновление не трогает)
    *[(f"plugins/{n}", DECK / "plugins" / n, 0o644) for n in ("_example.py", "_vintage_demo.py", "README.txt", "README_UA.txt")],
]
DIRS = ["", "server", "client", "sounds", "plugins"]


def build():
    now = int(time.time())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with tarfile.open(tmp, "w:gz", compresslevel=9, format=tarfile.PAX_FORMAT) as tar:
        def add(name, data, mode, kind=tarfile.REGTYPE):
            ti = tarfile.TarInfo(f"{TOP}/{name}".rstrip("/"))
            ti.type, ti.mode, ti.mtime = kind, mode, now
            ti.uid = ti.gid = 1000
            ti.uname = ti.gname = "deck"
            if kind == tarfile.DIRTYPE:
                tar.addfile(ti)
            else:
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
        for d in DIRS:
            add(d, b"", 0o755, tarfile.DIRTYPE)
        for arc, src, mode in FILES:
            data = src.read_bytes()
            if not arc.endswith(".apk"):
                data = data.replace(b"\r\n", b"\n")          # в Linux-архиве — переводы строк Unix
            if arc.endswith(".sh"):
                assert data.startswith(b"#!/usr/bin/env bash\n"), arc
            add(arc, data, mode)
        if VERSION:
            add("server/version.json", json.dumps(VERSION, ensure_ascii=False, indent=1).encode("utf-8"), 0o644)
    tmp.replace(OUT)
    with tarfile.open(OUT) as tar:
        names = tar.getnames()
        bad = [n for n in names if any(x in n for x in ("android", "/data/", "/logs/", "/update/", ".venv",
                                                        "__pycache__", ".jks"))]
        assert not bad, bad
        win = [n for n in names if n.endswith(("/apps.py", "/audio.py", "/keys.py", "/media.py", "/sensors.py",
                                               "/tray.py", "/lnk.py", ".bat"))]
        assert not win, win
    print(f"{OUT}  {OUT.stat().st_size} байт, записей: {len(names)}, версия: "
          + (f"{VERSION['sha'][:7]}{' (+правки)' if VERSION.get('dirty') else ''}" if VERSION else "неизвестна"))


if __name__ == "__main__":
    build()
