"""Satisfactory на деке: свой режим «Satisfactory» и блок в доке — электричество, батареи, производство, AWESOME Sink.

Свой мод игры тут не нужен: данные даёт мод Ficsit Remote Monitoring (FRM, ставится через Satisfactory Mod Manager).
Он поднимает в игре HTTP-сервер (по умолчанию порт 8080; запуск — в чате «/frm http start» или «автозапуск»
в настройках мода), плагин раз в 5 с спрашивает у него /api/getPower, /api/getProdStats, /api/getResourceSink и
/api/getSessionInfo, раз в 30 с — /api/getPowerUsage (кто сколько ест). Историю для графиков (1 мин / 10 мин /
1 ч / 10 ч) копит сам плагин (FRM даёт только «сейчас») и бережёт в history.json рядом с собой — по сессии игры.

Режим: слева узкая колонка кнопок-показателей, справа — выбранный: график и «топ», как у Factorio.
"""
import json
import os
import threading
import time
import urllib.request

NAME = {"en": "Satisfactory", "uk": "Satisfactory"}
ABOUT = {"en": "Factory stats: electricity, batteries, production, AWESOME Sink — charts. Needs the "
               "Ficsit Remote Monitoring game mod",
         "uk": "Статистика заводу: електрика, батареї, виробництво, AWESOME Sink — графіки. Потрібен мод гри Ficsit Remote Monitoring"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
LANGS = ["en", "uk"]                     # на каких языках свои тексты (первый — основной); другие — из языковых пакетов дека
PERMS = ["network", "files"]           # files — читать токен из конфига мода FRM в папке игры
VERSION = "1.2.0"                        # версия плагина в каталоге (catalog/index.json): подними — деки предложат обновиться
CHANGES = [("1.2.0", "English is now the main language: the plugin speaks English by default, Ukrainian is built in, other languages come from the deck's language packs",
            "Основна мова тепер англійська: плагін англійською за замовчуванням, українська вбудована, інші мови — з мовних пакетів деку"),
           ("1.1.0", "FRM token (API Authentication Token): a field in settings, read from the mod config "
                     "automatically; if its own address doesn't answer — looks for FRM on other ports; the «no"
                     " connection» reason — in the deck log",
            "Токен FRM (API Authentication Token): поле в налаштуваннях, сам читає з конфігу мода; своя адреса не відповідає — "
            "шукає FRM на інших портах; причина «немає зв'язку» — у лозі дека"),
           ("1.0.0", "First version in the catalog: data from Ficsit Remote Monitoring",
            "Перша версія в каталозі: дані з Ficsit Remote Monitoring")]
DEFAULT_ON = False

EVERY = 5.0                 # секунд между снимками
USAGE_EVERY = 30.0          # «кто сколько ест» — тяжёлый запрос (все постройки), реже
LIVE = 20.0                 # столько секунд нет ответа — «нет данных от игры»
TIMEOUT = 4.0
KEEP5, KEEP = 120, 60       # снимков по 5 с (10 мин) и точек в часовой / десятичасовой истории
TOP = 8
HIST_FILE = "history.json"
PORTS = list(range(8080, 8090))     # где искать FRM, если свой адрес не задан

# в духе интерфейса игры: тёмные панели, оранжевый FICSIT
THEME = {"bg": "#16181C", "panel": "#23262C", "border": "#0C0D10", "text": "#EDEFF2", "muted": "#9AA1AB",
         "accent": "#FA9549", "radius": 6}

RANGES = [("m1", 12, ("1 min", "1 хв"), (("-1 min", "-1 хв"), ("-30 s", "-30 с"))),
          ("m10", 60, ("10 min", "10 хв"), (("-10 min", "-10 хв"), ("-5 min", "-5 хв"))),
          ("h1", 60, ("1 h", "1 год"), (("-1 h", "-1 год"), ("-30 min", "-30 хв"))),
          ("h10", 60, ("10 h", "10 год"), (("-10 h", "-10 год"), ("-5 h", "-5 год")))]

# показатели: pp выработка, pc потребление, cap мощность (Вт); bat заряд (%), bi / bo в батареи / из батарей (Вт);
# im / iu предметы сделано / потрачено (шт/мин, без жидкостей), eff загрузка заводов (%); sp очки Sink в минуту
METRICS = ["pp", "pc", "cap", "bat", "bi", "bo", "im", "iu", "eff", "sp"]

# вкладка: (название ru/uk, значок, цвет, главный показатель, единица, si, линии графика, топ)
TABS = {
    "power": (("Electricity", "Електрика"), "⚡", "#F2C94C", "pc", "W", True,
              [("pp", ("Generation", "Вироблено"), "#3DDC6A", True), ("pc", ("Consumption", "Спожито"), "#FA9549", False),
               ("cap", ("Power", "Потужність"), "#6B7280", False)],
              ("pw", ("Top consumers", "Найбільше споживають"))),
    "battery": (("Batteries", "Батареї"), "🔋", "#4FC3F7", "bat", "%", False,
                [("bat", ("Charge", "Заряд"), "#4FC3F7", True)],
                None),
    "items": (("Production", "Виробництво"), "⚙", "#FA9549", "im", "/min", True,
              [("im", ("Made", "Зроблено"), "#FA9549", True), ("iu", ("Spent", "Витрачено"), "#7FB2D9", False)],
              ("im", ("Made the most", "Найбільше робиться"))),
    "sink": (("AWESOME Sink", "AWESOME Sink"), "◎", "#B57BFF", "sp", "/min", True,
             [("sp", ("Points", "Очки"), "#B57BFF", True)],
             None),
}
ORDER = ["power", "battery", "items", "sink"]


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


def num(x, default=0.0):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else default


def lst(x):
    return x if isinstance(x, list) else []


def mean(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return sum(xs) / len(xs) if xs else None


def rnd(x):
    if not isinstance(x, (int, float)):
        return None
    return round(x) if abs(x) >= 100 else round(x, 2)


_OPEN = urllib.request.build_opener(urllib.request.ProxyHandler({}))     # системный прокси к игре на этом ПК не нужен


def http_get(url, timeout=TIMEOUT, token=""):
    """GET к FRM; новые версии мода просят токен в заголовке X-FRM-Authorization (API Authentication Token)."""
    req = urllib.request.Request(url, headers={"X-FRM-Authorization": token} if token else {})
    with _OPEN.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _token_in(d):
    """Authentication_Token где угодно в конфиге мода (SML пишет JSON, вложенность бывает разной)."""
    if isinstance(d, dict):
        for k, v in d.items():
            if k == "Authentication_Token" and isinstance(v, str) and v.strip():
                return v.strip()
            t = _token_in(v)
            if t:
                return t
    elif isinstance(d, list):
        for v in d:
            t = _token_in(v)
            if t:
                return t
    return ""


def game_dirs():
    """Где стоит Satisfactory: Epic (манифесты лаунчера) и Steam (обычная папка библиотек). Только Windows; молча."""
    out = []
    pd = os.environ.get("ProgramData") or r"C:\ProgramData"
    man = os.path.join(pd, "Epic", "EpicGamesLauncher", "Data", "Manifests")
    try:
        for f in os.listdir(man):
            if f.endswith(".item"):
                try:
                    with open(os.path.join(man, f), encoding="utf-8") as fh:
                        m = json.load(fh)
                except (OSError, ValueError):
                    continue
                if "satisfactory" in str(m.get("DisplayName", "")).lower() and m.get("InstallLocation"):
                    out.append(m["InstallLocation"])
    except OSError:
        pass
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            out.append(os.path.join(base, "Steam", "steamapps", "common", "Satisfactory"))
    return out


def find_token(dirs=None):
    """Токен из Configs/FicsitRemoteMonitoring/WebServer.cfg в папке игры (FRM создаёт его сам)."""
    for d in dirs if dirs is not None else game_dirs():
        f = os.path.join(d, "FactoryGame", "Configs", "FicsitRemoteMonitoring", "WebServer.cfg")
        try:
            with open(f, encoding="utf-8") as fh:
                t = _token_in(json.load(fh))
        except (OSError, ValueError):
            continue
        if t:
            return t
    return ""


# ---------- разбор ответов FRM (без сети — проверяется тестом) ----------
def power(circuits):
    """getPower: по группе цепей; МВт → Вт. Заряд батарей — по всем группам (вес — ёмкость)."""
    pp = pc = cap = bi = bo = store = full = 0.0
    fuse = False
    for c in lst(circuits):
        if not isinstance(c, dict):
            continue
        pp += num(c.get("PowerProduction"))
        pc += num(c.get("PowerConsumed"))
        cap += num(c.get("PowerCapacity"))
        bi += num(c.get("BatteryInput"))
        bo += num(c.get("BatteryOutput"))
        bc = num(c.get("BatteryCapacity"))
        full += bc
        store += bc * num(c.get("BatteryPercent")) / 100
        fuse = fuse or c.get("FuseTriggered") is True
    return {"pp": pp * 1e6, "pc": pc * 1e6, "cap": cap * 1e6, "bi": bi * 1e6, "bo": bo * 1e6,
            "bat": store / full * 100 if full > 0 else None}, fuse, full


def prod(items):
    """getProdStats: предметы в минуту; жидкости и газы (м³) в суммы не идут — только в «топ»."""
    made = used = cur = mx = 0.0
    top = []
    for it in lst(items):
        if not isinstance(it, dict):
            continue
        p, c = num(it.get("CurrentProd")), num(it.get("CurrentConsumed"))
        fluid = it.get("Type") in ("Liquid", "Gas")
        if not fluid:
            made += p
            used += c
        cur += p
        mx += num(it.get("MaxProd"))
        if p > 0:
            top.append([str(it.get("Name") or it.get("ClassName") or "?")[:40], rnd(p), fluid])
    top.sort(key=lambda r: -r[1])
    return {"im": made, "iu": used, "eff": cur / mx * 100 if mx > 0 else None}, top[:TOP]


def usage(buildings):
    """getPowerUsage: кто сколько ест, по видам построек (МВт → Вт)."""
    agg = {}
    for b in lst(buildings):
        if not isinstance(b, dict):
            continue
        pi = b.get("PowerInfo") if isinstance(b.get("PowerInfo"), dict) else {}
        w = num(pi.get("PowerConsumed"))
        if w > 0:
            name = str(b.get("Name") or "?")[:40]
            agg[name] = agg.get(name, 0.0) + w * 1e6
    top = sorted(agg.items(), key=lambda kv: -kv[1])[:TOP]
    return [[k, rnd(v)] for k, v in top]


def sink(rows):
    """getResourceSink: первая (обычная) дорожка."""
    for r in lst(rows):
        if isinstance(r, dict):
            return {"total": num(r.get("TotalPoints")), "coupons": int(num(r.get("NumCoupon"))),
                    "pct": num(r.get("Percent")) * (100 if num(r.get("Percent")) <= 1 else 1),
                    "next": num(r.get("PointsToCoupon"))}
    return None


class History:
    """Снимки по 5 с; средние за минуту и за 10 минут — как у мода Factorio."""

    def __init__(self, d=None):
        d = d if isinstance(d, dict) else {}
        self.b5 = {m: self._l(d, "b5", m, KEEP5) for m in METRICS}
        self.b60 = {m: self._l(d, "b60", m, KEEP) for m in METRICS}
        self.b600 = {m: self._l(d, "b600", m, KEEP) for m in METRICS}
        self.acc1 = {m: [] for m in METRICS}
        self.acc10 = {m: [] for m in METRICS}

    @staticmethod
    def _l(d, k, m, keep):
        v = (d.get(k) or {}).get(m) if isinstance(d.get(k), dict) else None
        return [x if isinstance(x, (int, float)) else None for x in lst(v)][-keep:]

    @staticmethod
    def _push(lst_, v, keep):
        lst_.append(v)
        del lst_[:-keep]

    def add(self, v):
        for m in METRICS:
            x = v.get(m)
            x = x if isinstance(x, (int, float)) else None
            self._push(self.b5[m], x, KEEP5)
            self.acc1[m].append(x)
        if len(self.acc1[METRICS[0]]) >= 12:
            for m in METRICS:
                a = mean(self.acc1[m])
                self.acc1[m] = []
                self._push(self.b60[m], a, KEEP)
                self.acc10[m].append(a)
            if len(self.acc10[METRICS[0]]) >= 10:
                for m in METRICS:
                    self._push(self.b600[m], mean(self.acc10[m]), KEEP)
                    self.acc10[m] = []

    def series(self, m, rng):
        b5 = self.b5[m]
        if rng == "m1":
            v = b5[-12:]
        elif rng == "m10":                      # 120 снимков по 5 с → 60 точек по 10 с
            v = [mean(b5[i:i + 2]) for i in range(len(b5) % 2, len(b5), 2)]
        elif rng == "h1":
            v = self.b60[m]
        else:
            v = self.b600[m]
        return [rnd(x) for x in v]

    def dump(self):
        return {"b5": self.b5, "b60": self.b60, "b600": self.b600}


class Plugin:
    def __init__(self, api, get=http_get):
        self.api, self.get = api, get
        self.run = False
        self.now, self.top, self.fuse, self.batcap = {}, {}, False, 0.0
        self.session, self.paused, self.err = "", False, ""
        self.found = ""                         # где нашёлся FRM (своего адреса нет или он молчит)
        self.auto_token = ""                    # токен из конфига мода (своего в настройках нет)
        self.seen = 0.0                         # когда FRM последний раз ответил
        self.last_usage = 0.0
        self.sink_prev = None                   # (время, очки) — очков в минуту по разнице
        self.sinkv = None
        self.hist, self.saved = History(), 0.0
        self.tab = api.cfg.get("tab") if api.cfg.get("tab") in TABS else "power"
        self.range = api.cfg.get("range") if api.cfg.get("range") in [r[0] for r in RANGES] else "m10"

    # ---------- опрос FRM ----------
    def start(self):
        self.run = True
        threading.Thread(target=self.loop, name="satisfactory", daemon=True).start()

    def stop(self):
        self.run = False
        self.store()

    @staticmethod
    def url(a):
        a = a if "://" in a else "http://" + a
        return a.rstrip("/")

    def base(self):
        own = (self.api.cfg.get("addr") or "").strip()
        return self.found or (self.url(own) if own else self.url("127.0.0.1:%d" % PORTS[0]))

    def token(self):
        return (self.api.cfg.get("token") or "").strip() or self.auto_token

    def fetch(self, url, timeout=TIMEOUT):
        t = self.token()
        return self.get(url, timeout=timeout, token=t) if t else self.get(url, timeout=timeout)

    def is_frm(self, base):
        """Отвечает ли тут FRM: getSessionInfo — объект с SessionName (на 8080 любит сидеть отладка Steam / CEF)."""
        try:
            d = self.fetch(f"{base}/api/getSessionInfo", timeout=1.5)
        except Exception:
            return None
        d = d[0] if isinstance(d, list) and d else d
        return d if isinstance(d, dict) and "SessionName" in d else None

    def find(self):
        """Где сейчас спрашивали — молчит: ищем FRM на 127.0.0.1:8080…8089 (порт в настройках мода могли поменять).
        Свой адрес из настроек тоже не догма — найдётся на другом порту, берём его (в настройках видно)."""
        bad = self.base()
        for p in PORTS:
            b = self.url("127.0.0.1:%d" % p)
            if b != bad and self.is_frm(b) is not None:
                self.found = b
                return True
        own = (self.api.cfg.get("addr") or "").strip()
        if own and self.found:                      # нигде нет — снова спрашиваем свой адрес
            self.found = ""
        return False

    def explain(self, e):
        tr, b = self.api.tr, self.base()
        code = getattr(e, "code", None)
        if code in (401, 403):
            return tr("{0}: FRM asks for a token (HTTP {1}). Copy the «API Authentication Token» from the Ficsit Remote Monitoring mod settings into the «Token» field below.", "{0}: FRM просить токен (HTTP {1}). Скопіюй «API Authentication Token» з налаштувань мода Ficsit Remote Monitoring у поле «Токен» нижче.", b, code)
        if isinstance(code, int) or isinstance(e, ValueError):
            return tr("Something other than Ficsit Remote Monitoring answers on {0} (HTTP {1}) — the port is taken by another program (often Steam debugging). Set another port in the FRM mod settings (e.g. 8081).", "На {0} відповідає не Ficsit Remote Monitoring (HTTP {1}) — порт зайняла інша програма (часто — налагодження Steam). Постав у налаштуваннях мода FRM інший порт (наприклад 8081).", b, code or '?')
        return f"{b}: " + str(getattr(e, "reason", "") or e)[:160]

    def loop(self):
        while self.run:
            t = time.monotonic()
            self.poll()
            time.sleep(max(0.5, EVERY - (time.monotonic() - t)))

    def ask(self, ep):
        return self.fetch(f"{self.base()}/api/{ep}")

    def poll(self, again=True):
        try:
            info = self.ask("getSessionInfo")
            info = info[0] if isinstance(info, list) and info else info
            if not isinstance(info, dict) or "SessionName" not in info:
                raise ValueError("not FRM")
            pw = self.ask("getPower")
        except Exception as e:                      # игры нет / сервер FRM не запущен / порт занят чужим / нужен токен
            if again and getattr(e, "code", None) in (401, 403) and not self.token():
                self.auto_token = find_token()      # токен — в конфиге мода в папке игры
                if self.auto_token:
                    return self.poll(False)
            if again and self.find():
                return self.poll(False)
            err = self.explain(e)
            if err != self.err:
                self.err = err
                if err:
                    self.api.log.warning("Satisfactory: %s", err)
                self.api.call(self.api.update)
            return False
        v, fuse, batcap = power(pw)
        top = dict(self.top)
        errs = []
        try:
            pv, top["im"] = prod(self.ask("getProdStats"))
            v.update(pv)
        except Exception as e:
            errs.append("getProdStats: " + str(e)[:120])
        try:
            s = sink(self.ask("getResourceSink"))
        except Exception as e:
            s = None
            errs.append("getResourceSink: " + str(e)[:120])
        mono = time.monotonic()
        if mono - self.last_usage >= USAGE_EVERY or "pw" not in top:
            try:
                top["pw"] = usage(self.ask("getPowerUsage"))
                self.last_usage = mono
            except Exception as e:
                errs.append("getPowerUsage: " + str(e)[:120])
        name = str(info.get("SessionName") or "")[:60]
        paused = info.get("IsPaused") is True
        self.api.call(lambda: self.got(name, paused, v, top, fuse, batcap, s, errs, mono))
        return True

    def got(self, name, paused, v, top, fuse, batcap, s, errs, mono):
        if name != self.session:
            self.store()
            self.session = name
            self.hist = History(self.load(name))
            self.sink_prev = None
        if s:
            if self.sink_prev and not paused and mono > self.sink_prev[0]:
                v["sp"] = max(0.0, s["total"] - self.sink_prev[1]) * 60 / (mono - self.sink_prev[0])
            elif self.sink_prev and paused:
                v["sp"] = self.now.get("sp")
            self.sink_prev = (mono, s["total"])
        self.sinkv = s
        self.now, self.top, self.fuse, self.batcap = v, top, fuse, batcap
        self.paused, self.err, self.seen = paused, "; ".join(errs), time.time()
        if not paused:                          # на паузе завод стоит — история не идёт
            self.hist.add(v)
        if time.time() - self.saved > 60:
            self.store()
        self.api.update()

    # ---------- история на диске (по сессии игры) ----------
    def hist_path(self):
        return os.path.join(str(getattr(self.api, "folder", "") or "."), HIST_FILE)

    def load(self, name):
        try:
            with open(self.hist_path(), encoding="utf-8") as f:
                d = json.load(f)
            return (d.get("sessions") or {}).get(name)
        except (OSError, ValueError, AttributeError):
            return None

    def store(self):
        if not self.session:
            return
        self.saved = time.time()
        p = self.hist_path()
        try:
            try:
                with open(p, encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                d = {}
            ss = d.get("sessions") if isinstance(d.get("sessions"), dict) else {}
            ss[self.session] = self.hist.dump()
            ss = dict(list(ss.items())[-5:])          # последние 5 сохранений игры
            tmp = p + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"sessions": ss}, f)
            os.replace(tmp, p)
        except OSError:
            pass

    def live(self):
        return bool(self.now) and time.time() - self.seen < LIVE

    # ---------- телефон ----------
    def action(self, aid, value):
        if aid.startswith("tab:") and aid[4:] in TABS:
            self.tab = aid[4:]
            self.api.cfg["tab"] = self.tab
        elif aid.startswith("range:") and aid[6:] in [r[0] for r in RANGES]:
            self.range = aid[6:]
            self.api.cfg["range"] = self.range
        elif aid in ("addr", "token"):
            self.api.cfg[aid] = str(value or "").strip()[:200]
            self.err, self.found = "", ""
            threading.Thread(target=self.poll, daemon=True).start()
        else:
            return
        self.api.save()
        self.api.update()

    def unit(self, u):
        return self.api.tr(u, u.replace("/min", "/хв"))

    def fmt(self, key, unit, use_si=True):
        v = self.now.get(key)
        if not isinstance(v, (int, float)):
            return "—"
        return si(v, self.unit(unit)) if use_si else f"{v:.0f}{self.unit(unit)}"

    def why(self):
        tr = self.api.tr
        if not self.now:
            return tr("No data from the game: start Satisfactory with the Ficsit Remote Monitoring mod and its "
                      "web server (in the game chat: /frm http start).",
                      "Немає даних від гри: запусти Satisfactory з модом Ficsit Remote Monitoring і його веб-сервером "
                      "(у чаті гри: /frm http start).")
        return tr("The game is closed — showing the last data.", "Гра закрита — показую останні дані.")

    def notes(self, live):
        tr, out = self.api.tr, []
        if not live:
            out.append({"type": "label", "style": "line", "color": "#9AA1AB", "label": self.why()})
        elif self.paused:
            out.append({"type": "label", "style": "line", "color": "#9AA1AB", "label": tr("The game is paused.", "Гра на паузі.")})
        if live and self.fuse:
            out.append({"type": "label", "style": "line", "color": "#E5484D",
                        "label": tr("⚠ The fuse blew!", "⚠ Вибило запобіжник!")})
        return out

    def ui(self):
        tr, live = self.api.tr, self.live()
        note = self.notes(live)
        return {
            "theme": THEME,
            "mode": {"name": "Satisfactory", "icon": "gauge", "color": "#FA9549",
                     "sub": {"en": f"Factory: {self.session}" if live and self.session else ("Factory" if live else "No data from the game"),
                             "uk": f"Завод: {self.session}" if live and self.session else ("Завод" if live else "Немає даних від гри")}},
            "dock": [{"type": "card", "go": "mode", "title": "Satisfactory", "icon": "⚙", "items": note + [
                {"type": "text", "label": "⚡", "value": f'{self.fmt("pc", "W")} / {self.fmt("cap", "W")}'},
                {"type": "text", "label": "⚙", "value": self.fmt("im", "/min")},
                {"type": "text", "label": "◎", "value": self.fmt("sp", "/min")}]}],
            "screen": [{"type": "grid", "widths": [1, 3.6], "fill": True, "items": [
                {"type": "grid", "cols": 1, "items": self.tabs()},
                {"type": "card", "scroll": True, "items": note + self.page()}]}],
            "settings": [
                {"type": "text", "label": "Ficsit Remote Monitoring",
                 "value": tr("online", "на зв'язку") if live else tr("no connection", "немає зв'язку")},
                {"type": "text", "label": tr("Address", "Адреса"), "value": self.base()},
                *([{"type": "label", "style": "line", "color": "#E07A4F", "label": self.err[:300]}] if self.err else []),
                {"type": "label", "style": "line", "label": tr(
                    "Ficsit Remote Monitoring mod — via Satisfactory Mod Manager. In the game: /frm http "
                    "start (or web server autostart in the mod settings). Another PC — below (empty — "
                    "searches 127.0.0.1:8080…8089 itself).",
                    "Мод Ficsit Remote Monitoring — через Satisfactory Mod Manager. У грі: /frm http start (або "
                    "автозапуск веб-сервера в налаштуваннях мода). Інший ПК — нижче (порожньо — сам шукає на 127.0.0.1:8080…8089).")},
                {"type": "input", "id": "addr", "placeholder": self.api.cfg.get("addr") or tr("auto: ", "сам: ") + self.base().split("//")[-1],
                 "button": tr("Save", "Зберегти")},
                {"type": "input", "id": "token", "label": tr("Token", "Токен"),
                 "placeholder": tr("set", "задано") if (self.api.cfg.get("token") or "").strip() else
                 tr("from the mod config", "з конфігу мода") if self.auto_token else "API Authentication Token",
                 "button": tr("Save", "Зберегти")}],
        }

    def tabs(self):
        tr, out = self.api.tr, []
        for k in ORDER:
            (ru, uk), icon, col, key, unit, use_si, _lines, _top = TABS[k]
            sub, val = "", self.fmt(key, unit, use_si)
            if k == "power":
                sub = tr("generation ", "вироблено ") + self.fmt("pp", "W")
            elif k == "battery":
                sub = (f'↓{self.fmt("bi", "W")}  ↑{self.fmt("bo", "W")}' if self.batcap > 0
                       else tr("no batteries", "батарей немає"))
                if self.batcap <= 0:
                    val = "—"
            elif k == "items":
                sub = tr("load ", "завантаження ") + self.fmt("eff", "%", False)
            elif k == "sink":
                sub = tr("coupons ", "купонів ") + str(self.sinkv["coupons"]) if self.sinkv else ""
            out.append({"type": "stat", "id": "tab:" + k, "on": self.tab == k, "icon": icon, "color": col,
                        "label": tr(ru, uk), "value": val, "sub": sub})
        return out

    def series(self, key, n):
        vals = self.hist.series(key, self.range)[-n:]
        return [None] * (n - len(vals)) + vals

    def page(self):
        tr = self.api.tr
        (ru, uk), icon, col, key, unit, use_si, lines, top = TABS[self.tab]
        rng = next(r for r in RANGES if r[0] == self.range)
        n, xs = rng[1], [tr(*rng[3][0]), tr(*rng[3][1]), tr("now", "зараз")]
        head = [{"type": "row", "items": [{"type": "button", "id": "range:" + r[0], "label": tr(*r[2]), "on": r[0] == self.range}
                                          for r in RANGES]}]
        chart = {"type": "chart", "label": f"{icon} {tr(ru, uk)}", "sub": self.session,
                 "unit": self.unit(unit), "si": use_si, "h": 170, "xs": xs,
                 "series": [{"label": tr(*lab), "color": c, "fill": fill, "values": self.series(k, n)} for k, lab, c, fill in lines]}
        if self.tab == "battery":
            chart["min"], chart["max"] = 0, 100
        body = head + [chart]
        if self.tab == "power":
            used = self.now.get("pc") or 0
            cap = self.now.get("cap") or 0
            body.append({"type": "bar", "label": tr("Grid load", "Навантаження мережі"), "value": used, "max": cap or 1,
                         "color": "#E5484D" if cap and used / cap > 0.9 else "#F2C94C", "size": 10,
                         "text": f"{used / cap * 100:.0f}%" if cap else "—"})
        if self.tab == "battery":
            body.append({"type": "grid", "cols": 3, "items": [
                {"type": "stat", "icon": "↓", "label": tr("Charging", "Заряджаються"), "value": self.fmt("bi", "W"), "color": "#3DDC6A"},
                {"type": "stat", "icon": "↑", "label": tr("Discharging", "Віддають"), "value": self.fmt("bo", "W"), "color": "#FA9549"},
                {"type": "stat", "icon": "🔋", "label": tr("Capacity", "Ємність"),
                 "value": si(self.batcap * 1e6, "Wh") if self.batcap > 0 else "—"}]})
        if self.tab == "items":
            body.append({"type": "bar", "label": tr("Factory load", "Завантаження заводів"), "value": self.now.get("eff") or 0,
                         "max": 100, "color": "#FA9549", "size": 10, "text": self.fmt("eff", "%", False)})
        if self.tab == "sink" and self.sinkv:
            s = self.sinkv
            body.append({"type": "bar", "label": tr("Next coupon", "До купона") + f' ({si(s["next"])})', "value": s["pct"],
                         "max": 100, "color": "#B57BFF", "size": 10, "text": f'{s["pct"]:.0f}%'})
            body.append({"type": "grid", "cols": 2, "items": [
                {"type": "stat", "icon": "🎟", "label": tr("Coupons", "Купонів"), "value": str(s["coupons"]), "color": "#B57BFF"},
                {"type": "stat", "icon": "◎", "label": tr("Total points", "Усього очок"), "value": si(s["total"])}]})
        if top:
            rows = [r for r in lst(self.top.get(top[0])) if isinstance(r, list) and len(r) >= 2 and isinstance(r[1], (int, float))]
            if rows:
                mx = max(r[1] for r in rows) or 1
                body.append({"type": "label", "label": tr(*top[1])})
                for r in rows:
                    u = ("W" if top[0] == "pw" else
                         tr("m³/min", "м³/хв") if len(r) > 2 and r[2] else self.unit("/min"))
                    body.append({"type": "bar", "label": r[0], "value": r[1], "max": mx, "color": col, "size": 8, "text": si(r[1], u)})
        return body
