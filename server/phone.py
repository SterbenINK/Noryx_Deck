"""Телефон-киоск: настроить Android-телефон только под дек (по USB, через adb). Запуск — Phone.bat / phone.sh.

    python server/phone.py            — настроить: поставить Noryx.apk, убрать лишние приложения, сделать дек
                                        «владельцем устройства» (киоск: дек — рабочий стол, закреплён на экране,
                                        без шторки и экрана блокировки)
    python server/phone.py off | on   — выключить / включить киоск (дек остаётся владельцем)
    python server/phone.py release    — совсем отпустить телефон (дек можно удалить как обычное приложение)
    python server/phone.py restore    — вернуть убранные приложения
    python server/phone.py webview [файл.apk] — версия WebView на телефоне / поставить новую из файла
    --keep-apps                       — при настройке ничего не убирать

Приложения убираются только для пользователя (pm uninstall --user 0): система не трогается, вернуть — restore.
adb: из PATH, из папки platform-tools рядом с деком, иначе скачивается туда же с dl.google.com (официальный).
Только стандартная библиотека."""
import io
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import i18n

ROOT = Path(__file__).resolve().parent.parent
APK = ROOT / "Noryx.apk"
PKG = "local.claudedeck"
ADMIN = f"{PKG}/.DeckAdmin"
ACTIVITY = f"{PKG}/.MainActivity"
TOOLS = ROOT / "platform-tools"
TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-{}.zip"
REMOVED = ROOT / "data" / "phone-removed.json"     # что убрали (по серийному номеру) — чтобы вернуть

# Лишнее для дека. Только приложения поверх системы: не трогаем WebView / Chrome (на них работает дек),
# настройки, клавиатуру, рабочий стол Huawei (запасной, если киоск выключат), сервисы Google и HMS.
DEBLOAT = [
    # Huawei / EMUI
    "com.huawei.appmarket",              # AppGallery
    "com.huawei.android.hwouc",          # обновление системы (чтобы не обновилась сама и не сбросила настройки)
    "com.huawei.android.thememanager",   # темы
    "com.huawei.himovie.overseas", "com.huawei.himovie",
    "com.huawei.music", "com.android.mediacenter",
    "com.huawei.browser",
    "com.huawei.gameassistant",
    "com.huawei.intelligent",            # лента HiBoard
    "com.huawei.search",
    "com.huawei.tips",
    "com.huawei.phoneservice",           # «Поддержка»
    "com.huawei.health",
    "com.huawei.email", "com.android.email",
    "com.huawei.android.totemweather",
    "com.huawei.compass",
    "com.huawei.android.FMRadio",
    "com.huawei.android.mirrorshare",
    "com.huawei.screenrecorder",
    "com.huawei.notepad",
    "com.huawei.videoeditor",
    "com.huawei.parentcontrol",
    "com.android.soundrecorder",
    # Google — приложения (сами сервисы Google остаются)
    "com.google.android.youtube", "com.google.android.apps.youtube.music",
    "com.google.android.apps.maps", "com.google.android.gm", "com.google.android.apps.docs",
    "com.google.android.videos", "com.google.android.music", "com.google.android.apps.photos",
    "com.google.android.apps.tachyon", "com.google.android.googlequicksearchbox",
    # предустановленное
    "com.facebook.katana", "com.facebook.appmanager", "com.facebook.services", "com.facebook.system",
    "com.instagram.android", "com.booking", "com.netflix.mediaclient", "com.amazon.mShop.android.shopping",
    "com.ebay.mobile",
]
KEEP = {"com.google.android.webview", "com.android.chrome", "com.android.settings", "com.android.systemui", PKG}
assert not KEEP & set(DEBLOAT)

T = i18n.tr


class Fail(Exception):
    """Понятная ошибка для человека — печатаем и выходим."""


def say(text=""):
    print(" " + text if text else "", flush=True)


# ---------- adb ----------
def find_adb():
    exe = "adb.exe" if os.name == "nt" else "adb"
    local = TOOLS / exe
    if local.is_file():
        return str(local)
    return shutil.which("adb")


def get_adb():
    adb = find_adb()
    if adb:
        return adb
    kind = "windows" if os.name == "nt" else "linux" if sys.platform.startswith("linux") else "darwin"
    say(T("Нет adb — скачиваю platform-tools с dl.google.com…", "Немає adb — завантажую platform-tools з dl.google.com…"))
    try:
        with urllib.request.urlopen(TOOLS_URL.format(kind), timeout=120) as r:
            data = r.read()
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            z.extractall(ROOT)                     # в архиве — папка platform-tools/
    except Exception as e:
        raise Fail(T(f"Не удалось скачать adb: {e}. Скачай platform-tools вручную и распакуй в папку дека.",
                     f"Не вдалося завантажити adb: {e}. Завантаж platform-tools вручну й розпакуй у теку дека."))
    if os.name != "nt":
        for f in ("adb", "fastboot"):
            p = TOOLS / f
            if p.exists():
                p.chmod(0o755)
    adb = find_adb()
    if not adb:
        raise Fail(T("adb не нашёлся и после скачивания", "adb не знайшовся й після завантаження"))
    return adb


class Phone:
    """Телефон по adb. call() — единственное место, где запускается adb (тесты подменяют его)."""

    def __init__(self, adb):
        self.adb = adb

    def call(self, *args, timeout=120):
        r = subprocess.run([self.adb, *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def sh(self, cmd, timeout=60):
        return self.call("shell", cmd, timeout=timeout)

    # ----- подключение -----
    def wait(self, seconds=90):
        """Ждём один телефон с разрешённой отладкой; серийный номер."""
        said = set()
        t0 = time.monotonic()
        while True:
            _, out = self.call("devices", timeout=30)
            rows = [x.split("\t") for x in out.splitlines() if "\t" in x]          # «серийный\tсостояние»
            ok = [r[0] for r in rows if r[1].strip() == "device"]
            if len(ok) > 1:
                raise Fail(T("Подключено несколько телефонов — оставь один.", "Підключено кілька телефонів — залиш один."))
            if ok:
                return ok[0]
            if any(r[1].strip() == "unauthorized" for r in rows) and "auth" not in said:
                said.add("auth")
                say(T("На телефоне — окно «Разрешить отладку по USB?»: отметь «Всегда» и нажми «Разрешить».",
                      "На телефоні — вікно «Дозволити налагодження через USB?»: познач «Завжди» й натисни «Дозволити»."))
            elif not rows and "none" not in said:
                said.add("none")
                say(T("Жду телефон: кабель, на телефоне «Для разработчиков» → «Отладка по USB»,",
                      "Чекаю телефон: кабель, на телефоні «Для розробників» → «Налагодження через USB»,"))
                say(T("  режим USB — «Передача файлов». На Huawei ещё «Разрешить отладку ADB в режиме "
                      "«Только зарядка»».",
                      "  режим USB — «Передача файлів». На Huawei ще «Дозволити налагодження ADB у режимі "
                      "«Тільки заряджання»»."))
            if time.monotonic() - t0 > seconds:
                raise Fail(T("Телефон не подключился.", "Телефон не підключився."))
            time.sleep(2)

    def prop(self, name):
        return self.sh(f"getprop {name}")[1].strip()

    def packages(self):
        _, out = self.sh("pm list packages")
        return {x.split(":", 1)[1].strip() for x in out.splitlines() if x.startswith("package:")}

    def owner(self):
        _, out = self.sh("dumpsys device_policy")
        i = out.find("Device Owner")
        return i >= 0 and f"package={PKG}" in out[i:i + 400]

    def accounts(self):
        """Учётки на телефоне (Google, Huawei ID…): с ними Android не даёт сделать владельца устройства."""
        _, out = self.sh("dumpsys account")
        acc = []
        for line in out.splitlines():
            line = line.strip()
            if line.startswith("Account {") and "type=" in line:
                acc.append(line.split("type=", 1)[1].rstrip("}").strip())
        return acc

    def app(self, kiosk):
        return self.sh(f"am start -n {ACTIVITY} --es kiosk {kiosk}")


# ---------- шаги ----------
def removed_load():
    try:
        return json.loads(REMOVED.read_text(encoding="utf-8"))
    except Exception:
        return {}


def removed_save(data):
    REMOVED.parent.mkdir(parents=True, exist_ok=True)
    REMOVED.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


def apk_has_kiosk(path):
    """В APK есть приёмник DeckAdmin (приложение 1.3+) — без него владельцем устройства не стать."""
    try:
        with zipfile.ZipFile(path) as z:
            return any(b"Llocal/claudedeck/DeckAdmin;" in z.read(n) for n in z.namelist()
                       if n.startswith("classes") and n.endswith(".dex"))
    except Exception:
        return False


def install(ph):
    if not APK.is_file():
        raise Fail(T(f"Нет {APK.name} в папке дека.", f"Немає {APK.name} у теці дека."))
    if not apk_has_kiosk(APK):
        raise Fail(T(f"{APK.name} старый (до 1.3, без киоска) — собери новый: android/build.sh, и положи в папку дека.",
                     f"{APK.name} старий (до 1.3, без кіоску) — збери новий: android/build.sh, і поклади в теку дека."))
    say(T("Ставлю приложение дека…", "Ставлю застосунок дека…"))
    code, out = ph.call("install", "-r", "-g", str(APK), timeout=300)
    if code != 0 or "Success" not in out:
        if "UPDATE_INCOMPATIBLE" in out or "signatures do not match" in out.lower():
            raise Fail(T("На телефоне дек с другой подписью — удали его на телефоне и запусти снова.",
                         "На телефоні дек з іншим підписом — видали його на телефоні й запусти знову."))
        raise Fail(T("Не поставилось: ", "Не встановилося: ") + out.strip()[-300:])


def debloat(ph, serial):
    have = ph.packages()
    gone = [p for p in DEBLOAT if p in have and p not in KEEP]
    data = removed_load()
    done = list(dict.fromkeys(data.get(serial, [])))
    for p in gone:
        code, out = ph.sh(f"pm uninstall -k --user 0 {p}")
        if "Success" in out:
            done.append(p)
            say(T("  убрано: ", "  прибрано: ") + p)
    data[serial] = list(dict.fromkeys(done))
    removed_save(data)
    say(T(f"Лишнее убрано: {len(gone)} (вернуть — restore).", f"Зайве прибрано: {len(gone)} (повернути — restore)."))


def restore(ph, serial):
    data = removed_load()
    back = data.get(serial) or DEBLOAT
    n = 0
    for p in back:
        _, out = ph.sh(f"cmd package install-existing {p}")
        if "installed" in out.lower() and "fail" not in out.lower():
            n += 1
    data.pop(serial, None)
    removed_save(data)
    say(T(f"Возвращено приложений: {n}.", f"Повернуто застосунків: {n}."))


def tune(ph):
    """Чтобы EMUI не усыпляла и не убивала дек, а экран блокировки не мешал."""
    ph.sh(f"dumpsys deviceidle whitelist +{PKG}")
    ph.sh("settings put global wifi_sleep_policy 2")
    code, out = ph.sh("locksettings set-disabled true")
    if code != 0 and "password" in out.lower():
        say(T("На телефоне стоит PIN / пароль экрана — сними его, иначе экран блокировки останется.",
              "На телефоні стоїть PIN / пароль екрана — зніми його, інакше екран блокування залишиться."))


def make_owner(ph):
    if ph.owner():
        return True
    acc = ph.accounts()
    if acc:
        say(T("Android не даёт включить киоск, пока на телефоне есть учётные записи:",
              "Android не дає ввімкнути кіоск, поки на телефоні є облікові записи:"))
        for a in acc:
            say("  · " + a)
        say(T("Удали их: Настройки → Пользователи и аккаунты (Huawei ID — Настройки → свой аккаунт → Выйти),",
              "Видали їх: Налаштування → Користувачі й акаунти (Huawei ID — Налаштування → свій акаунт → Вийти),"))
        say(T("потом запусти Phone ещё раз. Киоск без аккаунтов — так и задумано: телефон только для дека.",
              "потім запусти Phone ще раз. Кіоск без акаунтів — так і задумано: телефон тільки для дека."))
        return False
    code, out = ph.sh(f"dpm set-device-owner {ADMIN}")
    if code == 0 and "Success" in out:
        return True
    low = out.lower()
    if "account" in low or "users" in low:
        say(T("Android против: на телефоне есть учётные записи или второй пользователь — удали и повтори.",
              "Android проти: на телефоні є облікові записи або другий користувач — видали й повтори."))
    elif "already" in low and "owner" in low:
        say(T("На телефоне уже есть другой «владелец устройства» — киоск дека не встанет.",
              "На телефоні вже є інший «власник пристрою» — кіоск дека не стане."))
    else:
        say(T("Не получилось сделать дек владельцем: ", "Не вийшло зробити дек власником: ") + out.strip()[-300:])
    return False


WEBVIEW_MIN = 90       # старше — страница дека местами не работает (WebView 83 не знает inset, gap во flex…)


def webview(ph):
    """(пакет, версия) WebView на телефоне: Android System WebView или Chrome — что выбрано в системе."""
    _, out = ph.sh("dumpsys webviewupdate")
    for line in out.splitlines():
        if "Current WebView package" in line and "(" in line:
            inside = line.rsplit("(", 1)[1].rstrip(")").split(",")
            if len(inside) >= 2:
                return inside[0].strip(), inside[1].strip()
    return "", ""


def webview_major(ver):
    try:
        return int(str(ver).split(".")[0])
    except ValueError:
        return 0


def webview_check(ph):
    """Старый WebView — предупредить и сказать, как обновить. True — всё в порядке."""
    pkg, ver = webview(ph)
    if not ver or webview_major(ver) >= WEBVIEW_MIN:
        return True
    say("")
    say(T(f"WebView на телефоне старый: {ver} ({pkg}) — часть окон дека не откроется или будет кривой.",
          f"WebView на телефоні старий: {ver} ({pkg}) — частина вікон дека не відкриється або буде кривою."))
    say(T("Обновить: Phone off → на телефоне добавь аккаунт Google → Play Маркет → «Android System WebView»",
          "Оновити: Phone off → на телефоні додай акаунт Google → Play Маркет → «Android System WebView»"))
    say(T("  (и Chrome) → Обновить → удали аккаунт → Phone on. Или скачай APK «Android System WebView»",
          "  (і Chrome) → Оновити → видали акаунт → Phone on. Або завантаж APK «Android System WebView»"))
    say(T("  (arm64-v8a, для Android 10) и: Phone webview путь\\к\\файлу.apk",
          "  (arm64-v8a, для Android 10) і: Phone webview шлях\\до\\файлу.apk"))
    return False


def webview_install(ph, path):
    f = Path(path)
    if not f.is_file():
        raise Fail(T(f"Нет файла {f}", f"Немає файлу {f}"))
    before = webview(ph)[1]
    say(T("Ставлю WebView…", "Ставлю WebView…"))
    code, out = ph.call("install", "-r", str(f), timeout=600)
    if code != 0 or "Success" not in out:
        raise Fail(T("Не поставилось: ", "Не встановилося: ") + out.strip()[-300:])
    after = webview(ph)[1]
    say(T(f"WebView: {before or '?'} → {after or '?'}. Перезапусти дек на телефоне (Phone on).",
          f"WebView: {before or '?'} → {after or '?'}. Перезапусти дек на телефоні (Phone on)."))


def setup(ph, serial, keep_apps=False):
    say(f"Телефон: {ph.prop('ro.product.manufacturer')} {ph.prop('ro.product.model')}, "
        f"Android {ph.prop('ro.build.version.release')}")
    install(ph)
    if not keep_apps:
        debloat(ph, serial)
    tune(ph)
    if not make_owner(ph):
        ph.app("on")
        return 2
    ph.app("on")
    say("")
    say(T("Готово: телефон — только дек. Выключить киоск — Настройки дека → «Приложение для Android»",
          "Готово: телефон — тільки дек. Вимкнути кіоск — Налаштування дека → «Застосунок для Android»"))
    say(T("  или Phone off; совсем отпустить телефон — Phone release.",
          "  або Phone off; зовсім відпустити телефон — Phone release."))
    webview_check(ph)
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    i18n.set_lang(i18n.config_lang(ROOT))
    keep = "--keep-apps" in argv
    argv = [a for a in argv if not a.startswith("--")]
    cmd = (argv or ["setup"])[0]
    if cmd not in ("setup", "on", "off", "release", "restore", "webview"):
        say(__doc__)
        return 1
    try:
        ph = Phone(get_adb())
        serial = ph.wait()
        if cmd == "setup":
            return setup(ph, serial, keep)
        if cmd == "webview":
            if len(argv) > 1:
                webview_install(ph, argv[1])
            else:
                pkg, ver = webview(ph)
                say(f"WebView: {ver or '?'} ({pkg or '?'})")
                if webview_check(ph):
                    say(T("Новый — обновлять не нужно.", "Новий — оновлювати не потрібно."))
            return 0
        if cmd in ("on", "off"):
            if not ph.owner():
                raise Fail(T("Дек не владелец устройства — сначала настрой телефон (Phone без слов).",
                             "Дек не власник пристрою — спершу налаштуй телефон (Phone без слів)."))
            ph.app(cmd)
            say(T("Киоск включён.", "Кіоск увімкнено.") if cmd == "on" else T("Киоск выключен.", "Кіоск вимкнено."))
            return 0
        if cmd == "release":
            ph.app("release")
            time.sleep(2)
            say(T("Телефон отпущен: дек — обычное приложение (можно удалить).",
                  "Телефон відпущено: дек — звичайний застосунок (можна видалити)."))
            return 0
        restore(ph, serial)
        return 0
    except Fail as e:
        say(str(e))
        return 1
    except (subprocess.TimeoutExpired, OSError) as e:
        say(T("adb не отвечает: ", "adb не відповідає: ") + str(e))
        return 1


if __name__ == "__main__":
    sys.exit(main())
