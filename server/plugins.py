"""Плагины дека: папка plugins/ рядом с server/. Плагин — файл plugins/<имя>.py или папка plugins/<имя>/plugin.py
(файлы и папки, чьё имя начинается с «_» или «.», не загружаются — например, образец plugins/_example.py).

Дек сам находит новые плагины, перезагружает изменённые и убирает удалённые (раз в несколько секунд). Плагин
описывает, что показать на телефоне: блок в доке, свои настройки, плитки для режимов — из простых элементов
(кнопка, переключатель, ползунок, текст, ряд). Выключили или удалили плагин — всё, что он добавил, пропадает
(плитки плагина в режимах прячутся и вернутся, если плагин снова включить). Как писать плагины — plugins/README.txt.

Внимание: плагин — обычный код на Python и выполняется на ПК с правами дека. Ставь только свои или проверенные."""
import ast
import asyncio
import importlib.util
import inspect
import logging
import math
import re
import shutil
import os
import sys
import time
import traceback
from pathlib import Path

import i18n
import perms

CRASH_N, CRASH_WINDOW = 8, 60.0     # столько ошибок плагина за столько секунд — останавливаем его (не весь дек)
SLOW_UI = 0.3                       # ui() дольше — в лог: весь дек ждёт, пока он думает

log = logging.getLogger("plugins")
LEGACY_TRASH = "_deleted"          # корзина прежних версий (удалённые плагины лежали тут) — дек стирает её при запуске
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
WIDGETS = {"button", "toggle", "slider", "text", "row", "input", "select", "textarea",   # простые
           "bar", "stat", "label", "image", "grid", "card", "shapes",  # для своего вида (шкалы, плашки, картинки, фигуры)
           "chart",                                                       # график: линии по точкам
           "stream"}                                                      # живая картинка с ПК (/stream/<id>, кадры api.frame)
BOXES = {"row", "grid", "card"}                                        # внутри — свои items
COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?")
# файлы плагина, которые страница может взять с ПК (/plugin/<id>/<путь>): картинки и шрифты
FILE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
              ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}
REL_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_./-]{0,150}")
DATA_IMG_RE = re.compile(r"data:image/(png|jpeg|webp|gif);base64,[A-Za-z0-9+/=]+")


def plugin_id(name):
    """Имя файла / папки → id плагина: маленькие латинские буквы, цифры, «-» и «_»."""
    pid = re.sub(r"[^a-z0-9_-]+", "-", name.lower()).strip("-_")[:40]
    return pid if ID_RE.fullmatch(pid or "") else None


def text_of(v, lang=None, pid=""):
    """Название и описание плагина: строка или {"en": …, "uk": …} (старые — {"ru", "uk"}) на языке дека (i18n.ptext:
    свой текст плагина, потом перевод из мовного пакета, потом английский)."""
    if lang is not None and lang != i18n.LANG:          # явный язык (не текущий) — без пакета
        if isinstance(v, dict):
            return str(v.get(lang) or v.get("en") or v.get("ru") or v.get("uk") or next(iter(v.values()), ""))
        return "" if v is None else str(v)
    return i18n.ptext(pid, v)


def clean_langs(v):
    """LANGS плагина или мода: на каких языках его собственные тексты (коды i18n.NAMES), первый — основной."""
    out = []
    for x in v if isinstance(v, (list, tuple)) else []:
        if isinstance(x, str) and x in i18n.NAMES and x not in out:
            out.append(x)
    return out


def color(v):
    """Цвет от плагина — только #RRGGBB / #RRGGBBAA (страница вставляет его в стили)."""
    return v if isinstance(v, str) and COLOR_RE.fullmatch(v) else None


def file_ref(v, exts=FILE_TYPES):
    """Путь к файлу плагина (без «..» и абсолютных) или картинка data:…;base64 (до 300 КБ)."""
    if not isinstance(v, str):
        return None
    if v.startswith("data:"):
        return v if len(v) <= 300_000 and DATA_IMG_RE.fullmatch(v) else None
    if REL_RE.fullmatch(v) and ".." not in v.split("/") and Path(v).suffix.lower() in exts:
        return v
    return None


def num(v, lo, hi):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return max(lo, min(hi, float(v)))
    return None


BADGES = {"blood", "alert", "ok", "dot"}   # значки, что дек рисует сам: капля крови, «!», ✓ (зелёный), • (жёлтый); иначе — 1–2 символа


def clean_shapes(items):
    """Фигуры элемента shapes (кукла, схема): прямоугольники / овалы в координатах viewBox, нажатие — action(id)."""
    out = []
    for s in items if isinstance(items, list) else []:
        if not isinstance(s, dict):
            continue
        x = {"kind": "ellipse" if s.get("kind") == "ellipse" else "rect"}
        for k, lo, hi in (("x", -2000, 4000), ("y", -2000, 4000), ("w", 0, 4000), ("h", 0, 4000), ("r", 0, 200),
                          ("band", 0, 1), ("bandH", 0, 1), ("opacity", 0, 1)):
            n = num(s.get(k), lo, hi)
            if n is not None:
                x[k] = n
        if "w" not in x or "h" not in x:
            continue
        for k in ("id", "label"):
            if s.get(k) is not None:
                x[k] = str(s[k])[:60]
        for k in ("fill", "stroke"):
            if color(s.get(k)):
                x[k] = s[k]
        for k in ("sel", "dash", "bevel", "tex", "splint"):
            if s.get(k):
                x[k] = True
        x["badges"] = [b if b in BADGES else str(b)[:4] for b in (s.get("badges") or [])[:3] if isinstance(b, str) and b]
        out.append(x)
    return out[:40]


def clean_mode(m, pid=""):
    """Режим от плагина: {"name": str | {en, uk}, "sub": …, "icon": имя значка режима, "color": "#RRGGBB"}.
    "en" — текст на языке дека (не украинском: свой у плагина / из пакета / английский), "uk" — украинский."""
    if not isinstance(m, dict) or not m.get("name"):
        return None
    name, sub = m["name"], m.get("sub") or ""
    cur = i18n.LANG
    try:
        i18n.LANG = "en" if cur == "uk" else cur
        en, sen = i18n.ptext(pid, name), i18n.ptext(pid, sub)
        i18n.LANG = "uk"
        ua, sua = i18n.ptext(pid, name), i18n.ptext(pid, sub)
    finally:
        i18n.LANG = cur
    out = {"en": en[:24], "uk": ua[:24], "subEn": sen[:80], "subUk": sua[:80],
           "icon": str(m.get("icon") or "star") if re.fullmatch(r"[a-z]{1,16}", str(m.get("icon") or "star")) else "star"}
    if color(m.get("color")) and len(m["color"]) == 7:
        out["color"] = m["color"].upper()
    return out


def clean_widgets(items, depth=0):
    """Элементы от плагина — только известные виды и поля (страница вставляет тексты как текст, не как HTML)."""
    out = []
    for w in items if isinstance(items, list) else []:
        if not isinstance(w, dict) or w.get("type") not in WIDGETS:
            continue
        x = {"type": w["type"]}
        for k in ("id", "label", "icon", "unit", "style", "hint", "title", "sub", "text", "alt", "placeholder", "button"):
            if w.get(k) is not None:
                x[k] = str(w[k])[:200]
        for k in ("color", "bg"):
            if color(w.get(k)):
                x[k] = w[k]
        if w["type"] == "image" and file_ref(w.get("src"), {e for e in FILE_TYPES if FILE_TYPES[e].startswith("image/")}):
            x["src"] = w["src"]
        for k, lo, hi in (("cols", 1, 8), ("w", 8, 1200), ("h", 8, 800), ("size", 10, 64)):
            n = num(w.get(k), lo, hi)
            if n is not None:
                x[k] = n
        if isinstance(w.get("parts"), list):          # шкала из частей (сытость по видам еды и т. п.)
            x["parts"] = [{"value": float(p.get("value") or 0), **({"color": p["color"]} if color(p.get("color")) else {}),
                           **({"label": str(p["label"])[:40]} if p.get("label") else {})}
                          for p in w["parts"][:8] if isinstance(p, dict) and num(p.get("value"), -1e9, 1e9) is not None]
        for k in ("value",):
            if isinstance(w.get(k), bool):
                x[k] = w[k]
            elif isinstance(w.get(k), (int, float)):
                x[k] = float(w[k])
            elif w.get(k) is not None:
                x[k] = str(w[k])[:4000 if w["type"] == "textarea" else 500]
        if isinstance(w.get("widths"), list):          # ширины колонок сетки: [1, 3] — вторая втрое шире
            ws = [num(v, 0.1, 20) for v in w["widths"][:8]]
            if ws and all(v is not None for v in ws):
                x["widths"] = ws
        for k in ("fill", "scroll"):                   # сетка на всю высоту; карточка / сетка со своей прокруткой
            if w.get(k):
                x[k] = True
        if w.get("go") in ("mode", "screen"):          # нажатие открывает режим / экран плагина (на этом телефоне)
            x["go"] = w["go"]
        if w["type"] == "select" and isinstance(w.get("options"), list):
            x["options"] = [{"value": str(o.get("value"))[:80], "label": str(o.get("label") or o.get("value"))[:80]}
                            for o in w["options"][:60] if isinstance(o, dict) and o.get("value") is not None]
        if w["type"] == "textarea":
            n = num(w.get("rows"), 2, 20)
            x["rows"] = n if n is not None else 5
        if w["type"] == "chart":
            x.update(clean_chart(w))
        if w["type"] == "shapes":
            x["items"] = clean_shapes(w.get("items"))
            for k in ("vw", "vh"):
                n = num(w.get(k), 1, 4000)
                if n is not None:
                    x[k] = n
        for k in ("min", "max", "step"):
            if isinstance(w.get(k), (int, float)) and not isinstance(w.get(k), bool):
                x[k] = float(w[k])
        for k in ("on", "off", "disabled", "wide", "pixel", "full"):
            if k in w:
                x[k] = bool(w[k])
        if w["type"] in BOXES and depth < 6:              # экран режима: сетка → карточка → сетка → карточка → ряд
            x["items"] = clean_widgets(w.get("items"), depth + 1)
        out.append(x)
    return out[:80]


def clean_chart(w):
    """График: до 6 линий по 300 точек (числа), цвет и подпись у каждой; xs — подписи под осью (до 6), si — 1.2k / 3.4M."""
    out = {"series": []}
    for sr in (w.get("series") if isinstance(w.get("series"), list) else [])[:40]:
        if not isinstance(sr, dict) or not isinstance(sr.get("values"), list) or len(out["series"]) >= 6:
            continue
        vals = [float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else None
                for v in sr["values"][-300:]]
        y = {"values": vals}
        if color(sr.get("color")):
            y["color"] = sr["color"]
        if sr.get("label"):
            y["label"] = str(sr["label"])[:40]
        if sr.get("fill"):
            y["fill"] = True
        out["series"].append(y)
    if isinstance(w.get("xs"), list):
        out["xs"] = [str(v)[:20] for v in w["xs"][:6]]
    if w.get("si"):
        out["si"] = True
    return out


def clean_theme(t):
    """Свой вид блока плагина: цвета, скругление, шрифт (sans / serif / mono или файл шрифта), текстура фона."""
    if not isinstance(t, dict):
        return {}
    x = {k: t[k] for k in ("bg", "panel", "border", "text", "muted", "accent") if color(t.get(k))}
    r = num(t.get("radius"), 0, 28)
    if r is not None:
        x["radius"] = r
    wt = num(t.get("weight"), 300, 900)                # толщина шрифта плагина (как в игре — потолще)
    if wt is not None:
        x["weight"] = int(wt)
    f = t.get("font")
    if f in ("sans", "serif", "mono"):
        x["font"] = f
    elif file_ref(f, {".woff2", ".woff", ".ttf", ".otf"}):
        x["font"] = f
    tex = file_ref(t.get("texture"), {".png", ".jpg", ".jpeg", ".webp", ".gif"})
    if tex:
        x["texture"] = tex
    if "pixel" in t:
        x["pixel"] = bool(t["pixel"])
    return x


def clean_tiles(items):
    out = []
    for t in items if isinstance(items, list) else []:
        if isinstance(t, dict) and t.get("id") and t.get("label"):
            x = {"id": str(t["id"])[:60], "label": str(t["label"])[:60]}
            for k in ("icon", "sub"):
                if t.get(k):
                    x[k] = str(t[k])[:60]
            for k in ("on", "screen"):                 # screen — плитка открывает экран плагина
                if k in t:
                    x[k] = bool(t[k])
            out.append(x)
    return out[:40]


INPUT_KINDS = {"down", "move", "up", "cancel", "wheel"}


def clean_input(e):
    """Касание по потоку (элемент stream) с телефона: вид, x / y — доли картинки 0…1, n — номер пальца, dy — прокрутка."""
    if not isinstance(e, dict) or e.get("k") not in INPUT_KINDS:
        return None
    x, y = num(e.get("x"), -1, 2), num(e.get("y"), -1, 2)
    if x is None or y is None:
        return None
    out = {"k": e["k"], "x": min(1.0, max(0.0, float(x))), "y": min(1.0, max(0.0, float(y))),
           "n": int(num(e.get("n"), 0, 9) or 0), "id": str(e.get("a") or "")[:60]}
    dy = num(e.get("dy"), -5000, 5000)
    if dy is not None:
        out["dy"] = float(dy)
    return out


def clean_ctx(items):
    """Кнопки плагина на плитке программы (вместо её сочетаний клавиш): {"procs": ["discord", …], "id", "label", "on"}.
    procs — имена процессов без .exe, нижним регистром (плитка подходит, если её процесс содержит одно из них)."""
    out = []
    for t in items if isinstance(items, list) else []:
        if not isinstance(t, dict) or not t.get("id") or not t.get("label"):
            continue
        procs = [str(x).lower()[:40] for x in (t.get("procs") or []) if isinstance(x, str) and re.fullmatch(r"[\w.\- ]{2,40}", x)][:8]
        if procs:
            out.append({"procs": procs, "id": str(t["id"])[:60], "label": str(t["label"])[:30], "on": bool(t.get("on"))})
    return out[:3]


class Api:
    """То, что получает плагин: Plugin(api). Методы можно звать из любого потока."""

    def __init__(self, host, pid, folder):
        self._host, self.id, self.folder = host, pid, Path(folder)
        self.log = logging.getLogger("plugin." + pid)

    @property
    def lang(self):
        return self._host.lang()

    def tr(self, base, uk, *args):
        """Текст на языке дека: base — основной язык плагина (LANGS[0]; в комплекте — английский), uk — украинский,
        другие — из мовного пакета (plugins[id]), нет — base. Значения — подстановкой: api.tr("Port {0}", "Порт {0}", p)."""
        self.lang                                # язык из конфига — подхватить, если сменился
        return i18n.ptr(self.id, base, uk, *args)

    @property
    def cfg(self):
        """Свои настройки плагина (dict), хранятся в конфиге дека; поменял — api.save()."""
        return self._host.plugin_cfg(self.id)

    @property
    def deck_cfg(self):
        """Настройки дека — только читать (язык, старые ключи для переезда настроек)."""
        return self._host.deck.store.cfg

    def save(self):
        self._host.soon(self._host.deck.store.save)

    def update(self):
        """Поменялось, что показывать (состояние, кнопки) — разослать телефонам."""
        self._host.soon(lambda: asyncio.ensure_future(self._host.push()))

    def toast(self, text):
        self._host.soon(lambda: asyncio.ensure_future(self._host.deck.toast(str(text))))

    def event(self, text, level="info", notify=False):
        """Запись в историю дека (notify — ещё и в центр уведомлений; level: info / warn / error)."""
        self._host.soon(lambda: asyncio.ensure_future(
            self._host.deck.event(self.id, str(text), level=level, notify=notify)))

    def later(self, seconds, fn):
        """Вызвать fn() через seconds секунд в потоке дека (отменится, если плагин выключат)."""
        h = self._host.deck.loop.call_later(seconds, lambda: self._host.alive(self.id) and fn())
        return h

    def call(self, fn):
        """Вызвать fn() в потоке дека (из своего потока — например, когда пришли данные от устройства)."""
        self._host.soon(lambda: self._host.alive(self.id) and fn())

    def frame(self, jpeg):
        """Новый кадр (JPEG) для живой картинки — элемента stream (/stream/<id>). Можно из своего потока;
        телефонам уходит только самый свежий кадр (медленный телефон пропускает лишние)."""
        self._host.put_frame(self.id, bytes(jpeg))

    def viewers(self):
        """Сколько телефонов сейчас смотрят поток плагина (0 — можно не снимать кадры)."""
        return len(self._host.waiters.get(self.id, ()))

    def _need(self, perm, what):
        """Метод api, которому нужно разрешение: нет согласия пользователя — не делаем (и видно на странице плагина)."""
        if self._host.has_perm(self.id, perm):
            return True
        it = self._host.items.get(self.id)
        if it is not None and perm not in it.denied:
            it.denied[perm] = what
            log.warning("Плагин «%s»: %s без разрешения «%s»", self.id, what, perm)
            self._host.soon(lambda: asyncio.ensure_future(self._host.push()))
        return False

    def _dep(self, name):
        """Устаревший метод api (perms.DEPRECATED): отмечаем у плагина, чтобы автор успел переделать."""
        it = self._host.items.get(self.id)
        if it is not None and name not in it.deprecated:
            it.deprecated.add(name)
            log.info("Плагин «%s»: устаревший api.%s", self.id, name)

    def script(self, text, label=None, key=None):
        """Скрипт дека (как у кнопки-скрипта: ctrl+c / пауза 500 / текст … / держать w 1500, шаги-сценарии).
        Повторный вызов с тем же key, пока идёт, — стоп. Жмёт клавиши в окно, которое на ПК сейчас активно.
        Нужен разрешение «input» (PERMS = ["input"])."""
        if not self._need("input", "api.script"):
            return
        sid = f"plugin:{self.id}:{key or label or 'script'}"
        name = str(label or self.id)
        self._host.soon(lambda: asyncio.ensure_future(self._host.deck.toggle_script(sid, name, {"script": str(text)})))


class Loaded:
    def __init__(self, pid, path, folder):
        self.id, self.path, self.folder = pid, Path(path), Path(folder)
        self.mod = self.obj = None
        self.error = ""
        self.stamp = None
        self.name = self.about = None
        self.langs = []                          # LANGS: на каких языках тексты плагина (первый — основной)
        self.default_on = True
        self.version = ""                        # VERSION плагина (каталог сравнивает со своей)
        self.files = [self.path]
        self.api, self.api_state = 1, "none"     # версия API плагина и что с ней (perms.check_api)
        self.declared, self.unknown = [], []    # PERMS: известные / непонятные имена
        self.found = {}                          # что видно в коде: {разрешение: [звідки]}
        self.denied = {}                         # api без разрешения: {разрешение: что звали}
        self.deprecated = set()
        self.stats = {}                          # ui / action / input → [сколько, всего с, самое долгое с]
        self.fails = []                          # когда падал (время) — за последнюю CRASH_WINDOW
        self.crashed = ""                        # остановлен деком из-за ошибок (почему); снова — «Перезапустить»
        self.halted = False                      # остановлен вручную («Остановить»), не выключен

    def took(self, what, sec):
        s = self.stats.setdefault(what, [0, 0.0, 0.0])
        s[0] += 1
        s[1] += sec
        s[2] = max(s[2], sec)

    @property
    def needed(self):
        """Что плагину нужно: заявленное и то, что видно в коде."""
        return [k for k in perms.ORDER if k in self.declared or k in self.found]


class Host:
    def __init__(self, deck, folder):
        self.deck = deck
        self.folder = Path(folder)
        self.items = {}            # id → Loaded
        self.frames = {}           # id → (номер, JPEG) — последний кадр потока плагина (элемент stream)
        self.waiters = {}          # id → {asyncio.Event} — кто сейчас смотрит поток (по одному на соединение)
        self.last_scan = 0.0
        # разрешения, на которые пользователь согласился: {id: [разрешение, ...]}. Ключа ещё нет — дек обновился с версии
        # без разрешений: уже включённым плагинам на первом поиске разрешения выдаём сами (иначе всё бы остановилось)
        self.migrate = not deck.store.cfg.get("pluginPermsSince")
        deck.store.cfg.setdefault("pluginPerms", {})
        pl = deck.store.cfg.setdefault("plugins", {})
        if "lamp" not in pl:       # лампа до плагинов была переключателем «Лампа» (по умолчанию выключен)
            pl["lamp"] = deck.store.cfg.get("lamp") is True
        c = deck.store.cfg                   # блок лампы в доке: «lamp» → «p:lamp»
        if isinstance(c.get("dockOrder"), list):
            c["dockOrder"] = ["p:lamp" if x == "lamp" else x for x in c["dockOrder"]]
        if isinstance(c.get("dockH"), dict) and "lamp" in c["dockH"]:
            c["dockH"].setdefault("p:lamp", c["dockH"].pop("lamp"))

    # ---------- вспомогательное ----------
    def lang(self):
        i18n.follow(self.deck.store.cfg.get("lang"))
        return i18n.LANG

    def soon(self, fn):
        loop = self.deck.loop
        if loop is None:
            return fn()
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            return fn()
        loop.call_soon_threadsafe(fn)

    def plugin_cfg(self, pid):
        allc = self.deck.store.cfg.setdefault("pluginCfg", {})
        return allc.setdefault(pid, {})

    def master(self):
        return self.deck.store.cfg.get("pluginsOn") is not False

    def enabled(self, pid):
        return self.master() and (self.deck.store.cfg.get("plugins") or {}).get(pid, True) is not False

    # ---------- разрешения и версия API ----------
    def granted(self, pid):
        g = (self.deck.store.cfg.get("pluginPerms") or {}).get(pid)
        return set(g) if isinstance(g, list) else set()

    def has_perm(self, pid, perm):
        return perm in self.granted(pid)

    def missing(self, it):
        g = self.granted(it.id)
        return [p for p in it.needed if p not in g]

    def blocked(self, it):
        """Почему не запускаем: api_new / api_old / perms; "" — можно."""
        if it.api_state in ("new", "old"):
            return "api_" + it.api_state
        return "perms" if self.missing(it) else ""

    def grant(self, it):
        self.deck.store.cfg.setdefault("pluginPerms", {})[it.id] = list(it.needed)

    def alive(self, pid):
        it = self.items.get(pid)
        return bool(it and it.obj is not None)

    # ---------- поиск ----------
    def discover(self):
        """{id: (файл plugin.py, папка плагина, отпечаток всех его .py)}"""
        found = {}
        if not self.folder.is_dir():
            return found
        for p in sorted(self.folder.iterdir()):
            if p.name[:1] in "._":
                continue
            if p.is_file() and p.suffix == ".py":
                main, folder, files = p, self.folder, [p]
            elif p.is_dir() and (p / "plugin.py").is_file():
                main, folder = p / "plugin.py", p
                files = sorted(x for x in p.rglob("*.py") if "__pycache__" not in x.parts)
            else:
                continue
            pid = plugin_id(p.stem if p.is_file() else p.name)
            if not pid or pid in found:
                continue
            stamp = tuple((str(f), f.stat().st_mtime_ns, f.stat().st_size) for f in files if f.exists())
            found[pid] = (main, folder, stamp)
        return found

    async def scan(self, force=False):
        """Новые — загрузить, изменённые — перезагрузить, удалённые — выгрузить. True — что-то поменялось."""
        self.last_scan = time.monotonic()
        found = await asyncio.get_running_loop().run_in_executor(None, self.discover)
        changed = False
        for pid in list(self.items):
            if pid not in found:
                await self.unload(pid)
                del self.items[pid]
                log.info("Плагин «%s» удалён", pid)
                changed = True
        for pid, (main, folder, stamp) in found.items():
            it = self.items.get(pid)
            if it and it.stamp == stamp and not force:
                continue
            if it:
                await self.unload(pid)
                log.info("Плагин «%s» изменился — перезагружаю", pid)
            it = self.items[pid] = Loaded(pid, main, folder)
            it.stamp = stamp
            it.files = [Path(s[0]) for s in stamp] or [main]
            self.peek(it)
            pl = self.deck.store.cfg.setdefault("plugins", {})
            if self.migrate and self.enabled(pid) and pid not in self.deck.store.cfg["pluginPerms"]:
                self.grant(it)                                 # был включён до разрешений — согласие «по наследству»
                self.deck.store.save()
            if pid not in pl and (it.default_on is False or self.missing(it)):
                pl[pid] = False                                # DEFAULT_ON = False или новому нужны разрешения — ждёт, пока включат
                self.deck.store.save()
            elif self.enabled(pid) and self.missing(it):
                log.warning("Плагин «%s» просит новые разрешения: %s — жду согласия", pid, ", ".join(self.missing(it)))
            if it.api_state in ("new", "old"):
                log.warning("Плагин «%s»: API %s, дек умеет %s…%s — не запускаю", pid, it.api, perms.API_MIN, perms.API_VERSION)
            if self.enabled(pid) and not self.blocked(it):
                self.read(it)
                if not it.error:
                    await self.start(it)
            else:
                self.peek(it)                # выключенный не загружаем: ни его кода, ни его библиотек в памяти
            changed = True
        if self.migrate:
            self.migrate = False
            self.deck.store.cfg["pluginPermsSince"] = perms.API_VERSION
            self.deck.store.save()
        if changed:
            await self.push()
        return changed

    def peek(self, it):
        """Выключенный плагин: имя, описание и есть ли класс Plugin — из текста файла, без запуска его кода."""
        it.mod = None
        try:
            tree = ast.parse(it.path.read_text(encoding="utf-8"), str(it.path))
            vals = {}
            for n in tree.body:
                if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) \
                        and n.targets[0].id in ("NAME", "ABOUT", "DEFAULT_ON", "API", "PERMS", "VERSION", "LANGS"):
                    try:
                        vals[n.targets[0].id] = ast.literal_eval(n.value)
                    except ValueError:
                        pass
            if not any(isinstance(n, ast.ClassDef) and n.name == "Plugin" for n in tree.body):
                raise TypeError("нет класса Plugin")
            it.name, it.about, it.error = vals.get("NAME"), vals.get("ABOUT"), ""
            it.langs = clean_langs(vals.get("LANGS"))
            it.default_on = vals.get("DEFAULT_ON", True)
            it.version = str(vals.get("VERSION") or "")[:20]
            it.api, it.api_state = perms.check_api(vals.get("API"))
            it.declared, it.unknown = perms.clean_perms(vals.get("PERMS"))
        except Exception as e:
            it.error = f"{type(e).__name__}: {e}"
        it.found = perms.sniff(it.files)

    def forget(self, it):
        """Выключили — модуль плагина из памяти долой (включат — загрузится заново)."""
        sys.modules.pop(f"deckplugin_{it.id.replace('-', '_')}", None)
        self.purge(it)
        it.mod = None

    def purge(self, it):
        """Свои модули плагина-папки (import ddc рядом с plugin.py) — из памяти: иначе после правки
        или замены папки import вернёт старый модуль из sys.modules."""
        if it.folder == self.folder:
            return
        root = os.path.normcase(os.path.abspath(str(it.folder))) + os.sep
        for name, m in list(sys.modules.items()):
            f = getattr(m, "__file__", None)
            if f and os.path.normcase(os.path.abspath(f)).startswith(root):
                sys.modules.pop(name, None)

    def read(self, it):
        """Загрузить модуль (без запуска): имя, описание, ошибки."""
        name = f"deckplugin_{it.id.replace('-', '_')}"
        try:
            sys.modules.pop(name, None)
            self.purge(it)
            spec = importlib.util.spec_from_file_location(name, it.path)
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            if str(it.folder) not in sys.path and it.folder != self.folder:
                sys.path.insert(0, str(it.folder))
            spec.loader.exec_module(mod)
            if not inspect.isclass(getattr(mod, "Plugin", None)):
                raise TypeError("нет класса Plugin")
            it.mod, it.error = mod, ""
            it.name, it.about = getattr(mod, "NAME", None), getattr(mod, "ABOUT", None)
            it.langs = clean_langs(getattr(mod, "LANGS", None))
        except Exception as e:
            it.mod, it.error = None, f"{type(e).__name__}: {e}"
            log.warning("Плагин «%s» не загрузился: %s", it.id, it.error)
            log.debug("%s", traceback.format_exc())

    async def _call(self, fn, *a):
        r = fn(*a)
        if inspect.isawaitable(r):
            r = await r
        return r

    async def start(self, it):
        if it.mod is None or it.obj is not None or it.crashed or it.halted:
            return
        try:
            it.obj = it.mod.Plugin(Api(self, it.id, it.folder))
            if hasattr(it.obj, "start"):
                await self._call(it.obj.start)
            it.error = ""
            log.info("Плагин «%s» запущен", it.id)
        except Exception as e:
            it.error = f"{type(e).__name__}: {e}"
            log.warning("Плагин «%s» не запустился: %s", it.id, it.error)
            it.obj = None

    # ---------- сбои и ручная остановка ----------
    def fail(self, it, where, e):
        """Ошибка в ui() / action() / input(): часто подряд — плагин останавливаем, остальной дек работает."""
        now = time.monotonic()
        it.fails = [t for t in it.fails if now - t < CRASH_WINDOW] + [now]
        if len(it.fails) >= CRASH_N and not it.crashed and it.obj is not None:
            why = f"{where}: {type(e).__name__}: {e}"[:300]
            it.crashed = i18n.tr("stopped by the deck: {0} errors in a minute — {1}", "зупинений деком: {0} помилок за хвилину — {1}", len(it.fails), why)
            log.warning("Плагин «%s» падает (%d ошибок за минуту) — останавливаю: %s", it.id, len(it.fails), why)
            self.soon(lambda: asyncio.ensure_future(self._crashed(it)))

    async def _crashed(self, it):
        await self.unload(it.id)
        await self.push()
        await self.deck.event("plugin", i18n.tr("Plugin «{0}» stopped: it kept crashing", "Плагін «{0}» зупинено: падав", it.id),
                              level="error", notify=True)

    async def halt(self, pid):
        """«Остановить» — завис или мешает: выгрузить, не выключая (после перезапуска дека или «Перезапустить» — снова)."""
        it = self.items.get(pid)
        if not it:
            return
        it.halted = True
        await self.unload(pid)
        log.info("Плагин «%s» остановлен вручную", pid)
        await self.push()

    async def restart(self, pid):
        """«Перезапустить»: снять «упал / остановлен», заново прочитать файл и запустить."""
        it = self.items.get(pid)
        if not it:
            return
        it.crashed, it.halted, it.fails = "", False, []
        await self.unload(pid)
        if self.enabled(pid) and not self.blocked(it):
            self.read(it)
            if not it.error:
                await self.start(it)
        log.info("Плагин «%s» перезапущен", pid)
        await self.push()

    # ---------- живая картинка (элемент stream) ----------
    def put_frame(self, pid, data):
        seq = self.frames.get(pid, (0, b""))[0] + 1
        self.frames[pid] = (seq, data)
        self.soon(lambda: [ev.set() for ev in list(self.waiters.get(pid, ()))])

    def watch(self, pid, ev, on):
        w = self.waiters.setdefault(pid, set())
        (w.add if on else w.discard)(ev)

    def input(self, pid, e):
        """Касание по потоку: сразу плагину (input), без рассылки состояния — их десятки в секунду."""
        it = self.items.get(pid)
        e = clean_input(e)
        if not e or not it or it.obj is None or not hasattr(it.obj, "input"):
            return
        t0 = time.perf_counter()
        try:
            it.obj.input(e)
        except Exception as ex:
            log.warning("Плагин «%s»: input: %s", pid, ex)
            self.fail(it, "input", ex)
        it.took("input", time.perf_counter() - t0)

    async def unload(self, pid):
        it = self.items.get(pid)
        if not it or it.obj is None:
            return
        self.frames.pop(pid, None)
        for ev in self.waiters.get(pid, ()):
            ev.set()
        obj, it.obj = it.obj, None
        try:
            if hasattr(obj, "stop"):
                await asyncio.wait_for(self._call(obj.stop), 5)
            log.info("Плагин «%s» остановлен", pid)
        except Exception as e:
            log.warning("Плагин «%s»: ошибка при остановке: %s", pid, e)

    async def stop_all(self):
        for pid in list(self.items):
            await self.unload(pid)

    # ---------- включить / выключить ----------
    async def set_on(self, pid, on, grant=None):
        """Включить / выключить. Плагину нужны разрешения, на которые ещё нет согласия, — включаем только с grant:
        списком, который пользователь видел в окне согласия (он должен покрывать всё, что нужно сейчас)."""
        it = self.items.get(pid)
        if it is None:
            return False
        if on and self.missing(it):
            if grant is None or not set(self.missing(it)) <= set(grant):
                await self.push()
                return False
            self.grant(it)
            it.denied.clear()
            log.info("Плагин «%s»: разрешения — %s", pid, ", ".join(it.needed) or "—")
        self.deck.store.cfg.setdefault("plugins", {})[pid] = bool(on)
        self.deck.store.save()
        await self.apply()
        return True

    async def set_master(self, on):
        self.deck.store.cfg["pluginsOn"] = bool(on)
        self.deck.store.save()
        await self.apply()

    async def apply(self):
        for pid, it in self.items.items():
            if self.enabled(pid) and it.obj is None and not it.error and not self.blocked(it) and not it.crashed and not it.halted:
                if it.mod is None:
                    self.read(it)
                await self.start(it)
            elif not self.enabled(pid) or self.blocked(it):
                if it.obj is not None:
                    await self.unload(pid)
                if it.mod is not None:
                    self.forget(it)
        await self.push()

    # ---------- телефон ----------
    def ui_of(self, it):
        if it.obj is None or not hasattr(it.obj, "ui"):
            return {}
        t0 = time.perf_counter()
        try:
            ui = it.obj.ui() or {}
            dt = time.perf_counter() - t0
            it.took("ui", dt)
            if dt > SLOW_UI:
                log.warning("Плагин «%s»: ui() думал %.2f с — дек ждал", it.id, dt)
            return {"dock": clean_widgets(ui.get("dock")), "settings": clean_widgets(ui.get("settings")),
                    "screen": clean_widgets(ui.get("screen")), "tiles": clean_tiles(ui.get("tiles")),
                    "mode": clean_mode(ui.get("mode"), it.id),
                    "ctx": clean_ctx(ui.get("ctx")), "theme": clean_theme(ui.get("theme")), "rev": abs(hash(it.stamp)) % 10 ** 8}
        except Exception as e:
            log.warning("Плагин «%s»: ui(): %s", it.id, e)
            self.fail(it, "ui()", e)
            return {"error": f"ui(): {type(e).__name__}: {e}"}

    def msg(self):
        lang, out = self.lang(), []
        for pid, it in sorted(self.items.items(), key=lambda x: text_of(x[1].name, pid=x[0]).lower() or x[0]):
            ui = self.ui_of(it)
            g = self.granted(pid)
            out.append({"id": pid, "name": text_of(it.name, pid=pid) or pid, "about": text_of(it.about, pid=pid),
                        "langs": it.langs, "langOk": self.lang_ok(it),
                        "on": self.enabled(pid), "running": it.obj is not None, "version": getattr(it, "version", ""),
                        "api": it.api, "apiState": it.api_state, "apiDeck": perms.API_VERSION,
                        "perms": [{"k": k, "label": perms.label(k, lang), "risk": perms.PERMS[k][4],
                                   "about": perms.about(k, lang), "declared": k in it.declared,
                                   "found": it.found.get(k, []), "granted": k in g} for k in it.needed],
                        "missing": self.missing(it), "blocked": self.blocked(it), "unknownPerms": it.unknown,
                        "denied": [{"k": k, "label": perms.label(k, lang), "what": w} for k, w in it.denied.items()],
                        "deprecated": sorted(it.deprecated), "crashed": it.crashed, "halted": it.halted,
                        "error": ui.pop("error", "") or it.error, "file": str(it.path.relative_to(self.folder.parent)), **ui})
        return {"t": "plugins", "on": self.master(), "folder": str(self.folder), "list": out}

    def lang_ok(self, it):
        """Говорит ли плагин на языке дека: заявил его в LANGS или его тексты есть в мовном пакете; LANGS нет — неизвестно (None)."""
        return i18n.speaks(it.id, it.langs)

    def health(self):
        """Для вкладки «Дек» в диагностике: что с каждым плагином и сколько он думает."""
        lang, out = self.lang(), []
        for pid, it in sorted(self.items.items()):
            st = {k: {"n": v[0], "avg": round(v[1] / v[0] * 1000, 1) if v[0] else 0, "max": round(v[2] * 1000, 1)}
                  for k, v in it.stats.items()}
            out.append({"id": pid, "name": text_of(it.name, pid=pid) or pid, "on": self.enabled(pid), "running": it.obj is not None,
                        "crashed": it.crashed, "halted": it.halted, "error": it.error, "blocked": self.blocked(it),
                        "fails": len([t for t in it.fails if time.monotonic() - t < CRASH_WINDOW]), "stats": st})
        return out

    # ---------- удалить ----------
    async def remove(self, pid):
        """Удалить с телефона насовсем: остановить, стереть файл / папку плагина и его прежние версии из каталога
        (plugins/_backups/<id>). Настройки и согласие в конфиге остаются — поставишь снова, будет как было. True — удалён."""
        it = self.items.get(pid)
        if it is None:
            return False
        src = it.path if it.folder == self.folder else it.folder
        await self.unload(pid)
        self.forget(it)

        def wipe():
            if src.is_dir():
                shutil.rmtree(src)
            else:
                src.unlink(missing_ok=True)
                for c in (self.folder / "__pycache__").glob(src.stem + ".*.pyc"):      # кэш однофайлового плагина
                    c.unlink(missing_ok=True)
            shutil.rmtree(self.folder / "_backups" / pid, ignore_errors=True)
        try:
            await asyncio.get_running_loop().run_in_executor(None, wipe)
        except OSError as e:
            log.warning("Плагин «%s»: не удалось удалить: %s", pid, e)
            it.error = f"{type(e).__name__}: {e}"
            await self.push()
            return False
        self.items.pop(pid, None)
        self.frames.pop(pid, None)
        log.info("Плагин «%s» удалён", pid)
        await self.push()
        return True

    def file(self, pid, rel):
        """Файл работающего плагина для страницы (картинки, шрифты) — только из его папки. None — нельзя."""
        it = self.items.get(pid)
        if not it or it.obj is None or it.folder == self.folder or not file_ref(rel):
            return None
        f = (it.folder / rel).resolve()
        try:
            f.relative_to(it.folder.resolve())
        except ValueError:
            return None
        return f if f.is_file() and f.suffix.lower() in FILE_TYPES else None

    async def push(self):
        msg = self.msg()
        await self.sync_modes(msg["list"])
        await self.deck.broadcast(msg)

    async def sync_modes(self, lst):
        """Режимы плагинов («p:<id>»): у работающего плагина, что объявил ui()["mode"]. Поменялся набор или
        названия — новый конфиг телефонам; режим пропал, а дек в нём — назад в прошлый обычный режим."""
        st = self.deck.store
        new = {f"p:{x['id']}": dict(x["mode"], plugin=x["id"]) for x in lst if x.get("running") and x.get("mode")}
        if new == st.plugin_modes:
            return
        st.plugin_modes = new
        if self.deck.mode and self.deck.mode.startswith("p:") and self.deck.mode not in new:
            self.deck.mode = self.deck.base
            await self.deck.broadcast(self.deck.state_msg())
        await self.deck.broadcast(self.deck.config_msg())

    async def action(self, pid, aid, value=None):
        """Нажали кнопку / переключатель / ползунок / плитку плагина."""
        it = self.items.get(pid)
        if not it or it.obj is None:
            raise RuntimeError(i18n.tr("plugin «{0}» is off", "плагін «{0}» вимкнено", pid))
        if not hasattr(it.obj, "action"):
            return None
        t0 = time.perf_counter()
        try:
            r = await self._call(it.obj.action, str(aid), value)
            it.took("action", time.perf_counter() - t0)
        except Exception as e:
            it.took("action", time.perf_counter() - t0)
            self.fail(it, f"action({aid})", e)
            log.warning("Плагин «%s»: action(%s): %s", pid, aid, e)
            await self.deck.toast(f"{text_of(it.name, pid=pid) or pid}: {e}")
            return None
        await self.push()
        return r
