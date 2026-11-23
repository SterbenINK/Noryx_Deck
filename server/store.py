"""Хранение конфигурации дека: режимы, раскладки кнопок, настройки."""
import i18n
import copy
import json
import logging
import secrets
import sys
import threading
from pathlib import Path

log = logging.getLogger("store")

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CONFIG = DATA / "config.json"

MODES = {      # ru — только чтобы старые сценарии («режим Обычный») находили режим
    "hobby": {"name": "Hobby", "uk": "Хобі", "ru": "Хобби", "icon": "chip"},
    "normal": {"name": "Normal", "uk": "Звичайний", "ru": "Обычный", "icon": "monitor"},
    "hotkeys": {"name": "Hotkeys", "uk": "Хоткеї", "ru": "Хоткеи", "icon": "keys"},
}
ORDER = ["hobby", "normal", "hotkeys"]
HK = "hotkeys"
OPTIONAL = {HK: "hk"}     # режим → переключатель в настройках, который его включает
# цвета для своих режимов: новому достаётся первый, которого ещё нет у других
# сетка плиток режима (окно режима): столбцов 4–8, строк 2–4; «Хоткеи» по умолчанию 8×4, остальные 4×2
COLS, ROWS = range(4, 9), range(2, 5)
GRID = {HK: (8, 4)}
GRID_DEFAULT = (4, 2)
USER_COLORS = ["#2EC4B6", "#F472B6", "#FF8A3D", "#5BD18B", "#FFB547", "#4DA3FF", "#A78BFA", "#F0544F"]


def app(label, match=None, auto=False, path=None, mono=None):
    return {"kind": "app", "label": label, "match": match or label, "auto": auto,
            "path": path, "proc": None, "mono": mono}


def macro(label, action, keys=None, mono=None):
    return {"kind": "macro", "label": label, "action": action, "keys": keys, "mono": mono}


def toggle(label, action, keys=None, mono=None):
    return {"kind": "toggle", "label": label, "action": action, "keys": keys, "mono": mono}


def hotkey(label, keys):
    return macro(label, "hotkey", keys)


HOTKEYS = [
    hotkey("Copy", "ctrl+c"), hotkey("Paste", "ctrl+v"), hotkey("Cut", "ctrl+x"),
    hotkey("Undo", "ctrl+z"), hotkey("Redo", "ctrl+shift+z"), hotkey("Save", "ctrl+s"),
    hotkey("Select all", "ctrl+a"), hotkey("Find", "ctrl+f"), hotkey("New tab", "ctrl+t"),
    hotkey("Close tab", "ctrl+w"), hotkey("Switch window", "alt+tab"), hotkey("Refresh", "f5"),
]


DEFAULT = {
    "version": 3,
    "cfg": {
        "bg": "graphite",
        "hobby": "#4DA3FF",
        "normal": "#A78BFA",
        "hotkeys": "#FFB547",
        "hk": False,         # третий режим «Хоткеи» — включается в настройках
        "hkView": "tiles",   # хоткеи: tiles (плитки) / keyboard (клавиатура)
        "hkKb": "keys",      # клавиатура: keys (правая половина) / numpad
        "auto": False,       # новый ПК: режимы сами программы не запускают…
        "close": False,      # …и не закрывают — включается в настройках
        "closeBusy": True,   # закрывая программы режима, не трогать занятые: пишет микрофон, идёт игра из Steam
        "mon": True,
        "spot": True,
        "player": "auto",     # плеер в доке: spotify / ytmusic / auto (что играет)
        "toasts": True,
        "lang": "en",        # язык интерфейса: en / uk / код пакета из каталога (i18n.NAMES)
        "clock": False,      # часы с датой в доке
        "autoMode": False,   # автопереключение режимов по правилам autoRules
        "autoRules": [],     # [{kind: app|out|time, value, label, mode, back}] — программа запустилась / выход звука / время
        "clipSync": False,   # общий буфер: текст, скопированный на ПК, сам появляется на телефоне (выключено — только вручную)
        "winLayouts": [],    # раскладки окон: [{id, name, wins: [{proc, label, path, x, y, w, h, max}]}]
        "viz": True,         # визуализатор звука в шапке (спектр того, что играет на ПК, цветами обложки)
        "vizColor": "auto",  # цвет визуализатора: auto (из обложки трека) / manual (vizHex)
        "vizHex": "#4DA3FF",
        "vizZones": [],      # цвета визуализатора по зонам (середина, между, края); пусто — все из vizHex
        "presets": {},       # готовые цвета, заменённые своими: {hobby|normal|hotkeys|user: [#RRGGBB, …]}
        "dockW": 236,        # ширина правой панели (дока), px на экране 1170×540
        "dockOrder": ["stats", "gap", "clock", "p:lamp", "media"],   # порядок блоков дока; gap — пустое место, p:<id> — плагин
        # (Arduino-лампа — плагин plugins/lamp: её настройки в pluginCfg.lamp; старые lamp / arduino / lampPort читает только он)
        "pluginsOn": True,   # плагины (папка plugins/) — все разом
        "plugins": {},       # включён ли плагин: {id: false}; нет записи — включён
        "pluginCfg": {},     # свои настройки плагинов: {id: {...}} — пишут сами плагины
        "mods": {},          # моды дека: {id: {on, hash}} — выбор «было / стало» (hash — каким мод был при выборе)
        "dockH": {},         # высота блоков дока, px содержимого: {stats|clock|media|p:<id>: px}; нет — своя
        "saver": True,       # режим часов, когда деком не пользуются
        "saverMin": 30,      # …через столько минут без касаний
        "screenOffMin": 60,  # гасить экран телефона через столько минут без касаний (0 — не гасить)
        "updAuto": False,    # ставить обновления с GitHub самому (выключено — только сообщит о новой версии)
        "colors": [],        # свои цвета режимов (колесо в окне режима): #RRGGBB, общие для всех режимов, до 12
        "lampPort": "",      # COM-порт, где лампа нашлась в прошлый раз
        "padMe": "default",  # саундпад: себе — устройство по умолчанию / имя / "off"
        "padMic": "off",     # саундпад: в эфир (виртуальный кабель для Discord) — имя / "off"
        "padVolMe": 80,
        "padVolMic": 80,
        "padNorm": True,     # выравнивать громкость звуков
        "padSolo": False,    # новый звук останавливает предыдущий
        "padVoice": "off",   # голос «в эфир»: микрофон гарнитуры, который дек подмешивает в кабель
        "padVoiceVol": 100,
    },
    "layouts": {
        "hobby": [
            app("Arduino IDE", "Arduino IDE", auto=True, mono="Ar"),
            app("HiView", "HiView", auto=True, mono="Hv"),
            app("VS Code", "Visual Studio Code", mono="VS"),
            app("Explorer", path="explorer.exe", mono="Ex"),
            app("Calculator", path="calc.exe", mono="="),
            macro("Screenshot", "screenshot", mono="Sc"),
            macro("Lock PC", "lock", mono="Lk"),
        ],
        "normal": [
            app("Discord", "Discord", auto=True, mono="Dc"),
            app("Steam", "Steam", auto=True, mono="St"),
            app("Telegram", "Telegram", auto=True, mono="Tg"),
            toggle("Microphone", "mic", mono="Mic"),
            app("Spotify", "Spotify", mono="Sp"),
            app("Prism Launcher", "Prism Launcher", mono="Mc"),
            toggle("FPS overlay", "hotkey", keys="ctrl+shift+o", mono="FPS"),
            macro("Screenshot", "screenshot", mono="Sc"),
            macro("Lock PC", "lock", mono="Lk"),
        ],
        "hotkeys": HOTKEYS,
    },
}


# Linux: те же режимы, но свои программы. «Файлы» — домашняя папка в файловом менеджере,
# «Оверлей FPS» — MangoHud (Shift справа + F12); в match через «|» — варианты названия программы.
LINUX_LAYOUTS = {
    "hobby": [
        app("Arduino IDE", "Arduino IDE", auto=True, mono="Ar"),
        app("VS Code", "Visual Studio Code|VSCodium|Code - OSS", mono="VS"),
        app("Files", path="~", mono="Fi"),
        app("Calculator", "KCalc|Qalculate|Calculator|Калькулятор", mono="="),
        macro("Screenshot", "screenshot", mono="Sc"),
        macro("Lock PC", "lock", mono="Lk"),
    ],
    "normal": [
        app("Discord", "Discord|Vesktop|WebCord", auto=True, mono="Dc"),
        app("Steam", "Steam", auto=True, mono="St"),
        app("Telegram", "Telegram", auto=True, mono="Tg"),
        toggle("Microphone", "mic", mono="Mic"),
        app("Spotify", "Spotify", mono="Sp"),
        app("Prism Launcher", "Prism Launcher", mono="Mc"),
        toggle("FPS overlay", "hotkey", keys="rshift+f12", mono="FPS"),
        macro("Screenshot", "screenshot", mono="Sc"),
        macro("Lock PC", "lock", mono="Lk"),
    ],
    "hotkeys": HOTKEYS,
}
if sys.platform != "win32":
    DEFAULT["layouts"] = LINUX_LAYOUTS


class Store:
    def __init__(self):
        self._lock = threading.Lock()
        self.plugin_modes = {}             # «p:<id>» → {plugin, ru, uk, subRu, subUk, icon, color}
        DATA.mkdir(parents=True, exist_ok=True)
        self.fresh = not CONFIG.exists()   # первый запуск: стандартные кнопки ещё не проверены на этом ПК
        if CONFIG.exists():
            try:
                self.data = json.loads(CONFIG.read_text(encoding="utf-8"))
                log.info("Конфиг загружен: %s", CONFIG)
            except Exception:
                log.exception("Конфиг повреждён, беру стандартный (старый сохранён как .bak)")
                CONFIG.replace(CONFIG.with_suffix(".bak"))
                self.data = copy.deepcopy(DEFAULT)
        else:
            self.data = copy.deepcopy(DEFAULT)
            log.info("Создаю стандартный конфиг")
        if self.data.get("version", 1) < 2:
            # версия 2: «обновляться самому» теперь выключено по умолчанию — в старых конфигах было включено само
            self.data.setdefault("cfg", {})["updAuto"] = False
            self.data["version"] = 2
        if self.data["version"] < 3:
            # версия 3: «Лампа» в настройках — только если Arduino нашлась; у кого лампа уже работала — нашлась
            c = self.data.setdefault("cfg", {})
            c["arduino"] = bool(c.get("lamp") and c.get("lampPort"))
            self.data["version"] = 3
        # недостающие ключи из стандартного
        for k, v in DEFAULT["cfg"].items():
            self.data.setdefault("cfg", {}).setdefault(k, copy.deepcopy(v))     # копия: списки не делим со стандартом
        if self.data["cfg"].get("lang") == "ru":      # русского больше нет — кто был на нём, переходит на украинский
            self.data["cfg"]["lang"] = "uk"
        for m in ORDER:
            self.data.setdefault("layouts", {}).setdefault(m, copy.deepcopy(DEFAULT["layouts"][m]))
        # свои названия и значки режимов: {id: {name, icon}}; свои режимы — там же с "user": True
        self.data.setdefault("modes", {})
        for m in self.user_modes():
            self.data["layouts"].setdefault(m, [])
        self.save()

    @property
    def cfg(self):
        return self.data["cfg"]

    @property
    def layouts(self):
        return self.data["layouts"]

    def save(self):
        with self._lock:
            tmp = CONFIG.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(CONFIG)

    # ---------- режимы: встроенные (MODES) и свои ----------
    def user_modes(self):
        return [m for m, v in self.data.get("modes", {}).items() if isinstance(v, dict) and v.get("user")]

    def is_user(self, m):
        return isinstance(m, str) and bool(self.data["modes"].get(m, {}).get("user"))

    # режимы от плагинов: «p:<id>», пока плагин работает и объявил режим (plugins.Host.sync_modes);
    # вместо плиток — свой экран плагина, программ при входе не закрывают (как «Хоткеи»)
    plugin_modes = {}

    def is_plugin(self, m):
        return isinstance(m, str) and m in self.plugin_modes

    def overlay(self, m):
        """Режим «поверх»: заходя в него, программы прошлого режима не закрываем (Хоткеи, режимы плагинов)."""
        return m == HK or self.is_plugin(m)

    def exists(self, m):
        return m in MODES or self.is_user(m) or self.is_plugin(m)

    def mode_ids(self):
        """Все режимы по порядку (свой порядок из «Правки» на главном), включая выключенные."""
        order = [m for m in self.data.get("order", []) if self.exists(m)]
        for m in ORDER + self.user_modes() + list(self.plugin_modes):
            if m not in order:
                order.append(m)
        return order

    def mode_on(self, m):
        if m in MODES:
            flag = OPTIONAL.get(m)
            return not flag or bool(self.cfg.get(flag))
        return self.is_user(m) or self.is_plugin(m)

    def mode_name(self, m, uk=False):
        name = self.data["modes"].get(m, {}).get("name")
        if name:
            return name
        if self.is_plugin(m):
            pm = self.plugin_modes[m]
            return i18n.tr(pm.get("en") or pm.get("ru") or m, pm.get("uk") or pm.get("en") or m)
        return i18n.tr(MODES[m]["name"], MODES[m]["uk"]) if m in MODES else str(m)

    def set_mode_meta(self, m, name, icon):
        meta = self.data["modes"].setdefault(m, {})
        if name or not meta.get("user"):          # у своего режима название не стирается
            meta["name"] = name
        if icon:
            meta["icon"] = icon
        self.save()

    def grid(self, m):
        """(столбцы, строки) сетки плиток режима."""
        meta = self.data["modes"].get(m) or {}
        dc, dr = GRID.get(m, GRID_DEFAULT)
        c, r = meta.get("cols"), meta.get("rows")
        return (c if c in COLS else dc), (r if r in ROWS else dr)

    def set_grid(self, m, cols, rows):
        cols, rows = (int(x) if isinstance(x, (int, float, str)) and str(x).isdigit() else 0 for x in (cols, rows))
        c, r = self.grid(m)
        meta = self.data["modes"].setdefault(m, {})
        meta["cols"], meta["rows"] = (cols if cols in COLS else c), (rows if rows in ROWS else r)
        self.save()
        return meta["cols"], meta["rows"]

    def add_mode(self, name, icon, color=None):
        """Свой режим: пустая раскладка, свободный цвет, в конец списка. Возвращает id."""
        mid = "u" + secrets.token_hex(3)
        while self.exists(mid) or mid in self.cfg:
            mid = "u" + secrets.token_hex(3)
        self.data["modes"][mid] = {"name": name, "icon": icon or "star", "user": True}
        self.data["layouts"][mid] = []
        used = {self.cfg.get(m) for m in self.mode_ids()}
        self.cfg[mid] = color or next((c for c in USER_COLORS if c not in used), USER_COLORS[0])
        self.data["order"] = self.mode_ids()
        self.save()
        return mid

    def del_mode(self, m):
        """Удалить свой режим (встроенные не удаляются). True — удалён."""
        if not self.is_user(m):
            return False
        self.data["modes"].pop(m, None)
        self.data["layouts"].pop(m, None)
        self.cfg.pop(m, None)
        self.data["order"] = [x for x in self.data.get("order", []) if x != m]
        self.save()
        return True

    def set_order(self, ids):
        seen, order = set(), []
        for m in list(ids or []) + self.mode_ids():
            if isinstance(m, str) and self.exists(m) and m not in seen:
                seen.add(m)
                order.append(m)
        self.data["order"] = order
        self.save()

    def public(self):
        modes = []
        for m in self.mode_ids():
            if not self.mode_on(m):
                continue
            meta = self.data["modes"].get(m, {})
            cols, rows = self.grid(m)
            if m in MODES:
                modes.append({"id": m, "custom": meta.get("name") or "", "icon": meta.get("icon") or MODES[m]["icon"],
                              "def": [MODES[m]["name"], MODES[m]["uk"]], "cols": cols, "rows": rows})
            elif self.is_plugin(m):
                pm = self.plugin_modes[m]
                modes.append({"id": m, "custom": meta.get("name") or "", "icon": meta.get("icon") or pm["icon"],
                              "def": [pm.get("en") or pm.get("ru", ""), pm["uk"]], "plugin": pm["plugin"],
                              "sub": [pm.get("subEn") or pm.get("subRu", ""), pm["subUk"]],
                              **({"color": pm["color"]} if pm.get("color") else {})})
            else:
                name = meta.get("name") or "?"
                modes.append({"id": m, "custom": name, "icon": meta.get("icon") or "star", "def": [name, name],
                              "user": True, "cols": cols, "rows": rows})
        # настройки плагинов (ключи, токены) телефонам не шлём — их видит только ПК
        cfg = {k: v for k, v in self.cfg.items() if k not in ("pluginCfg",)}
        return {"modes": modes, "cfg": cfg, "layouts": self.layouts}
