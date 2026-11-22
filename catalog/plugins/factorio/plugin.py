"""Factorio на деке: свой режим «Factorio» и блок в доке — электричество, загрязнение, производство, наука, враги.

Данные пишет мод игры Noryx Link (папка game-mod/claude-deck-link — в моды Factorio 2.0): раз в 5 с файл
script-output/claude-deck/factorio.json в папке данных игры (Windows — %APPDATA%\\Factorio, Linux — ~/.factorio).
Плагин этот файл читает. Игра на паузе или закрыта — файл не обновляется — «нет данных от игры».

Режим: слева узкая колонка кнопок-показателей, справа — выбранный: график (1 мин / 10 мин / 1 ч / 10 ч) и «топ»
(кто больше всех ест электричество, кто больше всех загрязняет, что делается, какая наука…).
"""
import json
import os
import sys
import threading
import time

NAME = {"ru": "Factorio", "uk": "Factorio"}
ABOUT = {"ru": "Статистика завода: электричество, загрязнение, производство, наука, враги — графики. Нужен мод игры Noryx Link",
         "uk": "Статистика заводу: електрика, забруднення, виробництво, наука, вороги — графіки. Потрібен мод гри Noryx Link"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
PERMS = ["files"]
VERSION = "1.0.0"                        # версия плагина в каталоге (catalog/index.json): подними — деки предложат обновиться
CHANGES = [("1.0.0", "Первая версия в каталоге: электричество, производство, наука, графики",
            "Перша версія в каталозі: електрика, виробництво, наука, графіки")]
REL = os.path.join("script-output", "noryx", "factorio.json")
LIVE = 15.0                                   # столько секунд файл не обновлялся — «нет данных от игры»

# в духе интерфейса игры: тёмно-серые панели, оранжевый акцент
THEME = {"bg": "#1F1E1D", "panel": "#2F2E2C", "border": "#121110", "text": "#ECE5D6", "muted": "#A39D90",
         "accent": "#F4A300", "radius": 4}

RANGES = [("m1", 12, ("1 мин", "1 хв"), (("-1 мин", "-1 хв"), ("-30 с", "-30 с"))),
          ("m10", 60, ("10 мин", "10 хв"), (("-10 мин", "-10 хв"), ("-5 мин", "-5 хв"))),
          ("h1", 60, ("1 ч", "1 год"), (("-1 ч", "-1 год"), ("-30 мин", "-30 хв"))),
          ("h10", 60, ("10 ч", "10 год"), (("-10 ч", "-10 год"), ("-5 ч", "-5 год")))]

# вкладка: (название ru/uk, значок, цвет, главный показатель, единица, si, линии графика, топ)
TABS = {
    "power": (("Электричество", "Електрика"), "⚡", "#F4C430", "pc", "W", True,
              [("pp", ("Выработка", "Вироблено"), "#3DDC6A", True), ("pc", ("Потребление", "Спожито"), "#F4A300", False)],
              ("pc", ("Больше всех потребляют", "Найбільше споживають"))),
    "pollution": (("Загрязнение", "Забруднення"), "☁", "#9DB36B", "pe", "/мин", True,
                  [("pe", ("Выбросы", "Викиди"), "#C08A4A", True), ("pa", ("Поглощение", "Поглинання"), "#5E9A3A", False)],
                  ("pe", ("Больше всех загрязняют", "Найбільше забруднюють"))),
    "items": (("Производство", "Виробництво"), "⚙", "#E39B3A", "im", "/мин", True,
              [("im", ("Сделано", "Зроблено"), "#E39B3A", True), ("iu", ("Потрачено", "Витрачено"), "#7FB2D9", False)],
              ("im", ("Больше всего делается", "Найбільше робиться"))),
    "science": (("Наука", "Наука"), "⚗", "#5DA9E9", "sc", "/мин", True,
                [("sc", ("Наука в лаборатории", "Наука в лабораторії"), "#5DA9E9", True)],
                ("sc", ("По видам", "За видами"))),
    "enemies": (("Враги", "Вороги"), "☣", "#C8432F", "ev", "%", False,
                [("ev", ("Эволюция", "Еволюція"), "#C8432F", True)],
                None),
}
ORDER = ["power", "pollution", "items", "science", "enemies"]


def pretty(name):
    """«electric-mining-drill» → «Electric mining drill» (названия из игры — внутренние, без перевода)."""
    s = str(name).replace("-", " ").replace("_", " ").strip()
    return s[:1].upper() + s[1:] if s else "?"


def si(v, unit=""):
    if not isinstance(v, (int, float)):
        return "—"
    a, p = abs(v), ""
    for k, s in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k")):
        if a >= k:
            v, a, p = v / k, a / k, s
            break
    n = round(v) if a >= 100 else round(v, 1) if a >= 10 else round(v, 2)
    return f"{n:g} {p}{unit}".strip() if (p or unit) else f"{n:g}"


def lst(x):
    """Пустую таблицу игра пишет в JSON как {} — считаем её пустым списком."""
    return x if isinstance(x, list) else []


def default_files():
    """Где игра пишет script-output: Windows — %APPDATA%\\Factorio, Linux — ~/.factorio (и Steam во Flatpak)."""
    home = os.path.expanduser("~")
    if sys.platform == "win32":
        bases = [os.path.join(os.environ.get("APPDATA", os.path.join(home, "AppData", "Roaming")), "Factorio")]
    else:
        bases = [os.path.join(home, ".factorio"), os.path.join(home, ".var", "app", "com.valvesoftware.Steam", ".factorio"),
                 os.path.join(home, ".var", "app", "com.factorio.Factorio", ".factorio")]
    return [os.path.join(b, REL) for b in bases]


class Plugin:
    def __init__(self, api):
        self.api = api
        self.data, self.mtime, self.file = {}, 0.0, None
        self.run = False
        self.tab = api.cfg.get("tab") if api.cfg.get("tab") in TABS else "power"
        self.range = api.cfg.get("range") if api.cfg.get("range") in [r[0] for r in RANGES] else "m10"

    # ---------- чтение файла от мода ----------
    def start(self):
        self.run = True
        threading.Thread(target=self.watch, name="factorio", daemon=True).start()

    def stop(self):
        self.run = False

    def files(self):
        own = (self.api.cfg.get("dir") or "").strip()
        if own:
            own = os.path.expanduser(own)
            return [own] if own.lower().endswith(".json") else [os.path.join(own, REL), os.path.join(own, "noryx", "factorio.json")]
        return default_files()

    def watch(self):
        while self.run:
            self.poll()
            time.sleep(1.0)

    def poll(self):
        best = None
        for f in self.files():
            try:
                m = os.path.getmtime(f)
            except OSError:
                continue
            if not best or m > best[1]:
                best = (f, m)
        if not best or (best[0] == self.file and best[1] == self.mtime):
            return False
        try:
            with open(best[0], encoding="utf-8") as fh:
                d = json.load(fh)
        except (OSError, ValueError):
            return False                    # игра как раз пишет файл — прочтём в следующий раз
        if not isinstance(d, dict) or not isinstance(d.get("series"), dict):
            return False
        self.file, self.mtime = best[0], best[1]
        self.api.call(lambda: self.got(d))
        return True

    def got(self, d):
        self.data = d
        self.api.update()

    def live(self):
        return bool(self.data) and time.time() - self.mtime < LIVE

    # ---------- телефон ----------
    def action(self, aid, value):
        if aid.startswith("tab:") and aid[4:] in TABS:
            self.tab = aid[4:]
            self.api.cfg["tab"] = self.tab
        elif aid.startswith("range:") and aid[6:] in [r[0] for r in RANGES]:
            self.range = aid[6:]
            self.api.cfg["range"] = self.range
        elif aid == "dir":
            self.api.cfg["dir"] = str(value or "").strip()[:300]
            self.file, self.mtime = None, 0.0
            threading.Thread(target=self.poll, daemon=True).start()
        else:
            return
        self.api.save()

    def now(self, key):
        now = self.data.get("now")
        v = now.get(key) if isinstance(now, dict) else None
        return v if isinstance(v, (int, float)) else None

    def unit(self, u):
        return self.api.tr(u, u.replace("/мин", "/хв"))

    def fmt(self, key, unit, use_si=True):
        v = self.now(key)
        if v is None or not self.data:
            return "—"
        return si(v, self.unit(unit)) if use_si else f"{v:.1f}{self.unit(unit)}"

    def where(self):
        d = self.data
        return d.get("planet") or d.get("surface") or ""

    def ui(self):
        tr, live = self.api.tr, self.live()
        note = [] if live else [{"type": "label", "style": "line", "color": "#A39D90", "label": self.why()}]
        return {
            "theme": THEME,
            "mode": {"name": "Factorio", "icon": "gauge", "color": "#F4A300",
                     "sub": {"ru": f"Завод: {pretty(self.where())}" if live else "Нет данных от игры",
                             "uk": f"Завод: {pretty(self.where())}" if live else "Немає даних від гри"}},
            "dock": [{"type": "card", "go": "mode", "title": "Factorio", "icon": "⚙", "items": note + [
                {"type": "text", "label": "⚡", "value": self.fmt("pc", "W")},           # коротко: док узкий
                {"type": "text", "label": "☁", "value": self.fmt("pe", "/мин")},
                {"type": "text", "label": "⚗", "value": self.fmt("sc", "/мин")}]}],
            "screen": [{"type": "grid", "widths": [1, 3.6], "fill": True, "items": [
                {"type": "grid", "cols": 1, "items": self.tabs()},
                {"type": "card", "scroll": True, "items": note + self.page()}]}],
            "settings": [
                {"type": "text", "label": tr("Мод игры", "Мод гри"),
                 "value": tr("на связи", "на зв'язку") if live else tr("нет данных", "немає даних")},
                {"type": "text", "label": tr("Файл", "Файл"), "value": self.file or self.files()[0]},
                *([{"type": "text", "label": tr("Электросети", "Електромережі"), "value": tr(
                    f'номеров {self.data["nets"].get("ids", 0)} → разных {self.data["nets"].get("real", 0)}',
                    f'номерів {self.data["nets"].get("ids", 0)} → різних {self.data["nets"].get("real", 0)}')}]
                  if isinstance(self.data.get("nets"), dict) else []),
                *([{"type": "label", "style": "line", "color": "#E07A4F",
                    "label": tr("Мод игры не смог прочесть: ", "Мод гри не зміг прочитати: ") + str(self.data["err"])[:300]}]
                  if self.data.get("err") else []),
                {"type": "label", "style": "line", "label": tr(
                    "Мод Noryx Link (game-mod) — в папку mods игры. Своя папка данных игры — ниже (пусто — как обычно).",
                    "Мод Noryx Link (game-mod) — у теку mods гри. Своя тека даних гри — нижче (порожньо — як зазвичай).")},
                {"type": "input", "id": "dir", "placeholder": self.api.cfg.get("dir") or tr("Папка данных Factorio", "Тека даних Factorio"),
                 "button": tr("Сохранить", "Зберегти")}],
        }

    def why(self):
        tr = self.api.tr
        if not self.data:
            return tr("Нет данных от игры: запусти Factorio с модом Noryx Link.",
                      "Немає даних від гри: запусти Factorio з модом Noryx Link.")
        return tr("Игра на паузе или закрыта — показываю последние данные.",
                  "Гра на паузі або закрита — показую останні дані.")

    def tabs(self):
        tr, out = self.api.tr, []
        for k in ORDER:
            (ru, uk), icon, col, key, unit, use_si, _lines, _top = TABS[k]
            sub = ""
            if k == "power":
                sub = tr("выработка ", "вироблено ") + self.fmt("pp", "W")
            elif k == "pollution":
                sub = tr("всего ", "всього ") + self.fmt("pt", "", True)
            elif k == "items":
                sub = tr("потрачено ", "витрачено ") + self.fmt("iu", "/мин")
            elif k == "science":
                r = self.data.get("research") or {}
                sub = f'{pretty(r.get("name"))} {r.get("progress", 0):g}%' if r.get("name") else tr("ничего не изучается", "нічого не вивчається")
            elif k == "enemies":
                sub = tr("убито ", "вбито ") + self.fmt("kl", "/мин")
            out.append({"type": "stat", "id": "tab:" + k, "on": self.tab == k, "icon": icon, "color": col,
                        "label": tr(ru, uk), "value": self.fmt(key, unit, use_si) if k != "enemies" else self.fmt("ev", "%", False),
                        "sub": sub})
        return out

    def series(self, key, n):
        vals = lst(((self.data.get("series") or {}).get(key) or {}).get(self.range))[-n:]
        vals = [v if isinstance(v, (int, float)) else None for v in vals]
        return [None] * (n - len(vals)) + vals            # история короче диапазона — слева пусто, «сейчас» справа

    def page(self):
        tr = self.api.tr
        (ru, uk), icon, col, key, unit, use_si, lines, top = TABS[self.tab]
        rng = next(r for r in RANGES if r[0] == self.range)
        n, xs = rng[1], [tr(*rng[3][0]), tr(*rng[3][1]), tr("сейчас", "зараз")]
        head = [{"type": "row", "items": [{"type": "button", "id": "range:" + r[0], "label": tr(*r[2]), "on": r[0] == self.range}
                                          for r in RANGES]}]
        chart = {"type": "chart", "label": f"{icon} {tr(ru, uk)}", "sub": pretty(self.where()) if self.data else "",
                 "unit": self.unit(unit), "si": use_si, "h": 170, "xs": xs,
                 "series": [{"label": tr(*lab), "color": c, "fill": fill, "values": self.series(k, n)} for k, lab, c, fill in lines]}
        if self.tab == "enemies":
            chart["min"], chart["max"] = 0, 100
        body = head + [chart]
        if self.tab == "science":
            r = self.data.get("research") or {}
            body.append({"type": "bar", "label": tr("Исследуется: ", "Досліджується: ") + (pretty(r["name"]) if r.get("name") else "—"),
                         "value": r.get("progress", 0) if r.get("name") else 0, "max": 100, "color": "#5DA9E9", "size": 10,
                         "text": f'{r.get("progress", 0):g}%' if r.get("name") else ""})
        if self.tab == "enemies":
            body.append({"type": "grid", "cols": 2, "items": [
                {"type": "stat", "icon": "☣", "label": tr("Эволюция", "Еволюція"), "value": self.fmt("ev", "%", False), "color": "#C8432F"},
                {"type": "stat", "icon": "⚔", "label": tr("Убито в минуту", "Вбито за хвилину"), "value": self.fmt("kl", "")}]})
        tops = [top] if top else []
        if self.tab == "power":                 # у электричества — и кто вырабатывает
            tops.append(("pp", ("Больше всех вырабатывают", "Найбільше виробляють"), "#3DDC6A"))
        for t in tops:
            rows = lst((self.data.get("top") or {}).get(t[0]))[:8]
            rows = [r for r in rows if isinstance(r, list) and len(r) == 2 and isinstance(r[1], (int, float))]
            if rows:
                mx = max(r[1] for r in rows) or 1
                c = t[2] if len(t) > 2 else col
                body.append({"type": "label", "label": tr(*t[1])})
                body += [{"type": "bar", "label": pretty(name), "value": v, "max": mx, "color": c, "size": 8,
                          "text": si(v, self.unit(unit))} for name, v in rows]
        return body
