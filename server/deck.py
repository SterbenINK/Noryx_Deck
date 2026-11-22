"""Noryx — сервер на ПК.

Раздаёт веб-клиент дека и держит WebSocket с телефоном.
Запуск: start.bat (или Start.bat в переносной версии) — в трее, без окна;
start_console.bat — с окном логов (для отладки). Linux: ./start.sh (модули lx_*.py).
"""
import logging
import logging.handlers
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).parent))
import i18n  # noqa: E402  (только стандартная библиотека)

i18n.set_lang(i18n.config_lang(ROOT))     # лог — на языке дека с первой строки; после загрузки конфига — из него
(ROOT / "logs").mkdir(exist_ok=True)
_fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")
_file = logging.handlers.RotatingFileHandler(ROOT / "logs" / "server.log", maxBytes=2_000_000,
                                             backupCount=2, encoding="utf-8")
_file.setFormatter(_fmt)
_file.addFilter(i18n.LogFilter())
_handlers = [_file]
if sys.stdout is not None:  # под pythonw.exe (режим трея) консоли нет
    _con = logging.StreamHandler(sys.stdout)
    _con.setFormatter(_fmt)
    _con.addFilter(i18n.LogFilter())
    _handlers.append(_con)
logging.basicConfig(level=logging.INFO, handlers=_handlers)
log = logging.getLogger("deck")


def _no_quickedit():
    """Отключает режим выделения в консоли: иначе клик по окну замораживает сервер."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.GetStdHandle(-10)
        mode = ctypes.c_uint32()
        if k.GetConsoleMode(h, ctypes.byref(mode)):
            k.SetConsoleMode(h, (mode.value & ~0x0040) | 0x0080)
    except Exception:
        pass


_no_quickedit()
sys.excepthook = lambda t, v, tb: log.critical("Необработанная ошибка", exc_info=(t, v, tb))
import faulthandler
import gc
gc.disable()  # мусор собирает comthread.gc_loop() в COM-потоке
_crash = open(ROOT / "logs" / "crash.log", "a", encoding="utf-8")
faulthandler.enable(_crash, all_threads=True)

try:
    import asyncio
    import hashlib
    import json
    import os
    import re
    import functools
    import shutil
    import socket
    import time

    from aiohttp import WSMsgType, web

    IS_WIN = sys.platform == "win32"
    if IS_WIN:
        import apps
        import audio
        import clip
        import devices
        import keys
        import sensors
        from media import Media
    else:                                  # Linux: свои модули с тем же интерфейсом (lx_*.py)
        import lx_apps as apps
        import lx_audio as audio
        import lx_clip as clip
        import lx_devices as devices
        import lx_keys as keys
        import lx_sensors as sensors
        from lx_media import Media
    import comthread
    import pair as pairmod
    import roles as rolesmod
    import catalog as catmod
    import deckmods as modsmod
    import tiles as tilesmod
    import busy
    import changelog as changelogmod
    import pad as padmod
    import script as scriptmod
    import update as updmod
    import viz as vizmod
    import wol as wolmod
    from diag import Diag
    import health as healthmod
    from events import Events
    import plugins as plugmod
    from store import HK, Store
except Exception:
    log.critical("Не удалось загрузить модули. Переустанови библиотеки: install.bat, а в переносной версии — "
                 "удали папку python (на Linux — .venv) и запусти Start.bat / start.sh.", exc_info=True)
    raise

PORT = 8765
DISCOVERY_PORT = 8766          # UDP: приложение на телефоне ищет ПК широковещательным запросом
CLIENT = ROOT / "client"
# APK для кнопки «Скачать APK»: рядом с сервером или в папке исходников приложения
APK = next((p for p in (ROOT / "Noryx.apk", ROOT / "android" / "Noryx.apk", ROOT / "ClaudeDeck.apk") if p.exists()),
           ROOT / "Noryx.apk")
# «версия» интерфейса: если после перезапуска сервера она другая — экраны дека перезагрузятся сами
CLIENT_BUILD = str(int((CLIENT / "index.html").stat().st_mtime))
_apk_sha = (None, "")
VERSION = "0.1"
HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")
SCEN_GRACE = 120                 # с: программу, запущенную сценарием, правила автопереключения не трогают (игра грузится)
DOCK_BLOCKS = ("stats", "gap", "clock", "media")      # блоки правой панели, порядок — cfg.dockOrder; ещё — блоки плагинов
DOCK_PLUGIN = re.compile(r"p:[a-z0-9][a-z0-9_-]{0,39}")    # «p:<id плагина>»
PLUGINS = ROOT / "plugins"
MODS = ROOT / "mods"                # моды — изменения самого дека (вид, функции страницы), не плагины


def dock_id(x):
    """Блок дока из настроек телефона: встроенный или блок плагина; «lamp» (до плагинов) — теперь плагин лампы."""
    x = "p:lamp" if x == "lamp" else x
    return x if isinstance(x, str) and (x in DOCK_BLOCKS or DOCK_PLUGIN.fullmatch(x)) else None
NUM_CFG = {"dockW": (190, 380), "saverMin": (1, 240), "screenOffMin": (0, 600)}   # числа из настроек: пределы
CLIP_MAX = 5000                # общий буфер: длиннее — не берём


def looks_secret(text):
    """Похоже на пароль или ключ: одно «слово» 8–64 символа из трёх и больше видов знаков — в общий буфер не берём."""
    t = text.strip()
    if not re.fullmatch(r"\S{8,64}", t) or re.match(r"https?://", t):
        return False
    kinds = sum(bool(re.search(p, t)) for p in (r"[a-zа-яё]", r"[A-ZА-ЯЁ]", r"\d", r"[^\w\s]"))
    return kinds >= 3
RAISE_WAIT = 15.0              # сколько ждать окно только что запущенной программы, чтобы вывести его наверх


def version_text():
    """Для лога: коммит и дата из server/version.json (кладут сборка и обновление)."""
    v = updmod.local_version(ROOT)
    return f"{v['sha'][:7]} {i18n.tr('от', 'від')} {str(v.get('date', ''))[:10]}" if v else VERSION


def app_entries(items):
    """Все запускаемые программы раскладки: обычные кнопки + участники групп."""
    out = []
    for it in items:
        if it.get("kind") == "app":
            out.append(it)
        elif it.get("kind") == "group":
            out.extend(x for x in it.get("items", []) if x.get("path"))
    return out


def app_item(src):
    return {"kind": "app", "label": src["label"], "match": src["label"], "auto": False,
            "path": src["path"], "proc": src.get("proc"), "target": src.get("target"), "mono": None}


class Deck:
    def __init__(self):
        self.store = Store()
        i18n.set_lang(self.store.cfg.get("lang"))
        self.clients = set()
        self.mode = None
        self.base = None            # последний режим, кроме «Хоткеев» (они программы не закрывают)
        self.toggles = {}           # состояние хоткей-переключателей (реальное состояние неизвестно)
        self.last = {}              # последние отправленные пакеты по типам
        self.loop = None
        self.media = None
        self.plugins = plugmod.Host(self, PLUGINS)
        self.mods = modsmod.Mods(self, MODS)
        self.catalog = catmod.Center(self, ROOT)   # каталог плагинов: поставить / обновить / откатить моды из main
        self.pad = None
        self.tray = None
        self.scripts = {}           # кнопки-скрипты, которые сейчас идут: id → задача asyncio
        self.steps = {}             # сценарии: id → «шаг/всего» (показывается на плитке, пока идёт)
        self.scen_procs = {}        # что запустил сценарий: процесс → до какого времени правила его не трогают
        self.auto_state = {}        # автопереключение: что было запущено, какой выход, какая минута — для «перемен»
        self.events = Events(None)  # история активности и центр уведомлений
        self.diag = None            # окно «Диагностика»: замеры (psutil), подписчики, задача рассылки
        self.diag_subs = set()
        self.diag_task = None
        self.seen = {}              # для истории: какие программы с плиток запущены, какие выходы звука, лампа, версия
        self.vol_timers = {}
        self.clip_items = []        # общий буфер: последние 20 текстов {id, ts, src: pc|phone, text}
        self.clip_seq = None
        self.ips = {}               # соединение телефона → его адрес (окно «Устройства»)
        self.pair = pairmod.Pairing(self.store.fresh)   # свои устройства: код с ПК → постоянный ключ
        self.who = {}               # соединение → id своего устройства ("local" — сам ПК)
        self.errs = {}              # соединение → сколько ошибок страницы уже записали в лог
        self.health = healthmod.Health()   # вкладка «Дек» в диагностике: время сообщений, потоки, ошибки
        self.cinfo = {}             # соединение → {с какого времени, что за устройство} (вкладка «Дек»)
        self.raising = {}           # программы, чьё окно ждём, чтобы вывести наверх: proc → задача asyncio
        self.viz = vizmod.Viz(audio.Loopback, self.viz_send)   # визуализатор в шапке: подписчики — телефоны
        self.out_dev = None         # выход по умолчанию: сменился — визуализатор переподключает захват
        self.touched = time.monotonic()   # когда с телефона последний раз что-то нажимали
        self.stop_event = None      # serve(): остановить сервер…
        self.stop_action = None     # …и что потом: "update" — помощник обновления уже ждёт
        self.updater = updmod.Controller(self)

    def idle(self, quiet_for):
        """Деком сейчас не пользуются: давно не нажимали, не идут скрипты, не играют звуки, голос не в эфире."""
        if time.monotonic() - self.touched < quiet_for:
            return False
        if any(not t.done() for t in self.scripts.values()):
            return False
        if (self.last.get("padState") or {}).get("playing"):
            return False
        c = self.store.cfg
        return c.get("padVoice", "off") == "off" or c.get("padMic", "off") == "off"

    def stop_for(self, action):
        self.stop_action = action
        if self.stop_event:
            self.stop_event.set()

    def clean_cfg(self, cfg):
        """Настройки с телефона: только известные ключи. Цвета (режимов и свои) — только #RRGGBB:
        страница вставляет их прямо в стили."""
        out = {}
        for k, v in (cfg if isinstance(cfg, dict) else {}).items():
            if k not in self.store.cfg or k in ("arduino", "plugins", "pluginsOn", "pluginCfg", "pluginPerms",
                                                "pluginPermsSince", "mods"):   # решает только ПК
                continue
            if k == "presets":
                v = {key: [c.upper() for c in lst if isinstance(c, str) and HEX_COLOR.fullmatch(c)][:8]
                     for key, lst in (v.items() if isinstance(v, dict) else [])
                     if (key == "user" or self.store.exists(key)) and isinstance(lst, list)}
            elif k == "vizHex" and not (isinstance(v, str) and HEX_COLOR.fullmatch(v)):
                continue
            elif k == "vizColor" and v not in ("auto", "manual"):
                continue
            elif k == "vizZones":
                v = [c.upper() for c in (v if isinstance(v, list) else []) if isinstance(c, str) and HEX_COLOR.fullmatch(c)]
                v = v if len(v) == 3 else []
            elif k == "dockOrder":
                v = list(dict.fromkeys(filter(None, (dock_id(x) for x in (v if isinstance(v, list) else [])))))
                v += [x for x in DOCK_BLOCKS if x not in v]
            elif k == "dockH":
                v = {dock_id(x): int(min(500, h)) for x, h in (v.items() if isinstance(v, dict) else [])
                     if dock_id(x) and x != "gap" and isinstance(h, (int, float)) and not isinstance(h, bool) and h >= 1}
            elif k in NUM_CFG:
                lo, hi = NUM_CFG[k]
                if isinstance(v, bool) or not isinstance(v, (int, float)):
                    continue
                v = int(max(lo, min(hi, v)))
            elif k == "autoRules":
                v = self.clean_rules(v)
            elif k == "winLayouts":
                v = self.clean_layouts(v)
            elif k == "colors":
                v = list(dict.fromkeys(c.upper() for c in (v if isinstance(v, list) else [])
                                       if isinstance(c, str) and HEX_COLOR.fullmatch(c)))[-12:]
            elif self.store.exists(k) and not (isinstance(v, str) and HEX_COLOR.fullmatch(v)):
                continue
            out[k] = v
        return out

    def clean_layouts(self, lays):
        out = []
        for L in lays if isinstance(lays, list) else []:
            if not isinstance(L, dict) or not isinstance(L.get("wins"), list):
                continue
            wins = []
            for w in L["wins"][:12]:
                try:
                    wins.append({"proc": str(w["proc"])[:120], "label": str(w.get("label") or w["proc"])[:60],
                                 "path": str(w.get("path") or "")[:500], "x": int(w["x"]), "y": int(w["y"]),
                                 "w": max(100, int(w["w"])), "h": max(80, int(w["h"])), "max": bool(w.get("max"))})
                except (KeyError, TypeError, ValueError):
                    continue
            out.append({"id": str(L.get("id") or "")[:20], "name": str(L.get("name") or "")[:40], "wins": wins})
        return out[:20]

    def clean_rules(self, rules):
        """Правила автопереключения с телефона: известные виды, режим существует, не больше 30."""
        out = []
        for r in rules if isinstance(rules, list) else []:
            if not isinstance(r, dict) or r.get("kind") not in ("app", "out", "time") or not self.store.exists(r.get("mode")):
                continue
            value = str(r.get("value") or "")[:200]
            if not value or (r["kind"] == "time" and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", value)):
                continue
            out.append({"kind": r["kind"], "value": value, "label": str(r.get("label") or value)[:80],
                        "mode": r["mode"], "back": bool(r.get("back")) and r["kind"] == "app"})
        return out[:30]

    # ---------- автопереключение режимов ----------
    async def auto_modes(self, running):
        """Раз в пару секунд: сработало правило — режим. Срабатывает только на перемену (программа запустилась,
        выход сменился, наступило время), поэтому ручной выбор не перебивает: переключил сам — так и остаётся.
        «Вернуть прежний» — программа закрылась, а режим всё ещё тот, что включило правило."""
        cfg = self.store.cfg
        rules = cfg.get("autoRules") or []
        st = self.auto_state
        out = next((d.get("id") for d in (self.last.get("audio") or {}).get("devices") or [] if d.get("default")), None)
        now = time.strftime("%H:%M")
        first = st.get("running") is None
        prev_run, prev_out, prev_min = st.get("running") or set(), st.get("out"), st.get("min")
        st["running"], st["out"], st["min"] = set(running), out, now
        if first or not cfg.get("autoMode"):
            return
        if self.steps:          # идёт сценарий: режимы и программы ведёт он — правила молчат
            return
        t = time.monotonic()
        self.scen_procs = {p: d for p, d in self.scen_procs.items() if d > t}
        for r in rules:
            if not self.store.mode_on(r["mode"]):
                continue
            if r["kind"] == "app" and r["value"] in self.scen_procs:
                continue        # программу только что запустил сценарий — он уже сделал, что нужно
            fired = (r["kind"] == "app" and r["value"] in running and r["value"] not in prev_run) \
                or (r["kind"] == "out" and out == r["value"] and prev_out != out) \
                or (r["kind"] == "time" and now == r["value"] and prev_min != now)
            if fired and self.mode != r["mode"]:
                if r.get("back"):
                    st.setdefault("back", {})[r["value"]] = (self.mode, r["mode"])
                await self.toast(self.tr("Автопереключение: ", "Автоперемикання: ") + r.get("label", r["value"]))
                log.info("Автопереключение: %s → %s", r.get("label"), r["mode"])
                await self.switch_mode(r["mode"], keep={r["value"]} if r["kind"] == "app" else ())
                return
        for proc, (before, auto) in list((st.get("back") or {}).items()):
            if proc in prev_run and proc not in running:
                del st["back"][proc]
                if self.mode == auto and before and self.store.mode_on(before):
                    await self.toast(self.tr("Автопереключение: обратно", "Автоперемикання: назад"))
                    await self.switch_mode(before)

    # ---------- контекстные действия плитки (программа запущена) ----------
    async def ctx_action(self, mode, index, label, script, window=True):
        """Действие на плитке запущенной программы (свои или стандартные для Discord, Arduino IDE, VS Code…):
        показать её окно и выполнить строку-скрипт — горячие клавиши программ работают, когда её окно активно."""
        try:
            it = self.store.layouts[mode][index]
        except (KeyError, IndexError, TypeError):
            return
        loop = asyncio.get_running_loop()
        if window and it.get("proc") and it["proc"] in apps.running_procs():
            await loop.run_in_executor(None, apps.show, it)
            await asyncio.sleep(0.15)
        await self.toggle_script(f"ctx:{mode}:{index}", label or it.get("label"), {"script": script}, times=1)

    def tr(self, ru, uk):
        """Текст для телефона на языке из настроек."""
        return uk if self.store.cfg.get("lang") == "uk" else ru

    # ---------- отправка ----------
    def role_of(self, ws):
        """Роль подключения: сам ПК и устройства без роли — основной дек."""
        return self.pair.role(self.who.get(ws))

    async def send(self, ws, msg):
        if self.who.get(ws) not in (None, "local") and not rolesmod.visible(self.role_of(ws), msg):
            return                                   # не для этой роли: буфер ПК, устройства, коды, отчёты
        try:
            await ws.send_str(json.dumps(msg, ensure_ascii=False))
        except Exception:
            self.clients.discard(ws)

    async def broadcast(self, msg, remember=True):
        if remember:
            self.last[msg["t"]] = msg
        for ws in list(self.clients):
            await self.send(ws, msg)
        if msg.get("t") == "upd" and msg.get("state") == "ahead" and msg.get("remote"):
            sha = msg["remote"].get("sha")
            if sha and self.seen.get("upd") != sha:          # новая версия на GitHub — уведомление, один раз
                self.seen["upd"] = sha
                text = msg["remote"].get("msg") or sha[:7]
                await self.event("update", self.tr("Доступно обновление: ", "Доступне оновлення: ") + text,
                                 level="update", notify=True, link="settings")

    async def event(self, kind, text, level="info", notify=False, redo=None, link=None):
        """В историю (и, если notify, в центр уведомлений) — и сразу телефонам."""
        ev = self.events.add(kind, text, level, notify, redo, link)
        msg = {"t": "event", "ev": ev, "unread": self.events.unread()}
        for ws in list(self.clients):
            await self.send(ws, msg)

    async def viz_send(self, frame):
        """Кадр визуализатора — только тем телефонам, что его просили (одна строка на всех)."""
        text = '{"t":"viz","b":"' + frame + '"}'
        for ws in list(self.viz.subs):
            try:
                await ws.send_str(text)
            except Exception:
                self.viz.subs.discard(ws)

    async def toast(self, text):
        if self.store.cfg.get("toasts", True):
            await self.broadcast({"t": "toast", "text": text}, remember=False)

    def state_msg(self):
        return {"t": "state", "mode": self.mode, "toggles": self.toggles,
                "scripts": sorted(k for k, task in self.scripts.items() if not task.done()), "steps": self.steps}

    def config_msg(self):
        return dict(self.store.public(), t="config", apk=APK.exists(), apkSha=apk_sha(), build=CLIENT_BUILD,
                    os="windows" if IS_WIN else "linux")

    def pc_msg(self, request):
        """Чем будить этот ПК (Wake-on-LAN): MAC карты, через которую подключился телефон, и подсеть. Приложение
        запоминает их вместе со своей сетью и, если ПК уснёт, шлёт «магические пакеты»."""
        try:
            sock = request.transport.get_extra_info("sockname") if request.transport else None
            ip = sock[0] if sock else ""
            return {"t": "pc", "macs": wolmod.macs_for(ip), "net": wolmod.subnet(ip), "name": socket.gethostname()}
        except Exception as e:
            log.warning("Wake-on-LAN: не определить MAC сетевой карты: %s", e)
            return {"t": "pc", "macs": [], "net": "", "name": ""}

    # ---------- режимы ----------
    async def switch_mode(self, new, keep=()):
        if not self.store.mode_on(new):
            return
        # «Хоткеи» — режим поверх остальных: заходя в него, ничего не закрываем, а выходя —
        # закрываем программы режима, который был до него
        old = self.base if self.store.overlay(self.mode) else self.mode
        cfg = self.store.cfg
        lay = self.store.layouts
        parts = [f"{self.tr('Режим', 'Режим')} «{self.store.mode_name(new, self.tr(False, True))}»"]
        running = apps.running_procs()
        if cfg.get("close") and old and old != new and not self.store.overlay(new):
            keep = {e.get("proc") for e in app_entries(lay.get(new, []))} | set(keep)   # + программа, включившая режим
            todo, seen, kept = [], set(), []
            for it in app_entries(lay.get(old, [])):
                p = it.get("proc")
                if not p or p in seen or p in keep or p in apps.PROTECTED or p not in running:
                    continue
                seen.add(p)
                if it.get("keep"):                  # «Не закрывать при смене режима» на плитке
                    kept.append(it["label"])
                    continue
                todo.append(it)
            why = {}
            if todo and cfg.get("closeBusy"):       # занята делом — не обрываем: микрофон, игра из Steam
                why = await asyncio.get_running_loop().run_in_executor(None, busy.reasons, {i["proc"] for i in todo})
            closed = []
            for it in todo:
                w = why.get(it["proc"])
                if w:
                    kept.append(it["label"] + " — " + (self.tr("микрофон", "мікрофон") if w == "mic" else self.tr("игра", "гра")))
                    log.info("Не закрываю %s: %s", it["label"], w)
                    continue
                closed.append(it["label"])
                asyncio.create_task(apps.close(it["proc"], it.get("target")))
            if closed:
                parts.append(self.tr("закрываю: ", "закриваю: ") + ", ".join(closed))
            if kept:
                parts.append(self.tr("не закрываю: ", "не закриваю: ") + ", ".join(kept))
        if cfg.get("auto"):
            started = []
            todo = []
            for it in lay.get(new, []):
                if not it.get("auto"):
                    continue
                if it.get("kind") == "app":
                    todo.append(it)
                elif it.get("kind") == "group":
                    todo.extend(it.get("items", []))
            seen = set()
            for it in todo:
                key = it.get("proc") or it.get("path")
                if not it.get("path") or key in seen or (it.get("proc") and it["proc"] in running):
                    continue
                seen.add(key)
                try:
                    await asyncio.get_running_loop().run_in_executor(None, apps.launch, it)
                    started.append(it["label"])
                except Exception as e:
                    log.warning("Автозапуск %s: %s", it["label"], e)
            if started:
                parts.append(self.tr("запускаю: ", "запускаю: ") + ", ".join(started))
        self.mode = new
        if not self.store.overlay(new):
            self.base = new
        await self.broadcast(self.state_msg())
        await self.toast(" · ".join(parts))
        await self.event("mode", self.tr("Режим «", "Режим «") + self.store.mode_name(new, self.tr(False, True)) + "»",
                         redo={"t": "mode", "mode": new})

    # ---------- программа: показать, если уже запущена, иначе запустить ----------
    async def open_app(self, it, running, toggle=False):
        """Плитка программы. Запущена — развернуть её окно из свёрнутого и вывести наверх. Окна нет (свёрнута
        в трей) — запустить ещё раз: программа сама покажет окно. Не запущена — запустить. После запуска окно
        выводится наверх, как появится: сама программа туда может не попасть — Windows и KWin не дают
        фоновым процессам перехватывать фокус.
        toggle (плитка программы): окно и так наверху — свернуть его.
        Возвращает "shown" (была запущена), "hidden" (свернул), "started" (запустил) или "starting"
        (уже запускается — второй раз не запускаю, жду окно дальше)."""
        loop = asyncio.get_running_loop()
        proc = it.get("proc")
        on = bool(proc) and proc in running
        res = await loop.run_in_executor(None, apps.show, it, toggle) if on else False
        if res:
            return "hidden" if res == "hidden" else "shown"
        if on and proc in self.raising:
            self.bring_up(it)
            return "starting"
        if on:
            log.info("%s: окна нет (свёрнута в трей?) — запускаю ещё раз, она покажет его сама", it["label"])
        await loop.run_in_executor(None, apps.launch, it)
        if proc:
            self.bring_up(it)
        return "shown" if on else "started"

    def bring_up(self, it, seconds=None):
        """Ждать окно только что запущенной программы и вывести его наверх (раз, как появится)."""
        seconds = seconds or RAISE_WAIT
        key = it.get("proc")
        old = self.raising.pop(key, None)
        if old:
            old.cancel()

        async def wait():
            loop = asyncio.get_running_loop()
            end = time.monotonic() + seconds
            try:
                while time.monotonic() < end:
                    await asyncio.sleep(0.5)
                    if await loop.run_in_executor(None, apps.show, it):
                        return
            except Exception as e:
                log.warning("Окно %s: %s", it.get("label"), e)
            finally:
                if self.raising.get(key) is task:
                    del self.raising[key]
        task = asyncio.ensure_future(wait())
        self.raising[key] = task

    # ---------- нажатие кнопки ----------
    async def press(self, mode, index):
        try:
            it = self.store.layouts[mode][index]
        except (KeyError, IndexError):
            return
        kind, label = it.get("kind"), it.get("label")
        try:
            if kind == "app":
                if not it.get("path"):
                    await self.toast(self.tr(f"«{label}»: программа не найдена. Удали кнопку и добавь через +",
                                            f"«{label}»: програму не знайдено. Видали кнопку й додай через +"))
                    return
                what = await self.open_app(it, apps.running_procs(), toggle=True)
                await self.toast({"shown": self.tr("Показываю: ", "Показую: "),
                                  "hidden": self.tr("Сворачиваю: ", "Згортаю: "),
                                  "starting": self.tr("Уже запускается: ", "Вже запускається: ")}.get(
                    what, self.tr("Запуск: ", "Запуск: ")) + label)
            elif kind == "group":
                members = [x for x in it.get("items", []) if x.get("path")]
                if not members:
                    await self.toast(self.tr(f"Группа «{label}» пустая — добавь программы: Правка → «Состав»",
                                            f"Група «{label}» порожня — додай програми: Редагувати → «Склад»"))
                    return
                running = apps.running_procs()
                started, shown = [], []
                for x in members:
                    try:
                        what = await self.open_app(x, running)
                        (shown if what == "shown" else started).append(x["label"])
                        if what == "started":
                            await asyncio.sleep(0.3)
                    except Exception as e:
                        log.warning("Группа %s: %s — %s", label, x["label"], e)
                parts = ([self.tr("запускаю ", "запускаю ") + ", ".join(started)] if started else []) + (
                    [self.tr("показываю ", "показую ") + ", ".join(shown)] if shown else [])
                await self.toast(self.tr(f"Группа «{label}»: ", f"Група «{label}»: ")
                                 + (" · ".join(parts) or self.tr("не запустилась", "не запустилася")))
            elif kind == "plugin":
                await self.plugins.action(str(it.get("plugin")), str(it.get("action")))
            elif kind == "macro":
                act = it.get("action")
                if act == "lock":
                    keys.lock_pc()
                elif act == "screenshot":
                    keys.screenshot()
                elif act == "winlayout":
                    await self.restore_layout(it.get("id"), label)
                    return
                elif act == "hotkey" and it.get("keys"):
                    keys.hotkey(it["keys"])
                elif act == "script":
                    await self.toggle_script(it.get("id") or f"{mode}:{index}", label, it)
                    return
                await self.toast(self.tr("Макрос: ", "Макрос: ") + label)
            elif kind == "toggle":
                act = it.get("action")
                if act == "mic":
                    muted = await comthread.run(audio.toggle_mic)
                    await self.toast(self.tr("Микрофон выключен", "Мікрофон вимкнено") if muted
                                     else self.tr("Микрофон включён", "Мікрофон увімкнено"))
                    await self.poll_audio()
                else:
                    if it.get("keys"):
                        keys.hotkey(it["keys"])
                    self.toggles[label] = not self.toggles.get(label, False)
                    await self.broadcast(self.state_msg())
                    await self.toast(f"{label}: " + (self.tr("вкл", "увімк") if self.toggles[label] else self.tr("выкл", "вимк")))
        except Exception as e:
            log.exception("Кнопка %s", label)
            await self.toast(self.tr("Ошибка", "Помилка") + f" «{label}»: {e}")

    # ---------- скрипты кнопок (как AutoHotkey) ----------
    def parse_script(self, text):
        """Разбор + проверка названий в действиях дека (режим, выход звука): ошибка видна сразу в редакторе."""
        uk = self.store.cfg.get("lang") == "uk"
        steps, errors = scriptmod.parse(text, keys.known, uk=uk)
        for n, raw in enumerate(str(text or "").splitlines(), 1):
            one, bad = scriptmod.parse(raw, keys.known, uk=uk)
            if bad or not one:
                continue
            st = one[0]
            if st[0] == "mode" and not self.find_mode(st[1]):
                errors.append((n, self.tr(f"нет режима «{st[1]}»", f"немає режиму «{st[1]}»")))
            elif st[0] == "out" and (self.last.get("audio") or {}).get("devices") and not self.find_output(st[1]):
                errors.append((n, self.tr(f"нет выхода звука «{st[1]}»", f"немає виходу звуку «{st[1]}»")))
        errors.sort(key=lambda e: e[0] or 10 ** 6)
        return steps, errors

    # ---------- действия дека из сценариев: найти по названию ----------
    def find_mode(self, name):
        q = str(name).strip().casefold()
        for m in self.store.mode_ids():
            if not self.store.mode_on(m):
                continue
            if q in {m.casefold(), self.store.mode_name(m).casefold(), self.store.mode_name(m, True).casefold()}:
                return m
        return None

    def find_output(self, name):
        q = str(name).strip().casefold()
        devs = (self.last.get("audio") or {}).get("devices") or []
        return next((d for d in devs if d.get("name", "").casefold() == q), None) \
            or next((d for d in devs if q in d.get("name", "").casefold()), None)

    async def find_app(self, name):
        """Программа по подписи: сначала плитки (там уже есть путь и процесс), потом найденные на ПК."""
        q = str(name).strip().casefold()
        tiles = [e for items in self.store.layouts.values() for e in app_entries(items) if e.get("path")]
        inst = [app_item(a) for a in await apps.list_installed()]
        for pool in (tiles, inst):
            hit = next((e for e in pool if e.get("label", "").casefold() == q), None) \
                or next((e for e in pool if q in e.get("label", "").casefold()), None)
            if hit:
                return hit
        return None

    async def deck_step(self, st):
        """Один шаг-действие дека из сценария. Не получилось — исключение с понятным текстом."""
        kind = st[0]
        if kind == "mode":
            m = self.find_mode(st[1])
            if not m:
                raise ValueError(self.tr(f"нет режима «{st[1]}»", f"немає режиму «{st[1]}»"))
            await self.switch_mode(m)
        elif kind in ("run", "close"):
            it = await self.find_app(st[1])
            if not it:
                raise ValueError(self.tr(f"программа «{st[1]}» не найдена", f"програму «{st[1]}» не знайдено"))
            if kind == "run":
                if it.get("proc"):                  # правила автопереключения эту программу пока не трогают
                    self.scen_procs[it["proc"]] = time.monotonic() + SCEN_GRACE
                await self.open_app(it, apps.running_procs())
            else:
                await apps.close(it.get("proc"), it.get("target"))
        elif kind == "out":
            await self.poll_audio()
            d = self.find_output(st[1])
            if not d:
                raise ValueError(self.tr(f"нет выхода звука «{st[1]}»", f"немає виходу звуку «{st[1]}»"))
            await comthread.run(audio.set_default_output, d.get("id"))
            await self.poll_audio()
        elif kind == "vol":
            if st[1]:
                await comthread.run(audio.set_session, st[1], st[2])
            else:
                await comthread.run(audio.set_master, st[2])
            await self.poll_audio()
        elif kind == "mic":
            snap = await comthread.run(audio.snapshot)
            if bool(snap.get("micMuted")) == st[1]:           # выключить нужно, если сейчас включён, и наоборот
                await comthread.run(audio.toggle_mic)
            await self.poll_audio()
        elif kind == "lamp":                                   # шаг «лампа 60» — плагину лампы
            await self.plugins.action("lamp", "level", st[1])

    async def toggle_script(self, sid, label, it, times=None):
        """Нажали кнопку-скрипт: не идёт — запустить, идёт — остановить."""
        task = self.scripts.get(sid)
        if task and not task.done():
            if not task.cancelling():        # уже останавливается (двойной тап) — не дёргаем второй раз
                task.cancel()
                await self.toast(self.tr(f"Скрипт «{label}» остановлен", f"Скрипт «{label}» зупинено"))
            return
        steps, errors = self.parse_script(it.get("script"))
        if errors:
            n, msg = errors[0]
            await self.toast(f"«{label}»: " + (self.tr(f"строка {n}: ", f"рядок {n}: ") if n else "") + msg)
            return
        if not steps:
            await self.toast(self.tr(f"Скрипт «{label}» пустой", f"Скрипт «{label}» порожній"))
            return
        n, loop = (times, False) if times else scriptmod.repeat_of(it)
        self.scripts[sid] = asyncio.create_task(self.run_script(sid, label, steps, n, loop))
        await self.broadcast(self.state_msg())
        if loop:
            await self.toast(self.tr(f"«{label}» — по кругу. Нажми ещё раз, чтобы остановить",
                                     f"«{label}» — по колу. Натисни ще раз, щоб зупинити"))
        else:
            await self.toast(self.tr("Скрипт: ", "Скрипт: ") + label + (f" ×{n}" if n > 1 else ""))

    async def run_script(self, sid, label, steps, times, loop):
        lp = asyncio.get_running_loop()
        held, skipped = [], 0
        t_start, quiet = lp.time(), sid.startswith(("ctx:", "__test__"))
        try:
            done = 0
            scenario = any(st[0] in scriptmod.DECK for st in steps)
            while loop or done < times:
                t0 = lp.time()
                for i, st in enumerate(steps, 1):
                    if scenario or sid == "__test__":   # сценарий — на плитке, «Проверить» — в редакторе: какой шаг идёт
                        self.steps[sid] = f"{i}/{len(steps)}"
                        await self.broadcast(self.state_msg())
                    if st[0] in scriptmod.DECK:
                        try:
                            await self.deck_step(st)
                        except Exception as e:
                            raise RuntimeError(self.tr(f"шаг {i}: ", f"крок {i}: ") + str(e)) from None
                    elif st[0] == "keys":
                        await lp.run_in_executor(None, keys.hotkey, st[1])
                    elif st[0] == "wait":
                        await asyncio.sleep(st[1] / 1000)
                    elif st[0] == "text":
                        skipped += await lp.run_in_executor(None, keys.type_text, st[1]) or 0
                    elif st[0] == "hold":
                        held.append(st[1])           # до нажатия: если остановят посреди — отпустим в finally
                        await lp.run_in_executor(None, keys.press, st[1])
                        await asyncio.sleep(st[2] / 1000)
                        held.pop()
                        await lp.run_in_executor(None, keys.release, st[1])
                done += 1
                if loop:      # по кругу без пауз — не чаще раза в 20 мс
                    await asyncio.sleep(max(0.0, scriptmod.MIN_LOOP_MS / 1000 - (lp.time() - t0)))
            log.info("Скрипт «%s»: готово (%d раз)", label, done)
            if not quiet:                     # долгие (сценарии) — и в уведомления, быстрые — только в историю
                await self.event("script", self.tr(f"«{label}»: готово", f"«{label}»: готово"),
                                 notify=lp.time() - t_start > 3)
        except asyncio.CancelledError:
            log.info("Скрипт «%s»: остановлен", label)
            if not quiet:
                await self.event("script", self.tr(f"«{label}»: остановлен", f"«{label}»: зупинено"))
        except Exception as e:
            log.warning("Скрипт «%s»: %s", label, e)
            await self.toast(f"«{label}»: {e}")
            await self.event("script", f"«{label}»: {e}", level="error", notify=True)
        finally:
            for combo in held:                # остановили, пока клавиша зажата: отпустить, даже если стоп пришёл дважды
                try:
                    await asyncio.shield(lp.run_in_executor(None, keys.release, combo))
                except (Exception, asyncio.CancelledError):
                    pass
            if self.scripts.get(sid) is asyncio.current_task():
                del self.scripts[sid]
                if self.steps.pop(sid, None) is not None:   # сценарий кончился: его программы ещё грузятся
                    end = time.monotonic() + SCEN_GRACE
                    self.scen_procs = {p: max(d, end) for p, d in self.scen_procs.items()}
            if skipped:
                await self.toast(self.tr(f"На Linux текст набирается только латиницей — пропущено символов: {skipped}",
                                         f"На Linux текст набирається лише латиницею — пропущено символів: {skipped}"))
            await self.broadcast(self.state_msg())

    # ---------- входящие ----------
    async def handle(self, ws, m):
        """Сообщение с телефона → свой обработчик msg_<вид> (таблица HANDLERS — после класса); неизвестные — мимо."""
        fn = HANDLERS.get(m.get("t"))
        if fn and not rolesmod.allowed(self.role_of(ws), m.get("t"), m):
            log.info("Роль «%s»: отказ в «%s»", self.role_of(ws)["role"], m.get("t"))
            if m.get("t") not in ("viz", "eventsRead", "diag"):
                await self.send(ws, {"t": "toast", "text": self.tr("Этому устройству так нельзя", "Цьому пристрою так не можна")})
            return
        if fn:
            t0 = time.perf_counter()
            try:
                await fn(self, ws, m)
            finally:
                ms = (time.perf_counter() - t0) * 1000
                self.health.took(m.get("t"), ms)
                if ms > healthmod.SLOW_MS:
                    log.warning("Сообщение «%s» обрабатывалось %.0f мс — дек ждал", m.get("t"), ms)

    async def msg_mode(self, ws, m):
        await self.switch_mode(m.get("mode"))

    async def msg_ctx(self, ws, m):
        await self.ctx_action(m.get("mode"), int(m.get("index", -1)), str(m.get("label") or "")[:40],
                              str(m.get("script") or "")[:2000], m.get("window") is not False)

    async def msg_press(self, ws, m):
        await self.press(m.get("mode"), int(m.get("index", -1)))

    async def msg_layout(self, ws, m):
        mode, items = m.get("mode"), tilesmod.clean_items(m.get("items"))
        if self.store.exists(mode) and items is not None:
            self.store.layouts[mode] = items
            self.store.save()
            await self.broadcast(self.config_msg())

    async def msg_add(self, ws, m):
        mode = m.get("mode")
        if self.store.exists(mode):
            inst = {a["path"]: a for a in await apps.list_installed()}
            picked = [app_item(inst[a["path"]]) for a in m.get("apps", []) if a.get("path") in inst]
            group = (m.get("group") or "").strip()
            if group and picked:
                self.store.layouts[mode].append({"kind": "group", "label": group[:60], "items": picked,
                                                 "auto": False, "mono": None})
                log.info("Группа «%s»: %s", group, ", ".join(x["label"] for x in picked))
            else:
                self.store.layouts[mode].extend(picked)
            self.store.save()
            await self.broadcast(self.config_msg())

    async def msg_groupAdd(self, ws, m):
        mode, idx = m.get("mode"), int(m.get("index", -1))
        lay = self.store.layouts.get(mode) or []
        if 0 <= idx < len(lay) and lay[idx].get("kind") == "group":
            inst = {a["path"]: a for a in await apps.list_installed()}
            grp = lay[idx]
            have = {x.get("path") for x in grp.setdefault("items", [])}
            for a in m.get("apps", []):
                if a.get("path") in inst and a["path"] not in have:
                    grp["items"].append(app_item(inst[a["path"]]))
            self.store.save()
            await self.broadcast(self.config_msg())

    async def msg_browse(self, ws, m):
        mode = m.get("mode")
        if self.store.exists(mode):
            await self.toast(self.tr("Открыл окно выбора файла на ПК", "Відкрив вікно вибору файлу на ПК"))
            try:
                item = await asyncio.get_running_loop().run_in_executor(None, apps.pick_exe)
            except Exception as e:
                log.exception("Окно выбора файла")
                await self.toast(self.tr("Не открылось окно выбора: ", "Не відкрилося вікно вибору: ") + str(e))
                item = None
            if item:
                new_item = app_item(item)
                lay = self.store.layouts[mode]
                gi = m.get("index")
                if isinstance(gi, int) and 0 <= gi < len(lay) and lay[gi].get("kind") == "group":
                    lay[gi].setdefault("items", []).append(new_item)
                else:
                    lay.append(new_item)
                self.store.save()
                await self.broadcast(self.config_msg())
                await self.toast(self.tr("Добавлено: ", "Додано: ") + item["label"])

    async def msg_cfg(self, ws, m):
        cfg = self.clean_cfg(m.get("cfg"))
        self.store.cfg.update(cfg)
        self.store.save()
        await self.broadcast(self.config_msg())
        if "hk" in cfg and self.mode == HK and not self.store.mode_on(HK):
            self.mode = self.base
            await self.broadcast(self.state_msg())
        if "lang" in cfg:
            i18n.set_lang(self.store.cfg.get("lang"))   # лог, окна выбора файлов и ярлык в меню — тоже
            if self.tray and hasattr(self.tray, "lang_changed"):
                self.tray.lang_changed()
            if hasattr(tray_mod(), "menu_entry"):
                await asyncio.get_running_loop().run_in_executor(None, tray_mod().menu_entry)
            await self.updater.push()          # тексты обновлений — на новом языке
        if "lang" in cfg:
            await self.plugins.push()             # названия и кнопки плагинов — на новом языке
        if self.pad and any(k.startswith("pad") for k in cfg):
            self.pad.config_changed()
        if cfg.get("viz") is False:
            for who in list(self.viz.subs):
                self.viz.want(who, False)

    async def msg_modeCfg(self, ws, m):
        mode, icon = m.get("id"), str(m.get("icon") or "")
        if self.store.exists(mode):
            name = " ".join(str(m.get("name") or "").split())[:24]
            self.store.set_mode_meta(mode, name, icon if re.fullmatch(r"[a-z]{1,16}", icon) else "")
            color = str(m.get("color") or "")
            if re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
                self.store.cfg[mode] = color
                self.store.save()
            if "cols" in m or "rows" in m:
                cols, rows = self.store.set_grid(mode, m.get("cols"), m.get("rows"))
                log.info("Режим «%s»: сетка %d×%d", self.store.mode_name(mode), cols, rows)
            await self.broadcast(self.config_msg())

    async def msg_modeAdd(self, ws, m):
        # свой режим из «Правки» на главном экране
        name = " ".join(str(m.get("name") or "").split())[:24] or self.tr("Новый режим", "Новий режим")
        icon, color = str(m.get("icon") or ""), str(m.get("color") or "")
        mid = self.store.add_mode(name, icon if re.fullmatch(r"[a-z]{1,16}", icon) else "star",
                                  color if re.fullmatch(r"#[0-9A-Fa-f]{6}", color) else None)
        if "cols" in m or "rows" in m:
            self.store.set_grid(mid, m.get("cols"), m.get("rows"))
        log.info("Новый режим «%s» (%s)", name, mid)
        await self.broadcast(self.config_msg())
        await self.toast(self.tr("Новый режим: ", "Новий режим: ") + name)

    async def msg_modeDel(self, ws, m):
        mode = m.get("id")
        name = self.store.mode_name(mode) if self.store.is_user(mode) else ""
        if self.store.del_mode(mode):
            log.info("Удалён режим «%s» (%s)", name, mode)
            if self.base == mode:
                self.base = None
            if self.mode == mode:
                self.mode = None
                await self.broadcast(self.state_msg())
            await self.broadcast(self.config_msg())
            await self.toast(self.tr("Удалён режим: ", "Видалено режим: ") + name)

    async def msg_modeOrder(self, ws, m):
        self.store.set_order(m.get("ids"))
        await self.broadcast(self.config_msg())

    async def msg_scriptCheck(self, ws, m):
        # редактор скрипта на телефоне: проверить текст и показать, что получится
        steps, errors = self.parse_script(str(m.get("text") or "")[:20000])
        await self.send(ws, {"t": "scriptInfo", "seq": m.get("seq"), "steps": len(steps),
                             "secs": scriptmod.seconds(steps), "errors": errors[:5]})

    async def msg_scriptTest(self, ws, m):
        # «Проверить» в редакторе: один прогон, повторное нажатие — стоп
        await self.toggle_script("__test__", self.tr("Проверка", "Перевірка"), {"script": str(m.get("text") or "")}, times=1)

    # ---------- обновления с GitHub (update.py) ----------
    async def msg_updCheck(self, ws, m):
        await self.updater.check(manual=True)

    async def msg_updApply(self, ws, m):
        await self.updater.apply()

    async def msg_updNow(self, ws, m):
        await self.updater.tray_click(notify=False)   # «Обновить»: проверит и, если есть новее, поставит

    async def msg_updFile(self, ws, m):
        await self.updater.pick_file()

    async def msg_updRollback(self, ws, m):
        await self.updater.rollback()

    async def msg_updToken(self, ws, m):
        await self.updater.set_token(str(m.get("token") or "")[:300])

    async def msg_changelog(self, ws, m):
        """«Лог изменений» в настройках: что поменялось в установленной версии (новая — в upd.changes)."""
        await self.send(ws, {"t": "changelog", "items": changelogmod.entries()})

    async def msg_key(self, ws, m):
        combo = str(m.get("keys") or "")[:60]
        if combo:
            try:
                await asyncio.get_running_loop().run_in_executor(None, keys.hotkey, combo)
            except Exception as e:
                log.warning("Клавиша %s: %s", combo, e)
                await self.toast(str(e))

    async def msg_padList(self, ws, m):
        if self.pad:
            self.pad.scan()

    async def msg_pad(self, ws, m):
        if self.pad and m.get("id"):
            self.pad.play(str(m["id"]))

    async def msg_padStop(self, ws, m):
        if self.pad:
            self.pad.stop()

    async def msg_padDevices(self, ws, m):
        if self.pad:
            self.pad.refresh_devices(force=bool(m.get("force")))

    async def msg_clipSet(self, ws, m):
        text = str(m.get("text") or "")[:CLIP_MAX]
        if text.strip():
            ok = await asyncio.get_running_loop().run_in_executor(None, clip.set, text)
            if ok:
                self.clip_seq = await asyncio.get_running_loop().run_in_executor(None, clip.seq)   # своё — не ловить обратно
                await self.clip_add("phone", text)
            await self.toast(self.tr("Скопировано на ПК", "Скопійовано на ПК") if ok else
                             self.tr("Не вышло записать в буфер ПК", "Не вийшло записати в буфер ПК"))

    async def msg_clipGet(self, ws, m):
        await self.send(ws, {"t": "clip", "items": self.clip_items})

    async def msg_openUrl(self, ws, m):
        url = str(m.get("url") or "")
        if re.fullmatch(r"https?://\S{1,2000}", url):
            await asyncio.get_running_loop().run_in_executor(None, apps.launch, {"label": url, "path": url})
            await self.toast(self.tr("Открыл на ПК", "Відкрив на ПК"))

    async def msg_devices(self, ws, m):
        snap = await asyncio.get_running_loop().run_in_executor(None, devices.snapshot)
        phones = sorted({self.ips.get(c, "?") for c in self.clients})
        await self.send(ws, dict(snap, t="devices", phones=phones, paired=self.phones_list(ws)))

    async def msg_pairCode(self, ws, m):                        # «Подключить новый телефон» в окне «Устройства»
        await self.send(ws, {"t": "pairCode", "code": self.pair.current(), "ttl": int(self.pair.code["until"] - self.pair.now())})

    async def msg_phoneForget(self, ws, m):
        pid = str(m.get("id") or "")
        name = (self.pair.phones.get(pid) or {}).get("name", "")
        if self.pair.forget(pid):
            log.info("Устройство забыто: %s", name)
            for c in [c for c, w in self.who.items() if w == pid]:
                await c.close()
            for c in list(self.clients):
                if c in self.who:
                    await self.send(c, {"t": "phones", "paired": self.phones_list(c)})

    async def msg_wlGrab(self, ws, m):
        wins = await asyncio.get_running_loop().run_in_executor(None, apps.grab_windows)
        for w in wins:
            it = await self.find_app_proc(w["proc"])
            w["label"], w["path"] = (it.get("label"), it.get("path")) if it else (w["title"][:40] or w["proc"], "")
        await self.send(ws, {"t": "wlWins", "wins": wins})

    async def msg_wlSave(self, ws, m):
        await self.save_layout(m)

    async def msg_wl(self, ws, m):
        L = next((x for x in self.store.cfg.get("winLayouts") or [] if x["id"] == m.get("id")), None)
        if L:
            await self.restore_layout(L["id"], L["name"])

    async def msg_eventsRead(self, ws, m):
        self.events.mark_read(m.get("upto"))
        await self.broadcast({"t": "eventsRead", "read": self.events.read, "unread": self.events.unread()}, remember=False)

    async def msg_run(self, ws, m):
        # «Повторить» из истории: запустить (или показать) программу по подписи
        it = await self.find_app(str(m.get("name") or ""))
        if it:
            await self.open_app(it, apps.running_procs())
            await self.toast(self.tr("Запуск: ", "Запуск: ") + it["label"])

    async def msg_diag(self, ws, m):
        (self.diag_subs.add if m.get("on") else self.diag_subs.discard)(ws)
        if self.diag_subs and (self.diag_task is None or self.diag_task.done()):
            self.diag_task = asyncio.ensure_future(self.diag_loop())

    async def msg_viz(self, ws, m):
        # шапку видно (экран включён, не правка, не микшер) — дай спектр того, что играет на ПК
        self.viz.want(ws, bool(m.get("on")) and self.store.cfg.get("viz") is not False)

    async def msg_padMeter(self, ws, m):
        if self.pad:
            self.pad.meter(bool(m.get("on")))

    async def msg_padAdd(self, ws, m):
        await self.toast(self.tr("Открыл на ПК окно выбора звуков", "Відкрив на ПК вікно вибору звуків"))
        try:
            added = await asyncio.get_running_loop().run_in_executor(None, padmod.pick_files)
        except Exception as e:
            log.exception("Выбор звуков")
            await self.toast(self.tr("Не открылось окно выбора: ", "Не відкрилося вікно вибору: ") + str(e))
            added = []
        if added and self.pad:
            self.pad.scan()
            await self.toast(self.tr("Добавлено: ", "Додано: ") + ", ".join(added[:5]) + (" …" if len(added) > 5 else ""))

    async def msg_padFolder(self, ws, m):
        try:
            await asyncio.get_running_loop().run_in_executor(None, padmod.open_folder)
            await self.toast(self.tr("Открыл папку звуков на ПК", "Відкрив папку звуків на ПК"))
        except Exception as e:
            await self.toast(self.tr("Не открылась папка: ", "Не відкрилася папка: ") + str(e))

    async def msg_plugin(self, ws, m):                                      # кнопка / переключатель / ползунок плагина
        try:
            await self.plugins.action(str(m.get("id")), str(m.get("a")), m.get("v"))
        except RuntimeError as e:
            await self.toast(str(e))

    async def msg_pin(self, ws, m):                                         # касание по живой картинке плагина
        self.plugins.input(str(m.get("id")), m)

    async def msg_pluginOn(self, ws, m):
        g = m.get("grant")                                   # согласие на разрешения (список, что видел пользователь)
        g = [str(x) for x in g][:20] if isinstance(g, list) else None
        if not await self.plugins.set_on(str(m.get("id")), bool(m.get("on")), grant=g) and m.get("on"):
            await self.toast(self.tr("Плагину нужны разрешения — включи его на странице «Плагины»",
                                     "Плагіну потрібні дозволи — увімкни його на сторінці «Плагіни»"))

    async def msg_pluginHalt(self, ws, m):                                  # «Остановить» зависший плагин
        await self.plugins.halt(str(m.get("id")))

    async def msg_pluginRestart(self, ws, m):
        await self.plugins.restart(str(m.get("id")))

    async def msg_pluginDelete(self, ws, m):
        """«Удалить» на странице «Плагины» — насовсем (файлы плагина и его прежние версии из каталога)."""
        if await self.plugins.remove(str(m.get("id") or "")):
            await self.send(ws, {"t": "toast", "text": self.tr("Плагин удалён", "Плагін видалено")})

    # ---------- моды дека ----------
    async def mods_scan(self):
        changed = await asyncio.get_running_loop().run_in_executor(None, self.mods.scan)
        await self.broadcast(self.mods.msg())
        return changed

    async def msg_modChoose(self, ws, m):
        """«Оставить новый» / «Вернуть как было» в окне «было / стало», переключатель в Настройки → «Моды»."""
        if self.mods.choose(str(m.get("id") or ""), bool(m.get("on"))):
            await self.broadcast(self.mods.msg())

    async def msg_lookSave(self, ws, m):
        """Редактор вида: собранный на телефоне вид → мод mods/<id>/ (сразу включён: его уже видели вживую).
        Другие виды из редактора выключаются — два сразу спорили бы друг с другом."""
        try:
            mid = await asyncio.get_running_loop().run_in_executor(
                None, self.mods.save_look, m.get("id") or None, m.get("name"), m.get("look"), m.get("css"), m.get("img"),
                m.get("layout"))
        except (ValueError, OSError) as e:
            log.warning("Редактор вида: %s", e)
            await self.send(ws, {"t": "toast", "text": self.tr("Вид не сохранился: ", "Вигляд не зберігся: ") + str(e)})
            return
        await asyncio.get_running_loop().run_in_executor(None, self.mods.scan)
        for other in self.mods.looks():
            if other != mid and self.mods.on(other):
                self.mods.choose(other, False)
        self.mods.choose(mid, True)
        await self.broadcast(self.mods.msg())
        await self.send(ws, {"t": "lookSaved", "id": mid})

    async def purge_trash(self):
        """Корзины прежних версий («удалённые» плагины и моды лежали в _deleted): теперь удаляем насовсем — их тоже."""
        for trash in (PLUGINS / "_deleted", MODS / "_deleted"):
            if trash.is_dir():
                await asyncio.get_running_loop().run_in_executor(None, functools.partial(shutil.rmtree, trash, ignore_errors=True))
                log.info("Стёрта прежняя корзина %s", trash.name if not trash.is_relative_to(ROOT) else trash.relative_to(ROOT))

    async def msg_modDelete(self, ws, m):
        """«Удалить» на странице «Моды» — насовсем: папка мода, его прежние версии из каталога, выбор в cfg.mods."""
        mid = str(m.get("id") or "")
        if mid not in self.mods.items:
            return
        d = self.mods.items[mid].folder

        def wipe():
            shutil.rmtree(d)
            shutil.rmtree(MODS / "_backups" / mid, ignore_errors=True)
        try:
            await asyncio.get_running_loop().run_in_executor(None, wipe)
        except OSError as e:
            log.warning("Мод «%s»: не удалось удалить: %s", mid, e)
            await self.send(ws, {"t": "toast", "text": self.tr("Мод не удалился: ", "Мод не видалився: ") + str(e)})
            return
        self.store.cfg.get("mods", {}).pop(mid, None)
        self.store.save()
        log.info("Мод «%s» удалён", mid)
        await self.mods_scan()
        await self.send(ws, {"t": "toast", "text": self.tr("Мод удалён", "Мод видалено")})

    async def sort_folders(self):
        moved = await asyncio.get_running_loop().run_in_executor(None, modsmod.sort_folders, PLUGINS, MODS)
        if any(t for _, t in moved):
            await self.plugins.scan(force=True)
        return moved

    async def msg_modsScan(self, ws, m):
        await self.sort_folders()
        await self.mods_scan()

    async def msg_modsFolder(self, ws, m):
        try:
            MODS.mkdir(exist_ok=True)
            await asyncio.get_running_loop().run_in_executor(None, padmod.open_folder, MODS)
            await self.toast(self.tr("Открыл папку модов на ПК", "Відкрив теку модів на ПК"))
        except Exception as e:
            await self.toast(self.tr("Не открылась папка: ", "Не відкрилася тека: ") + str(e))

    async def msg_catalog(self, ws, m):
        """Страница «Каталог»: открыли — свежий список (проверка, если давно не смотрели или просят)."""
        if m.get("check") or time.time() - self.catalog.checked > 600:
            await self.catalog.check()
        else:
            await self.send(ws, self.catalog.msg())

    async def msg_catalogInstall(self, ws, m):
        res = await self.catalog.install(str(m.get("id") or ""))
        if res:
            await self.send(ws, {"t": "toast", "text": self.cat_error(res)})

    async def msg_catalogRollback(self, ws, m):
        res = await self.catalog.rollback(str(m.get("id") or ""), str(m.get("name") or ""))
        if res:
            await self.send(ws, {"t": "toast", "text": self.cat_error(res)})

    def cat_error(self, code):
        return {"busy": self.tr("Каталог занят — подожди, пока закончит", "Каталог зайнятий — зачекай, поки закінчить"),
                "none": self.tr("Нет такого плагина или копии", "Немає такого плагіна чи копії"),
                "api_new": self.tr("Плагину нужна новая версия дека — сначала обнови дек", "Плагіну потрібна нова версія дека — спершу онови дек"),
                "api_old": self.tr("Плагин для старой версии дека", "Плагін для старої версії дека"),
                "os": self.tr("Плагин не для этой ОС", "Плагін не для цієї ОС"),
                "bad": self.tr("Файлы плагина не сошлись с каталогом — попробуй позже", "Файли плагіна не зійшлися з каталогом — спробуй пізніше"),
                "token": self.tr("GitHub не пустил: проверь токен в настройках обновления", "GitHub не пустив: перевір токен у налаштуваннях оновлення"),
                "private": self.tr("Репозиторий закрыт: нужен токен (настройки → обновление)", "Репозиторій закритий: потрібен токен (налаштування → оновлення)"),
                "rate": self.tr("GitHub просит подождать (лимит запросов)", "GitHub просить зачекати (ліміт запитів)"),
                "net": self.tr("Нет связи с GitHub", "Немає зв’язку з GitHub"),
                }.get(code, self.tr("Не получилось — смотри лог", "Не вийшло — дивись лог"))

    async def msg_healthReport(self, ws, m):
        """Отчёт для разработчика: zip в logs/reports (состояние, конец логов, конфиг без секретов)."""
        try:
            path = await asyncio.get_running_loop().run_in_executor(
                None, healthmod.report, ROOT, self.health_msg(), self.store.cfg, updmod.local_version(ROOT))
            log.info("Отчёт для разработчика: %s", path)
            await self.send(ws, {"t": "healthReport", "name": path.name, "path": str(path)})
            if m.get("open"):
                await asyncio.get_running_loop().run_in_executor(None, padmod.open_folder, path.parent)
        except Exception as e:
            await self.toast(self.tr("Отчёт не собрался: ", "Звіт не зібрався: ") + str(e))

    async def msg_pluginsOn(self, ws, m):
        await self.plugins.set_master(bool(m.get("on")))

    async def msg_pluginsScan(self, ws, m):
        if any(t for _, t in await self.sort_folders()):
            await self.mods_scan()
        await self.plugins.scan(force=True)
        await self.toast(self.tr("Плагины перечитаны", "Плагіни перечитано"))

    async def msg_pluginsFolder(self, ws, m):
        try:
            PLUGINS.mkdir(exist_ok=True)
            await asyncio.get_running_loop().run_in_executor(None, padmod.open_folder, PLUGINS)
            await self.toast(self.tr("Открыл папку плагинов на ПК", "Відкрив теку плагінів на ПК"))
        except Exception as e:
            await self.toast(self.tr("Не открылась папка: ", "Не відкрилася тека: ") + str(e))

    async def msg_media(self, ws, m):
        if self.media:
            self.media.command(m.get("cmd"))

    async def msg_vol(self, ws, m):
        target, value = m.get("target"), float(m.get("value", 0))
        if target == "__master__":
            await comthread.run(audio.set_master, value)
        else:
            await comthread.run(audio.set_session, target, value)
        old = self.vol_timers.pop(target, None)
        if old:
            old.cancel()
        name = self.tr("общая", "загальна") if target == "__master__" else target
        self.vol_timers[target] = asyncio.get_running_loop().call_later(1.5, lambda: asyncio.ensure_future(self.event(
            "audio", self.tr(f"Громкость ({name}): {round(value)}%", f"Гучність ({name}): {round(value)}%"),
            redo={"t": "vol", "target": target, "value": value})))

    async def msg_out(self, ws, m):
        try:
            await comthread.run(audio.set_default_output, m.get("id"))
            await self.poll_audio()
        except Exception as e:
            log.exception("Смена выхода")
            await self.toast(self.tr("Не удалось сменить выход: ", "Не вдалося змінити вихід: ") + str(e))

    async def msg_listApps(self, ws, m):
        lst = await apps.list_installed(refresh=bool(m.get("refresh")))
        await self.send(ws, {"t": "apps", "list": [{"label": a["label"], "path": a["path"], "src": a.get("src", ""),
                                                      "proc": a.get("proc")} for a in lst]})

    async def msg_ping(self, ws, m):
        await self.send(ws, {"t": "pong"})

    # ---------- общий буфер обмена ----------
    async def clip_add(self, src, text):
        self.clip_items = [x for x in self.clip_items if x["text"] != text][-19:]
        self.clip_items.append({"id": int(time.time() * 1000), "ts": time.time(), "src": src, "text": text})
        await self.broadcast({"t": "clip", "items": self.clip_items}, remember=False)

    async def clip_poll(self):
        """Включён «Брать с ПК»: скопировали текст на ПК — он появляется в буфере на телефоне.
        Секретное (пометка менеджера паролей, похоже на пароль) и слишком длинное — не берём."""
        loop = asyncio.get_running_loop()
        s_ = await loop.run_in_executor(None, clip.seq)
        if s_ == self.clip_seq:
            return
        first, self.clip_seq = self.clip_seq is None, s_
        text = await loop.run_in_executor(None, clip.get)
        if first or not text or not text.strip() or len(text) > CLIP_MAX or looks_secret(text):
            return
        if self.clip_items and self.clip_items[-1]["text"] == text:
            return
        await self.clip_add("pc", text)

    # ---------- раскладки окон ----------
    async def find_app_proc(self, proc):
        tiles = [e for items in self.store.layouts.values() for e in app_entries(items) if e.get("proc") == proc and e.get("path")]
        if tiles:
            return tiles[0]
        return next((app_item(a) for a in await apps.list_installed() if a.get("proc") == proc and a.get("path")), None)

    async def save_layout(self, m):
        """Новая раскладка (плитка в режим) или «Запомнить заново» (id есть): окна с телефона, имя."""
        lays = list(self.store.cfg.get("winLayouts") or [])
        lid = str(m.get("id") or "") or "w" + format(int(time.time() * 1000) % (36 ** 6), "x")
        new = self.clean_layouts([{"id": lid, "name": str(m.get("name") or self.tr("Раскладка", "Розкладка")), "wins": m.get("wins")}])
        if not new or not new[0]["wins"]:
            await self.toast(self.tr("Не выбрано ни одного окна", "Не вибрано жодного вікна"))
            return
        old = next((i for i, L in enumerate(lays) if L["id"] == lid), None)
        if old is None:
            lays.append(new[0])
            mode = m.get("mode")
            if self.store.exists(mode):
                self.store.layouts.setdefault(mode, []).append({"kind": "macro", "action": "winlayout", "id": lid,
                                                                 "label": new[0]["name"], "mono": None})
        else:
            lays[old] = new[0]
        self.store.cfg["winLayouts"] = lays
        self.store.save()
        await self.broadcast(self.config_msg())
        await self.toast(self.tr(f"Раскладка «{new[0]['name']}»: окон {len(new[0]['wins'])}",
                                 f"Розкладка «{new[0]['name']}»: вікон {len(new[0]['wins'])}"))

    async def restore_layout(self, lid, label):
        """Расставить окна: запущенные — на место (свёрнутые развернуть), не запущенные — запустить и дождаться окна."""
        L = next((x for x in self.store.cfg.get("winLayouts") or [] if x["id"] == lid), None)
        if not L:
            await self.toast(self.tr(f"«{label}»: раскладка не найдена", f"«{label}»: розкладку не знайдено"))
            return
        loop = asyncio.get_running_loop()
        running = apps.running_procs()
        placed, waiting, missing = 0, [], []
        for w in L["wins"]:
            args = (w["proc"], w["x"], w["y"], w["w"], w["h"], w["max"])
            if w["proc"] in running and await loop.run_in_executor(None, apps.place_window, *args):
                placed += 1
            elif w.get("path") and w["proc"] not in running:
                await loop.run_in_executor(None, apps.launch, {"label": w["label"], "path": w["path"], "proc": w["proc"]})
                waiting.append(args)
            else:
                missing.append(w["label"])
        await self.toast(self.tr(f"«{L['name']}»: окон на месте {placed}", f"«{L['name']}»: вікон на місці {placed}")
                         + (self.tr(f", запускаю {len(waiting)}", f", запускаю {len(waiting)}") if waiting else ""))
        end = time.monotonic() + 15
        while waiting and time.monotonic() < end:
            await asyncio.sleep(0.7)
            waiting = [a for a in waiting if not await loop.run_in_executor(None, apps.place_window, *a)]
        await self.event("app", self.tr(f"Раскладка «{L['name']}»", f"Розкладка «{L['name']}»"),
                         redo={"t": "wl", "id": lid})

    async def track_running(self, procs):
        """История: программы с плиток запустились / закрылись (первый опрос — только запомнить)."""
        labels = {e["proc"]: e.get("label") for items in self.store.layouts.values() for e in app_entries(items) if e.get("proc")}
        prev = self.seen.get("procs")
        if prev is not None:
            for p_ in sorted(set(procs) - prev):
                await self.event("app", self.tr("Запущена: ", "Запущено: ") + str(labels.get(p_, p_)),
                                 redo={"t": "run", "name": labels.get(p_, p_)})
            for p_ in sorted(prev - set(procs)):
                await self.event("app", self.tr("Закрыта: ", "Закрито: ") + str(labels.get(p_, p_)))
        self.seen["procs"] = set(procs)

    async def diag_loop(self):
        """Окно «Диагностика» открыто хоть на одном телефоне — замеры раз в 2 с."""
        loop = asyncio.get_running_loop()
        if self.diag is None:
            self.diag = Diag()
        while self.diag_subs:
            try:
                snap = await asyncio.wait_for(loop.run_in_executor(None, self.diag.snapshot), 20)
            except Exception as e:               # не упасть насовсем: телефону — что случилось, и пробовать дальше
                log.exception("Диагностика")
                snap = {"t": "diag", "fail": f"{type(e).__name__}: {e}"}
            hs = self.health_msg()
            for ws in list(self.diag_subs):
                await self.send(ws, snap)
                await self.send(ws, hs)
            await asyncio.sleep(2)

    def health_msg(self):
        """Вкладка «Дек»: процесс, потоки, сообщения, плагины, подключения, ошибки."""
        try:
            ver = updmod.local_version(ROOT) or {}
        except Exception:
            ver = {}
        cl = [dict(self.cinfo.get(ws, {}), ip=self.ips.get(ws, "?")) for ws in self.clients]
        return self.health.snapshot(extra={"plugins": self.plugins.health(), "clients": cl, "viz": len(getattr(self.viz, "subs", ()) or ()),
                                           "version": {"sha": str(ver.get("sha", ""))[:7], "date": ver.get("date", ""), "msg": ver.get("msg", "")}})

    # ---------- опросы ----------
    async def poll_audio(self):
        try:
            snap = await comthread.run(audio.snapshot)
            out = next((d.get("id") for d in snap.get("devices") or [] if d.get("default")), None)
            names = {d.get("id"): d.get("name") for d in snap.get("devices") or []}
            if out != self.out_dev:
                if self.out_dev is not None:
                    self.viz.restart()
                    await self.event("audio", self.tr("Выход звука: ", "Вихід звуку: ") + str(names.get(out) or out),
                                     redo={"t": "out", "id": out})
                self.out_dev = out
            before = self.seen.get("devs")
            if before is not None and snap.get("available"):
                for i in before.keys() - names.keys():
                    await self.event("device", self.tr("Отключилось: ", "Відключилося: ") + str(before[i]), level="warn", notify=True)
                for i in names.keys() - before.keys():
                    await self.event("device", self.tr("Подключилось: ", "Підключилося: ") + str(names[i]), notify=True)
            if snap.get("available"):
                self.seen["devs"] = names
            msg = dict(snap, t="audio")
            if self.last.get("audio") != msg:
                await self.broadcast(msg)
        except Exception:
            log.exception("audio poll")

    async def pollers(self):
        loop = asyncio.get_running_loop()
        n = 0
        errs = {}

        async def step(name, fn):
            """Части опроса не зависят друг от друга; одну и ту же ошибку пишем в лог один раз."""
            try:
                await fn()
                errs.pop(name, None)
            except Exception as e:
                text = f"{type(e).__name__}: {e}"
                if errs.get(name) != text:
                    errs[name] = text
                    log.exception("poller: %s", name)

        async def stats():
            st = await loop.run_in_executor(None, sensors.read)
            await self.broadcast(dict(st, t="stats"))

        async def running():
            run = await loop.run_in_executor(None, apps.running_procs)      # обход всех процессов — ~8 мс, не в потоке сервера
            procs = sorted({e.get("proc") for items in self.store.layouts.values() for e in app_entries(items)
                            if e.get("proc") and e["proc"] in run})
            msg = {"t": "running", "procs": procs}
            if self.last.get("running") != msg:
                await self.broadcast(msg)
            await self.track_running(procs)
            await self.auto_modes(run)

        async def sounds():
            if self.pad:                     # закинули звук в папку — кнопка появится сама
                # не в потоке сервера: диск со звуками может просыпаться секунды — сервер зависал на 9 с
                await asyncio.get_running_loop().run_in_executor(None, self.pad.check_folder)

        async def clipboard():
            if self.store.cfg.get("clipSync"):
                await self.clip_poll()

        while True:
            # телефонов нет — датчики, буфер и звуки никому не нужны, а громкость и программы (история, автопереключение)
            # опрашиваем вдвое реже; автопереключение включено — программы как обычно
            seen = bool(self.clients)
            if seen:
                await step("датчики", stats)
                await step("буфер", clipboard)
            if n % 2 == 1:
                await step("плагины", self.plugins.scan)     # закинули / поменяли / удалили плагин — подхватить
            if n % (2 if seen else 4) == 0:
                await step("громкость", self.poll_audio)
            if n % (2 if seen or self.store.cfg.get("autoMode") else 4) == 0:
                await step("программы", running)
            if seen and n % 2 == 0:
                await step("звуки", sounds)
            n += 1
            await asyncio.sleep(1.5)

    def on_media(self, state):
        if self.loop:
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.broadcast(dict(state, t="media"))))

    def on_pad(self, msg):
        if self.loop:
            keep = msg.get("t") != "padMeter"        # уровни — живые, новым клиентам их не повторяем
            self.loop.call_soon_threadsafe(lambda: asyncio.ensure_future(self.broadcast(msg, remember=keep)))

    # ---------- HTTP ----------
    async def ws_handler(self, request):
        if not pairmod.same_origin(request):       # чужой сайт в браузере — не наша страница
            log.warning("Отказ %s: соединение открывает чужая страница (%s)", request.remote, str(request.headers.get("Origin"))[:120])
            return web.Response(status=403)
        ws = web.WebSocketResponse(heartbeat=15)
        await ws.prepare(request)
        who = self.auth(request)
        # обновление со старой версии: прежние телефоны — свои, ключ выдаём сами. Только новой странице (/ws?p=1):
        # прежняя ключ не поймёт и перезагрузится по номеру сборки — ключ остался бы лишней записью в списке
        if who is None and self.pair.grace() and request.query.get("p") == "1":
            who = await self.admit(ws, request)
        if who is None:
            who = await self.guest(ws, request)    # ждёт код с ПК; ушёл, не введя, — None
            if who is None:
                return ws
        self.who[ws] = who
        if who != "local":
            self.pair.touch(who, request.remote, device_name(request.headers.get("User-Agent", "")))
        self.ips[ws] = request.remote
        self.cinfo[ws] = {"since": time.time(), "ua": short_ua(request.headers.get("User-Agent", "")), "who": "local" if who == "local" else "phone"}
        self.clients.add(ws)
        log.info("Подключился клиент %s (всего %d)", request.remote, len(self.clients))
        log.info("Клиент %s: %s", request.remote, short_ua(request.headers.get("User-Agent", "")))
        self.errs[ws] = 0
        await self.send(ws, self.role_msg(ws))
        await self.send(ws, self.config_msg())
        await self.send(ws, self.pc_msg(request))
        await self.send(ws, self.state_msg())
        for k in ("stats", "audio", "media", "running", "plugins", "mods", "catalog", "padList", "padState", "upd"):
            if k in self.last:
                await self.send(ws, self.last[k])
        await self.updater.on_client(ws)
        await self.send(ws, self.events.msg())
        await self.event("phone", self.tr("Подключился телефон ", "Підключився телефон ") + str(request.remote), notify=True)
        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        m = json.loads(msg.data)
                        if m.get("t") == "ping":      # сторож связи на телефоне — не нажатие (touched не трогаем)
                            await self.send(ws, {"t": "pong"})
                            continue
                        if m.get("t") == "jserr":     # ошибка страницы на телефоне — в лог (не больше 30 за подключение)
                            self.errs[ws] = self.errs.get(ws, 0) + 1
                            if self.errs[ws] <= 30:
                                log.warning("Ошибка страницы на телефоне %s: %s", request.remote, str(m.get("m"))[:400])
                            continue
                        self.touched = time.monotonic()
                        await self.handle(ws, m)
                    except Exception:
                        log.exception("Сообщение: %s", msg.data[:200])
        finally:
            self.clients.discard(ws)
            self.viz.want(ws, False)
            self.diag_subs.discard(ws)
            self.ips.pop(ws, None)
            self.cinfo.pop(ws, None)
            self.who.pop(ws, None)
            self.errs.pop(ws, None)
            log.info("Клиент отключился %s", request.remote)
            await self.event("phone", self.tr("Отключился телефон ", "Відключився телефон ") + str(request.remote), notify=True)
        return ws

    # ---------- свои устройства ----------
    def auth(self, request):
        """Кто пришёл: "local" (сам ПК), id своего устройства (ключ в cookie) или None."""
        if pairmod.is_local(request):
            return "local"
        return self.pair.check(request.cookies.get(pairmod.COOKIE))

    def allowed(self, request):
        """Картинки, файлы плагинов, поток — только своим (пока идёт переход со старой версии — всем)."""
        if self.auth(request) is None and not self.pair.grace():
            raise web.HTTPForbidden()

    async def admit(self, ws, request):
        """Новое своё устройство: ключ — телефону, на ПК остаётся хэш."""
        pid, key = self.pair.issue(device_name(request.headers.get("User-Agent", "")), request.remote)
        await self.send(ws, {"t": "paired", "key": key})
        log.info("Новое своё устройство: %s (%s)", self.pair.phones[pid]["name"], request.remote)
        return pid

    async def show_code(self, force=False):
        """Код подключения — туда, где его увидит хозяин: уведомление на ПК и подсказка на своих телефонах."""
        if not force and not self.pair.may_show():
            return
        text = self.tr("Новое устройство просит доступ к деку. Код: ", "Новий пристрій просить доступ до дека. Код: ") + self.pair.current()
        if self.tray:
            self.tray.notify(text)
        await self.broadcast({"t": "toast", "text": text, "force": True, "ms": 20000, "mainOnly": True}, remember=False)

    async def guest(self, ws, request):
        """Неизвестное устройство: до ввода кода ему — только язык и номер сборки страницы. Вернёт id, когда стало своим."""
        ip = request.remote
        log.info("Неизвестное устройство %s: %s — жду код", ip, short_ua(request.headers.get("User-Agent", "")))
        # прежняя страница (до обновления дека) знает только config: по номеру сборки она перезагрузится на новую
        await self.send(ws, {"t": "config", "build": CLIENT_BUILD, "guest": True})
        hello = {"t": "pair", "lang": self.store.cfg.get("lang"), "pc": socket.gethostname()}
        await self.send(ws, hello)
        left = 300                                   # сообщений на одно соединение: дальше — закрываем
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            left -= 1
            if left < 0:
                break
            try:
                m = json.loads(msg.data)
                t = m.get("t") if isinstance(m, dict) else None
            except ValueError:
                continue
            if t == "ping":
                await self.send(ws, {"t": "pong"})
            elif t == "hello":                       # ключ сообщением: cookie у страницы выключены
                pid = self.pair.check(m.get("key"))
                if pid:
                    return pid
                await self.send(ws, dict(hello, err="key"))
            elif t == "pairAsk":
                await self.show_code()
            elif t == "pair":
                res = self.pair.enter(m.get("code"))
                if res == "ok":
                    pid = await self.admit(ws, request)
                    await self.event("phone", self.tr("Новое своё устройство: ", "Новий свій пристрій: ")
                                     + f"{self.pair.phones[pid]['name']} ({ip})", notify=True)
                    return pid
                log.warning("Устройство %s: неверный код подключения", ip)
                await self.send(ws, dict(hello, err=res))
        await ws.close()
        return None

    def role_msg(self, ws):
        return dict(self.role_of(ws), t="role")

    async def msg_phoneRole(self, ws, m):
        """Основной дек назначает роль своему устройству (окно «Устройства»)."""
        pid = str(m.get("id") or "")
        r = self.pair.set_role(pid, m.get("role"), self.store.exists)
        if r is None:
            return
        log.info("Роль устройства %s: %s", self.pair.phones[pid].get("name", "?"), r["role"])
        for c in list(self.clients):
            if self.who.get(c) == pid:
                await self.send(c, self.role_msg(c))
            elif c in self.who:
                await self.send(c, {"t": "phones", "paired": self.phones_list(c)})

    def phones_list(self, ws=None):
        online = {w for w in self.who.values() if w != "local"}
        return self.pair.public(online, self.who.get(ws))

    async def icon(self, request):
        self.allowed(request)
        p = request.query.get("p", "")
        res = await apps.icon_png(p) if p else None
        if not res:
            return web.Response(status=404)
        body, ctype = res if isinstance(res, tuple) else (res, "image/png")   # Linux: PNG или SVG из темы
        return web.Response(body=body, content_type=ctype, headers={"Cache-Control": "max-age=86400"})

    async def art(self, request):
        self.allowed(request)
        a = self.media.art if self.media else None
        if not a:
            return web.Response(status=404)
        # адрес содержит хэш картинки: кэшировать можно только «свою» картинку
        cache = "max-age=86400, immutable" if request.query.get("v") == a[2] else "no-store"
        return web.Response(body=a[0], content_type=a[1], headers={"Cache-Control": cache})

    async def plugin_file(self, request):
        """Картинки и шрифты плагина-папки (plugins/<id>/…) — для его блока, экрана и плиток."""
        self.allowed(request)
        f = self.plugins.file(request.match_info["pid"], request.match_info["rel"])
        if f is None:
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Content-Type": plugmod.FILE_TYPES[f.suffix.lower()],
                                            "Cache-Control": "max-age=3600"})

    async def mod_file(self, request):
        """Файлы мода (CSS, JS, картинки, шрифты) — только своим устройствам и только из папки мода."""
        self.allowed(request)
        f = self.mods.file(request.match_info["mid"], request.match_info["rel"])
        if f is None:
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Content-Type": modsmod.FILE_TYPES[f.suffix.lower()], "Cache-Control": "no-cache"})

    async def report_file(self, request):
        """Отчёт для разработчика — скачать на телефон (только своим, только из logs/reports)."""
        self.allowed(request)
        name = request.match_info["name"]
        if not re.fullmatch(r"deck-report-\d{8}-\d{6}\.zip", name):
            raise web.HTTPNotFound()
        f = ROOT / "logs" / "reports" / name
        if not f.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(f, headers={"Content-Disposition": f'attachment; filename="{name}"'})

    async def plugin_stream(self, request):
        """Живая картинка плагина (элемент stream): MJPEG — multipart/x-mixed-replace, понимает любой <img>, и старый
        WebView тоже. Шлём только самый свежий кадр; граница — сразу после кадра (иначе Chrome показывает его, лишь
        когда придёт следующий). Нового кадра нет 2 с — повторяем прежний: так узнаём, что телефон ушёл."""
        self.allowed(request)
        pid = request.match_info["pid"]
        if not self.plugins.alive(pid):
            raise web.HTTPNotFound()
        resp = web.StreamResponse(headers={"Content-Type": "multipart/x-mixed-replace; boundary=deckframe",
                                           "Cache-Control": "no-cache, no-store", "Pragma": "no-cache"})
        await resp.prepare(request)
        ev, last = asyncio.Event(), -1
        self.plugins.watch(pid, ev, True)
        try:
            await resp.write(b"--deckframe\r\n")
            f = self.plugins.frames.get(pid)                 # что уже есть — сразу (неподвижный экран иначе ждал бы 2 с)
            if f:
                last = f[0]
                await resp.write(b"Content-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(f[1]) + f[1] + b"\r\n--deckframe\r\n")
            while self.plugins.alive(pid):
                try:
                    await asyncio.wait_for(ev.wait(), 2.0)
                    fresh = True
                except asyncio.TimeoutError:
                    fresh = False
                ev.clear()
                if request.transport is None or request.transport.is_closing():
                    break
                f = self.plugins.frames.get(pid)
                if not f or (fresh and f[0] == last):
                    continue
                last, data = f
                await resp.write(b"Content-Type: image/jpeg\r\nContent-Length: %d\r\n\r\n" % len(data)
                                 + data + b"\r\n--deckframe\r\n")
        except (ConnectionResetError, ConnectionError, RuntimeError):
            pass
        finally:
            self.plugins.watch(pid, ev, False)
        return resp

    async def apk(self, request):
        if not APK.exists():
            return web.Response(status=404, text="APK нет на ПК")
        return web.FileResponse(APK, headers={
            "Content-Type": "application/vnd.android.package-archive",
            "Content-Disposition": 'attachment; filename="Noryx.apk"'})

    async def index(self, request):
        return web.FileResponse(CLIENT / "index.html", headers={"Cache-Control": "no-cache"})

    async def on_startup(self, app):
        self.loop = asyncio.get_running_loop()
        await self.purge_trash()
        changed = await apps.resolve_layouts(self.store.layouts)
        if self.store.fresh:
            # первый запуск на новом ПК: стандартные кнопки программ, которых тут нет, убираем
            for mode, items in self.store.layouts.items():
                keep = [it for it in items if it.get("kind") != "app" or it.get("path")]
                if len(keep) != len(items):
                    log.info("Режим %s: убрал ненайденные программы (%d)", mode, len(items) - len(keep))
                    self.store.layouts[mode] = keep
                    changed = True
        if changed:
            self.store.save()
        self.media = Media(self.on_media, lambda: self.store.cfg.get("player", "spotify"))
        self.media.watched = lambda: bool(self.clients)
        try:                                        # плагин ушёл из сборки (лампа) — у того, кто им пользовался, остаётся
            await asyncio.get_running_loop().run_in_executor(None, updmod.keep_plugins, ROOT)
        except Exception:
            log.exception("Плагины после обновления")
        try:                                        # плагин в mods/ или мод в plugins/ — по своим папкам
            await asyncio.get_running_loop().run_in_executor(None, modsmod.sort_folders, PLUGINS, MODS)
        except Exception:
            log.exception("Порядок в папках плагинов и модов")
        await self.plugins.scan()                   # плагины (лампа на Arduino — catalog/plugins/lamp)
        await self.mods_scan()                      # моды дека: новый / изменённый — «было / стало» на основном деке
        if IS_WIN:
            self.pad = padmod.Pad(self.store.cfg, self.on_pad)
        else:
            from lx_sound import PulseFactory
            self.pad = padmod.Pad(self.store.cfg, self.on_pad, PulseFactory())
        asyncio.create_task(self.pollers())
        self.health.attach()
        asyncio.create_task(self.health.watch_loop(asyncio))
        asyncio.create_task(comthread.gc_loop())
        self.updater.start()
        if self.updater.can():
            asyncio.create_task(self.catalog.loop())   # каталог плагинов: через минуту после запуска, потом раз в 6 ч


# вид сообщения с телефона → обработчик: методы Deck.msg_<вид> (см. Deck.handle; набор сверяет tests/test_tiles.py)
HANDLERS = {name[4:]: fn for name, fn in vars(Deck).items() if name.startswith("msg_")}


def tray_mod():
    """Модуль трея этой ОС (у Linux ещё и ярлык в меню приложений)."""
    if IS_WIN:
        import tray
    else:
        import lx_tray as tray
    return tray


def selftest():
    """Проверка новой версии перед установкой (update.py вызывает её у скачанной копии): модули загрузились,
    конфиг этого ПК читается и отдаётся телефону, разбор скриптов работает, образец плагина загружается."""
    d = Deck()
    json.dumps(d.config_msg(), ensure_ascii=False)
    json.dumps(d.updater.msg(), ensure_ascii=False)
    steps, errors = scriptmod.parse("ctrl+c\nпауза 10", keys.known)
    assert steps and not errors, errors
    ex = Path(d.plugins.folder) / "_example.py"         # образец плагина из сборки загружается (не запуская)
    if ex.is_file():
        it = plugmod.Loaded("example", ex, ex.parent)
        d.plugins.read(it)
        assert not it.error, it.error


def apk_sha():
    """SHA-256 файла Noryx.apk: приложение сравнивает со своим и предлагает обновиться (пересчёт — если файл
    поменялся)."""
    global _apk_sha
    try:
        st = APK.stat()
    except OSError:
        return ""
    key = (st.st_mtime_ns, st.st_size)
    if _apk_sha[0] != key:
        _apk_sha = (key, hashlib.sha256(APK.read_bytes()).hexdigest())
    return _apk_sha[1]


def device_name(ua):
    """Короткое имя устройства для списка своих: модель телефона или браузер."""
    ua = ua or ""
    x = re.search(r"; ([^;)]+) Build/", ua)
    if x:
        return x.group(1).strip()[:60]
    for pat, name in ((r"Android", "Android"), (r"Windows NT", "Windows"), (r"Mac OS X", "Mac"), (r"Linux", "Linux")):
        if re.search(pat, ua):
            return name
    return (ua[:40] or "?")


def short_ua(ua):
    """Из User-Agent — что важно для поиска поломок: Android, версия WebView/Chrome, версия приложения дека."""
    out = []
    for pat in (r"Android [\d.]+", r"; ([^;)]+) Build/", r"Chrome/[\d.]+", r"ClaudeDeckApp/[\d.]+", r"Windows NT [\d.]+",
                r"Firefox/[\d.]+", r"Version/[\d.]+ Safari"):
        x = re.search(pat, ua or "")
        if x:
            out.append(x.group(1) if x.groups() else x.group(0))
    return ", ".join(out) or (ua or "?")[:120]


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()


def local_ip_for(peer):
    """IP этого ПК в той сети, откуда пришёл запрос (важно, если сетевых карт несколько)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((peer, 1))
        return s.getsockname()[0]
    except Exception:
        return lan_ip()
    finally:
        s.close()


class Discovery(asyncio.DatagramProtocol):
    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        if data.strip() == b"CLAUDE_DECK_DISCOVER":
            url = f"http://{local_ip_for(addr[0])}:{PORT}"
            self.transport.sendto(f"CLAUDE_DECK {url}".encode(), addr)
            log.info("Приложение %s ищет ПК — ответил %s", addr[0], url)


def watchdog(loop):
    """Если цикл событий завис дольше 8 с — пишет стеки всех потоков в лог."""
    import threading
    import time
    import traceback
    beat = {"t": time.monotonic()}

    def tick():
        beat["t"] = time.monotonic()
        loop.call_later(1, tick)
    loop.call_soon_threadsafe(tick)

    def watch():
        reported = False
        while True:
            time.sleep(2)
            lag = time.monotonic() - beat["t"]
            if lag > 8 and not reported:
                reported = True
                frames = sys._current_frames()
                dump = []
                for th in threading.enumerate():
                    f = frames.get(th.ident)
                    if f:
                        dump.append(f"--- поток {th.name}\n" + "".join(traceback.format_stack(f)))
                log.error("Сервер завис на %.0f с. Стеки потоков:\n%s", lag, "\n".join(dump))
            elif lag < 3:
                reported = False
    threading.Thread(target=watch, name="watchdog", daemon=True).start()


def deck_alive(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url + "/", timeout=1.5) as r:
            head = r.read(4096)
            return b"Noryx" in head or b"Claude Deck" in head    # прежнее название — у ещё не обновлённых
    except Exception:
        return False


def port_free(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if not IS_WIN:
        # как и сам aiohttp: соединения прошлого запуска в TIME_WAIT не мешают, мешает только тот, кто слушает порт
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", port))
        return True
    except OSError:
        return False
    finally:
        s.close()


async def serve(restart=False, quiet=False):
    tmod = tray_mod()
    loop = asyncio.get_running_loop()
    stop = asyncio.Event()
    want_restart = {"v": False}
    ip = lan_ip()
    url_local, url_phone = f"http://127.0.0.1:{PORT}", f"http://{ip}:{PORT}"

    # порт занят: если отвечает живой дек — просто открываем его; иначе ждём (перезапуск / TIME_WAIT)
    for _ in range(40):
        if port_free(PORT):
            break
        if not restart and deck_alive(url_local):
            log.info("Дек уже запущен — открываю его в браузере")
            import webbrowser
            webbrowser.open(url_local)
            return None
        await asyncio.sleep(0.5)
    else:
        log.error("Порт %d занят другой программой — сервер не запущен", PORT)
        return None

    deck = Deck()
    deck.stop_event = stop
    app = web.Application()
    app.router.add_get("/", deck.index)
    app.router.add_get("/ws", deck.ws_handler)
    app.router.add_get("/icon", deck.icon)
    app.router.add_get("/art", deck.art)
    app.router.add_get("/apk", deck.apk)
    app.router.add_get("/plugin/{pid}/{rel:.+}", deck.plugin_file)
    app.router.add_get("/mod/{mid}/{rel:.+}", deck.mod_file)
    app.router.add_get("/stream/{pid}", deck.plugin_stream)
    app.router.add_get("/report/{name}", deck.report_file)

    async def quit_handler(request):
        if request.remote not in ("127.0.0.1", "::1"):
            return web.Response(status=403)
        loop.call_soon(stop.set)
        return web.Response(text="Noryx остановлен")
    app.router.add_get("/quit", quit_handler)
    app.router.add_static("/static/", CLIENT, show_index=False)
    app.on_startup.append(deck.on_startup)
    log.info("Noryx %s запускается…%s", version_text(), i18n.tr(" (перезапуск)", " (перезапуск)") if restart else "")
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, host="0.0.0.0", port=PORT)
    await site.start()
    watchdog(loop)
    udp = None
    try:
        udp, _ = await loop.create_datagram_endpoint(Discovery, local_addr=("0.0.0.0", DISCOVERY_PORT))
        log.info("Автопоиск для приложения: UDP %d", DISCOVERY_PORT)
    except OSError as e:
        log.warning("Автопоиск недоступен (UDP %d занят: %s) — в приложении можно ввести адрес вручную", DISCOVERY_PORT, e)
    log.info("Слушаю порт %d", PORT)
    log.info("На этом ПК:   %s", url_local)
    log.info("С телефона:   %s", url_phone)

    def on_quit():
        loop.call_soon_threadsafe(stop.set)

    def on_restart():
        want_restart["v"] = True
        loop.call_soon_threadsafe(stop.set)

    t = tmod.Tray(url_local, url_phone, on_quit, on_restart, greet=not (restart or quiet))
    t.uk = lambda: deck.store.cfg.get("lang") == "uk"
    # пункты «Обновить дек» (проверит и поставит) и «Обновить из файла…»; нажатия приходят из потока трея
    t.upd_label = deck.updater.tray_label
    t.on_update = lambda: asyncio.run_coroutine_threadsafe(deck.updater.tray_click(), loop)
    t.on_update_file = lambda: asyncio.run_coroutine_threadsafe(deck.updater.pick_file(), loop)
    deck.tray = t
    if hasattr(tmod, "menu_entry"):          # ярлык в меню приложений — на языке дека (если он там есть)
        loop.run_in_executor(None, tmod.menu_entry)
    if t.start():
        log.info("Значок в трее готов")
    await stop.wait()
    log.info("Останавливаюсь…")
    for task in list(deck.scripts.values()):    # идущие скрипты — стоп, зажатые клавиши отпускаются
        task.cancel()
    if deck.scripts:
        await asyncio.sleep(0.2)
    try:
        await asyncio.wait_for(deck.plugins.stop_all(), 8)   # плагины отпускают свои устройства (лампа — порт)
    except Exception as e:
        log.warning("Плагины не остановились: %s", e)
    t.stop()
    await asyncio.sleep(0.4)  # даём трею убрать значок, иначе останется «призрак» до наведения мыши
    for ws in list(deck.clients):
        try:
            await ws.close()
        except Exception:
            pass
    if udp:
        udp.close()
    # порт закрывается первым делом; дальше aiohttp ждёт, пока закроются соединения, — под Wine с подключённым
    # телефоном это не кончалось никогда, а помощник обновления и перезапуск ждут выхода. Не wait_for: тот
    # отменяет и ждёт отмены, а она тут не доходит — ждали бы так же вечно
    _, pending = await asyncio.wait({asyncio.ensure_future(runner.cleanup())}, timeout=5)
    if pending:
        log.warning("Соединения не закрылись за 5 с — выхожу, не дожидаясь")
    return deck.stop_action or ("restart" if want_restart["v"] else None)


def main():
    restart = "--restart" in sys.argv
    # не asyncio.run: тот после serve() ждёт все задачи и потоки executor'а (до 5 минут), а выходим мы всё равно
    # через os._exit — всё нужное к этому моменту сохранено
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    action = loop.run_until_complete(serve(restart, quiet="--quiet" in sys.argv))
    if action == "restart":
        tray_mod().spawn_restart()
        log.info("Новая копия запущена")
    elif action == "update":
        log.info("Выхожу: дальше работает помощник обновления (logs/update.log)")
    logging.shutdown()
    os._exit(0)  # не ждём фоновые потоки (медиа, COM) — всё уже сохранено


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.critical("Сервер упал", exc_info=True)
        raise
