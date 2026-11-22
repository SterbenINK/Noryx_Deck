"""Собирает Noryx.zip — переносную версию дека для друга.

Внутри только то, что нужно для запуска на чужом ПК: сервер, клиент, APK, скетч лампы,
лаунчеры. Без личного: android/ (ключ подписи), data/ (конфиг, иконки), logs/, .venv/, sounds/.
Файлы лежат в корне архива: «Извлечь все…» в Проводнике сразу даёт папку Noryx.
"""
import json
import sys
import time
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent         # packaging/windows: лаунчеры и README переносной версии
DECK = HERE.parent.parent                       # корень репозитория: server/, client/, Noryx.apk…
EXTRA = HERE
OUT = Path(sys.argv[1]) if len(sys.argv) > 1 else DECK / "dist" / "Noryx.zip"
sys.path.insert(0, str(DECK / "server"))
import update  # noqa: E402  (только стандартная библиотека)

# версия сборки (коммит git или то, что передало обновление) — по ней дек понимает, есть ли новее на GitHub
VERSION = update.build_version(DECK)

# все модули сервера, кроме Linux-версий (lx_*.py): новый общий модуль попадёт в архив сам
SERVER = sorted(p.name for p in (DECK / "server").glob("*.py") if not p.name.startswith("lx_"))
assert {"deck.py", "store.py", "keys.py", "tray.py", "script.py", "update.py"} <= set(SERVER), SERVER

PLUGINS = ["_example.py", "_vintage_demo.py", "README.txt", "README_UA.txt"]
FILES = [  # (путь в архиве, источник, вид)
    ("Start.bat", EXTRA / "Start.bat", "bat"),
    ("Stop.bat", EXTRA / "Stop.bat", "bat"),
    ("Firewall.bat", EXTRA / "Firewall.bat", "bat"),
    ("Phone.bat", DECK / "phone.bat", "bat"),          # телефон-киоск (server/phone.py)
    ("README.txt", EXTRA / "README.txt", "txt-bom"),
    ("README_UA.txt", EXTRA / "README_UA.txt", "txt-bom"),
    ("Noryx.apk", DECK / "Noryx.apk", "bin"),
    ("requirements.txt", DECK / "requirements.txt", "txt"),
    ("server/setup.bat", EXTRA / "server" / "setup.bat", "bat"),
    *[(f"server/{n}", DECK / "server" / n, "bin") for n in SERVER],
    ("client/index.html", DECK / "client" / "index.html", "bin"),
    # плагины: встроенная лампа, образец и описание (свои плагины пользователя обновление не трогает)
    *[(f"plugins/{n}", DECK / "plugins" / n, "txt-bom" if n.endswith(".txt") else "bin") for n in PLUGINS],
]
DIRS = ["sounds/"]   # пустая папка для звуков


def crlf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")


def build():
    stamp = time.localtime()[:6]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for d in DIRS:
            zi = zipfile.ZipInfo(d, stamp)
            zi.external_attr = (0o40755 << 16) | 0x10
            z.writestr(zi, b"")
        for arc, src, kind in FILES:
            data = src.read_bytes()
            if kind == "bat":
                assert all(b < 128 for b in data), f"{arc}: в .bat только ASCII"
                data = crlf(data)
            elif kind in ("txt", "txt-bom"):
                data = crlf(data)
                if kind == "txt-bom" and not data.startswith(b"\xef\xbb\xbf"):
                    data = b"\xef\xbb\xbf" + data
            zi = zipfile.ZipInfo(arc, stamp)
            zi.external_attr = 0o100644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, data)
        if VERSION:
            zi = zipfile.ZipInfo("server/version.json", stamp)
            zi.external_attr = 0o100644 << 16
            zi.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(zi, json.dumps(VERSION, ensure_ascii=False, indent=1).encode("utf-8"))
    tmp.replace(OUT)
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
        bad = [n for n in names if n.startswith(("android", "data/", "logs/", ".venv", "update/")) or "__pycache__" in n
               or n.endswith((".jks", ".log")) or (n.endswith(".json") and n != "server/version.json")]
        assert not bad, bad
        assert z.testzip() is None
    print(f"{OUT}  {OUT.stat().st_size} байт, файлов: {len(names)}, версия: "
          + (f"{VERSION['sha'][:7]}{' (+правки)' if VERSION.get('dirty') else ''}" if VERSION else "неизвестна"))
    for n in names:
        print("  ", n)


if __name__ == "__main__":
    build()
