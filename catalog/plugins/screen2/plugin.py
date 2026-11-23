"""Другой экран: телефон — второй монитор ПК.

Монитор даёт драйвер виртуального дисплея (Virtual Display Driver: winget install --id=VirtualDrivers.Virtual-Display-Driver -e).
Для Windows он всегда подключён, поэтому щипок на деке ничего не «отключает»: плагин просто перестаёт снимать картинку,
пока её никто не смотрит (api.viewers() == 0), — окна остаются на месте, Windows молчит.

Картинка: GDI-снимок только этого монитора (wincap.Grabber, с курсором) → JPEG (Pillow) → api.frame → /stream/screen2
(MJPEG в <img>, понимает и старый WebView). Не изменилось — кадр не шлём. Касания с телефона → api input →
настоящий сенсорный ввод Windows (InjectTouchInput: прокрутка пальцем, удержание — правый клик) или мышь.
Режим «Другий екран» — поток на весь экран (full), без шапки и дока; щипок — на главный дека, развёл — обратно.
"""
import io
import queue
import re
import sys
import threading
import time
import zlib

NAME = {"en": "Second screen", "uk": "Другий екран"}
ABOUT = {"en": "The phone as a second PC monitor (needs the Virtual Display Driver). Windows only",
         "uk": "Телефон — другий монітор ПК (потрібен драйвер Virtual Display Driver). Тільки Windows"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
LANGS = ["en", "uk"]                     # на каких языках свои тексты (первый — основной); другие — из языковых пакетов дека
PERMS = ["screen", "input", "native"]
VERSION = "1.1.0"                        # версия плагина в каталоге (catalog/index.json): подними — деки предложат обновиться
CHANGES = [("1.1.0", "English is now the main language: the plugin speaks English by default, Ukrainian is built in, other languages come from the deck's language packs",
            "Основна мова тепер англійська: плагін англійською за замовчуванням, українська вбудована, інші мови — з мовних пакетів деку"),
           ("1.0.0", "First version in the catalog: the phone as a second monitor",
            "Перша версія в каталозі: телефон — другий монітор")]
OS = ["windows"]                         # только Windows: на Linux каталог не предложит
DEFAULT_ON = False

WIN = sys.platform == "win32"
VIRTUAL_RE = re.compile(r"virtual|vdd|mtt|idd|parsec|spacedesk|usbmmidd|amyuni", re.I)
FPS = {"15": 15, "30": 30, "60": 60}
QUALITY = {"low": 55, "mid": 70, "high": 85}
SCALE = {"100": 1.0, "75": 0.75, "50": 0.5}
RESCAN = 3.0            # секунд между переборами мониторов (подключили / поменяли разрешение)
HOLD_TICK = 0.05        # палец стоит на месте — всё равно напоминаем Windows о касании (иначе отменит его)
WINGET = "winget install --id=VirtualDrivers.Virtual-Display-Driver -e"


def pick(mons, want):
    """Какой монитор показывать: выбранный в настройках (по имени устройства), иначе виртуальный не основной."""
    if want:
        for m in mons:
            if m["dev"] == want:
                return m
    virt = [m for m in mons if VIRTUAL_RE.search(m.get("adapter", "") + " " + m.get("name", ""))]
    for m in virt:
        if not m["primary"]:
            return m
    return virt[0] if virt else None


def encode(raw, w, h, scale, quality):
    """BGRX сверху вниз → JPEG (Pillow)."""
    from PIL import Image
    im = Image.frombuffer("RGB", (w, h), raw, "raw", "BGRX", 0, 1)
    if scale < 1:
        im = im.resize((max(2, int(w * scale)), max(2, int(h * scale))), Image.BILINEAR)
    b = io.BytesIO()
    im.save(b, "JPEG", quality=quality, optimize=False)
    return b.getvalue()


class Plugin:
    def __init__(self, api, cap=None):
        self.api = api
        self.cap = cap                         # подмена wincap в тестах
        self.run = False
        self.mons, self.mon, self.err = [], None, ""
        self.fps_real, self.kb = 0.0, 0
        self.inq = queue.Queue()
        self.input_mode = ""                   # "touch" / "mouse" — как получилось
        self.last_scan = 0.0
        self.watching = 0

    # ---------- жизнь ----------
    def start(self):
        self.run = True
        if self.cap is None and WIN:
            try:
                import wincap
                self.cap = wincap
            except Exception as e:             # ctypes / user32 — не должно, но пусть видно в настройках
                self.err = str(e)[:200]
        if self.cap is None:
            return
        self.scan()
        threading.Thread(target=self.capture, name="screen2", daemon=True).start()
        threading.Thread(target=self.inputs, name="screen2-in", daemon=True).start()

    def stop(self):
        self.run = False
        self.inq.put(None)

    def cfg(self, k, default):
        v = self.api.cfg.get(k)
        return v if v is not None else default

    def scan(self):
        try:
            mons = self.cap.monitors()
        except Exception as e:
            self.err = str(e)[:200]
            return
        mon = pick(mons, self.api.cfg.get("monitor"))
        changed = mon != self.mon or [m["dev"] for m in mons] != [m["dev"] for m in self.mons]
        self.mons, self.mon, self.last_scan = mons, mon, time.monotonic()
        if changed:
            self.api.update()

    # ---------- картинка ----------
    def capture(self):
        g, gkey, last_crc, t_fps, n = None, None, None, time.monotonic(), 0
        while self.run:
            t0 = time.monotonic()
            if t0 - self.last_scan > RESCAN:
                self.scan()
            mon, watch = self.mon, self.api.viewers()
            if bool(watch) != bool(self.watching):     # начали / перестали смотреть — обновить строку в доке
                self.watching = watch
                self.api.update()
            if not watch or not mon:
                if g:
                    g.close()
                    g, gkey, last_crc = None, None, None
                time.sleep(0.2)
                continue
            key = (mon["x"], mon["y"], mon["w"], mon["h"])
            try:
                if key != gkey:
                    if g:
                        g.close()
                    g, gkey, last_crc = self.cap.Grabber(*key), key, None
                raw = g.grab(cursor=True)
                crc = zlib.crc32(raw)
                if crc != last_crc:                  # не изменилось — не жмём и не шлём (поток повторит прежний кадр)
                    last_crc = crc
                    jpg = encode(raw, mon["w"], mon["h"], SCALE.get(str(self.cfg("scale", "100")), 1.0),
                                 QUALITY.get(self.cfg("quality", "mid"), 70))
                    self.api.frame(jpg)
                    self.kb, n = len(jpg) // 1024, n + 1
                if self.err:
                    self.err = ""
                    self.api.update()
            except Exception as e:
                if str(e)[:200] != self.err:
                    self.err = str(e)[:200]
                    self.api.update()
                if g:
                    g.close()
                g, gkey = None, None
                time.sleep(1)
                continue
            now = time.monotonic()
            if now - t_fps >= 2:
                self.fps_real, n, t_fps = n / (now - t_fps), 0, now
            time.sleep(max(0.0, 1 / FPS.get(str(self.cfg("fps", "30")), 30) - (now - t0)))
        if g:
            g.close()

    # ---------- касания ----------
    def input(self, e):
        """Из потока дека — только в очередь (вводом занимается свой поток)."""
        self.inq.put(e)

    def inputs(self):
        dev, kind, down, pos = None, None, False, (0, 0)
        while self.run:
            try:
                e = self.inq.get(timeout=HOLD_TICK if down else 1.0)
            except queue.Empty:
                if down and kind == "touch":         # палец держат — Windows должна видеть, что касание живо
                    dev.send("move", *pos)
                continue
            if e is None:
                break
            mon = self.mon
            if not mon:
                continue
            want = "mouse" if self.api.cfg.get("mouse") else "touch"
            if dev is None or kind != want:
                dev, kind = self.device(want)
                if dev is None:
                    continue
            x = mon["x"] + e["x"] * (mon["w"] - 1)
            y = mon["y"] + e["y"] * (mon["h"] - 1)
            pos = (x, y)
            try:
                dev.send(e["k"], x, y)
            except Exception as ex:
                self.api.log.warning("input: %s", ex)
            down = e["k"] in ("down", "move")

    def device(self, want):
        try:
            if want == "touch":
                d = self.cap.Touch()
                self.input_mode = "touch"
                return d, "touch"
        except Exception as e:
            self.api.log.warning("touch: %s", e)
        try:
            d = self.cap.Mouse()
            self.input_mode = "mouse"
            return d, want
        except Exception as e:
            self.api.log.warning("mouse: %s", e)
            return None, None

    # ---------- телефон ----------
    def action(self, aid, value):
        if aid == "monitor":
            self.api.cfg["monitor"] = str(value or "")
            self.last_scan = 0
        elif aid in ("fps", "quality", "scale"):
            self.api.cfg[aid] = str(value)
        elif aid == "mouse":
            self.api.cfg["mouse"] = bool(value)
        elif aid == "rescan":
            self.last_scan = 0
        else:
            return
        self.api.save()
        self.api.update()

    def label(self, m):
        tr = self.api.tr
        return f'{m["name"] or m["dev"]} — {m["w"]}×{m["h"]}' + (tr(" (main)", " (основний)") if m["primary"] else "")

    def help(self):
        tr = self.api.tr
        if self.cap is None:
            return [tr("Windows only for now.", "Поки що тільки Windows.")]
        return [tr("Virtual monitor not found. Install the Virtual Display Driver (once, as administrator):",
                   "Віртуальний монітор не знайдено. Постав драйвер Virtual Display Driver (один раз, від адміністратора):"),
                WINGET,
                tr("Then it shows up in «Settings → Display» as a second monitor. Your phone's resolution "
                   "(2340×1080) — in C:\\VirtualDisplayDriver\\vdd_settings.xml, then disable and enable the "
                   "driver in Device Manager.",
                   "Потім у «Параметри → Дисплей» він з'явиться другим монітором. Свою роздільність телефона (2340×1080) — "
                   "у C:\\VirtualDisplayDriver\\vdd_settings.xml, потім вимкнути й увімкнути драйвер у диспетчері пристроїв."),
                tr("Or pick any monitor manually in the plugin settings.", "Або вибери в налаштуваннях плагіна будь-який монітор вручну.")]

    def ui(self):
        tr, mon = self.api.tr, self.mon
        watch = self.api.viewers()
        sub = (f'{mon["w"]}×{mon["h"]}' if mon else tr("no monitor", "немає монітора"))
        if mon:
            screen = [{"type": "stream", "id": "scr", "full": True,
                       "text": tr("Waiting for the picture from the PC…", "Чекаю картинку з ПК…") if not self.err else self.err}]
        else:
            screen = [{"type": "card", "title": tr("Second screen", "Другий екран"), "items":
                       [{"type": "label", "style": "line", "label": t} for t in self.help()]
                       + ([{"type": "label", "style": "line", "color": "#E5484D", "label": self.err}] if self.err else [])
                       + [{"type": "button", "id": "rescan", "label": tr("Search again", "Шукати знову")}]}]
        opts = [{"value": "", "label": tr("Auto (virtual)", "Сам (віртуальний)")}] + \
               [{"value": m["dev"], "label": self.label(m)} for m in self.mons]
        status = (f'{self.fps_real:.0f} {tr("fps", "кадр/с")}, ~{self.kb} {tr("KB", "КБ")}' if watch else tr("nobody is watching", "ніхто не дивиться"))
        return {
            "mode": {"name": {"en": "Second screen", "uk": "Другий екран"}, "icon": "monitor", "color": "#4C8DFF",
                     "sub": sub},
            "screen": screen,
            "dock": [{"type": "card", "go": "mode", "title": tr("Second screen", "Другий екран"), "icon": "🖥", "items": [
                {"type": "text", "label": tr("Monitor", "Монітор"), "value": self.label(mon) if mon else "—"},
                {"type": "text", "label": tr("Stream", "Потік"), "value": status}]}],
            "settings": [
                *([{"type": "label", "style": "line", "color": "#E07A4F", "label": t} for t in self.help()] if not mon else []),
                *([{"type": "label", "style": "line", "color": "#E5484D", "label": self.err}] if self.err else []),
                {"type": "select", "id": "monitor", "label": tr("Monitor", "Монітор"), "value": self.api.cfg.get("monitor") or "",
                 "options": opts},
                {"type": "select", "id": "fps", "label": tr("Frames per second", "Кадрів за секунду"), "value": str(self.cfg("fps", "30")),
                 "options": [{"value": k, "label": k} for k in FPS]},
                {"type": "select", "id": "quality", "label": tr("Quality", "Якість"), "value": self.cfg("quality", "mid"),
                 "options": [{"value": "low", "label": tr("Low (faster)", "Низька (швидше)")},
                             {"value": "mid", "label": tr("Medium", "Середня")},
                             {"value": "high", "label": tr("High", "Висока")}]},
                {"type": "select", "id": "scale", "label": tr("Picture size", "Розмір картинки"), "value": str(self.cfg("scale", "100")),
                 "options": [{"value": k, "label": k + " %"} for k in SCALE]},
                {"type": "toggle", "id": "mouse", "label": tr("Mouse instead of touches", "Миша замість дотиків"),
                 "value": bool(self.api.cfg.get("mouse"))},
                {"type": "text", "label": tr("Input", "Введення"),
                 "value": {"touch": tr("Windows touches", "дотики Windows"), "mouse": tr("mouse", "миша")}.get(self.input_mode, "—")},
                {"type": "text", "label": tr("Stream", "Потік"), "value": status}],
        }
