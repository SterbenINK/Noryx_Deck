"""Монитор (DDC/CI) — плагин-мод дека (в сборку не входит: папку monitor кладут в plugins/ сами): яркость, контраст, цвета монитора с телефона и пресеты.

Нажал плитку пресета — монитор сам меняет настройки (как кнопками на мониторе, только сразу всё).
Работа с монитором — ddc.py рядом (Windows — dxva2.dll, Linux — ddcutil). DDC медленный: все команды идут
в своём потоке по очереди с паузой между ними; ползунок шлёт только последнее значение; перед записью
читаем текущее (что совпадает — не трогаем), так пресет «горит» правильно, даже если меняли кнопками монитора.

Пресет — данные: {"Ночь": {"<монитор>": {"16": 0}}} — номера кодов VCP в десятичном виде (0x10 = 16 — яркость).
В меню монитора нужен включённый DDC/CI (обычно «Система» → «DDC/CI» → Вкл)."""
import queue
import threading
import time

NAME = {"en": "Monitor (DDC/CI)", "uk": "Монітор (DDC/CI)"}
ABOUT = {"en": "Monitor brightness, contrast and colors from the deck, presets in one tap",
         "uk": "Яскравість, контраст і кольори монітора з дека, пресети однією кнопкою"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
LANGS = ["en", "uk"]                     # на каких языках свои тексты (первый — основной); другие — из языковых пакетов дека
PERMS = ["devices", "native", "launch"]
VERSION = "1.1.1"                        # версия плагина в каталоге (catalog/index.json): подними — деки предложат обновиться
CHANGES = [("1.1.1", "Instructions (README) in English", "Інструкція (README) — англійською й українською"),
           ("1.1.0", "English is now the main language: the plugin speaks English by default, Ukrainian is built in, other languages come from the deck's language packs",
            "Основна мова тепер англійська: плагін англійською за замовчуванням, українська вбудована, інші мови — з мовних пакетів деку"),
           ("1.0.1", "Instructions — in the plugin README", "Інструкція — у README плагіна"),
           ("1.0.0", "First version in the catalog: brightness, contrast, colors, presets",
            "Перша версія в каталозі: яскравість, контраст, кольори, пресети")]

GAP = 0.12                          # с между командами (Samsung иначе пропускает вторую)
POLL = 30                           # с — перечитать яркость / контраст (вдруг меняли кнопками монитора)
# что показываем, если монитор отвечает: код VCP → (англ., укр.)
CODES = {0x10: ("Brightness", "Яскравість"), 0x12: ("Contrast", "Контраст"), 0x16: ("Red", "Червоний"),
         0x18: ("Green", "Зелений"), 0x1A: ("Blue", "Синій"), 0x62: ("Volume", "Гучність")}
COLOR = {0x10: "#FFC845", 0x12: "#B8C2D6", 0x16: "#FF4A4A", 0x18: "#3DDC6A", 0x1A: "#4A8CFF"}  # цвет ползунка
QUICK = (0x10, 0x12)                # в доке и при опросе


class Plugin:
    def __init__(self, api, make_backend=None):
        self.api = api
        self.make = make_backend
        c = api.cfg
        c.setdefault("presets", {})
        c.setdefault("order", [])
        self.mons = []                  # [{"key", "name", "vals": {code: [cur, max]}}]
        self.err = ""
        self.busy = ""
        self.q = queue.Queue()
        self.pending = {}               # (key, code) → значение (ползунок: только последнее)
        self.lock = threading.Lock()
        self.run = False
        self.dev = None

    # ---------- жизнь ----------
    def start(self):
        self.run = True
        threading.Thread(target=self.worker, name="ddc", daemon=True).start()
        self.q.put(("probe",))

    def stop(self):
        self.run = False
        self.q.put(("quit",))

    def worker(self):
        last_poll = time.monotonic()
        while self.run:
            try:
                job = self.q.get(timeout=1)
            except queue.Empty:
                job = None
            try:
                self.flush()
                if job is None:
                    if self.mons and time.monotonic() - last_poll > POLL:
                        last_poll = time.monotonic()
                        self.read(QUICK)
                    continue
                if job[0] == "quit":
                    break
                if job[0] == "probe":
                    self.probe()
                elif job[0] == "apply":
                    self.apply(job[1])
                elif job[0] == "refresh":
                    self.read(CODES)
            except Exception as e:      # монитор отвалился и т. п. — показать, не падать
                self.err = f"{type(e).__name__}: {e}"
                self.api.log.warning("ddc: %s", self.err)
                self.push()
        if self.dev:
            self.dev.close()

    def push(self):
        self.api.call(self.api.update)

    # ---------- работа с монитором (только в потоке ddc) ----------
    def probe(self):
        self.busy = self.api.tr("Looking for monitors…", "Шукаю монітори…")
        self.push()
        if self.dev is None:
            if self.make:
                self.dev = self.make()
            else:
                import ddc
                self.dev = ddc.backend()
        found = self.dev.monitors()
        mons = []
        for m in found:
            vals = {}
            for code in CODES:
                v = self.dev.get(m["key"], code)
                if v is not None:
                    vals[code] = list(v)
                time.sleep(GAP / 2)
            if vals:                                 # не отвечает ни на что — DDC/CI выключен
                mons.append({"key": m["key"], "name": m["name"], "vals": vals})
        self.mons, self.busy = mons, ""
        self.err = "" if mons else self.api.tr("Monitors didn't answer over DDC/CI — turn DDC/CI on in the monitor menu",
                                               "Монітори не відповіли по DDC/CI — увімкни DDC/CI в меню монітора")
        self.seed()
        self.push()

    def read(self, codes):
        for m in self.mons:
            for code in codes:
                if code in m["vals"]:
                    v = self.dev.get(m["key"], code)
                    if v is not None:
                        m["vals"][code] = list(v)
                    time.sleep(GAP / 2)
        self.push()

    def write(self, mon, code, value):
        mx = mon["vals"].get(code, [0, 100])[1] or 100
        value = max(0, min(mx, int(round(value))))
        if self.dev.set(mon["key"], code, value):
            mon["vals"][code] = [value, mx]
        time.sleep(GAP)

    def flush(self):
        """Ползунки: записать последние значения."""
        with self.lock:
            todo, self.pending = self.pending, {}
        for (key, code), v in todo.items():
            m = self.mon(key)
            if m:
                self.write(m, code, v)
        if todo:
            self.push()

    def apply(self, name):
        p = self.api.cfg["presets"].get(name)
        if not p:
            return
        self.busy = name
        self.push()
        for m in self.mons:
            want = p.get(m["key"]) or p.get("*") or {}
            for code, v in want.items():
                code = int(code)
                cur = self.dev.get(m["key"], code)              # читаем перед записью: совпадает — не трогаем
                time.sleep(GAP / 2)
                if cur is None:
                    continue
                m["vals"][code] = list(cur)
                if cur[0] != int(v):
                    self.write(m, code, v)
        self.busy = ""
        self.api.event(self.api.tr("Monitor: preset «{0}»", "Монітор: пресет «{0}»", name))
        self.push()

    # ---------- пресеты ----------
    def seed(self):
        """Первый запуск: «Сток» — как настроено сейчас (вернуться к своему), «Ночь» — яркость 0."""
        c = self.api.cfg
        if c["presets"] or not self.mons:
            return
        stock, night = self.api.tr("Stock", "Сток"), self.api.tr("Night", "Ніч")      # имена — на языке дека при первом запуске
        c["presets"][stock] = {m["key"]: {str(k): v[0] for k, v in m["vals"].items() if k != 0x62} for m in self.mons}
        c["presets"][night] = {"*": {"16": 0}}
        c["order"] = [stock, night]
        self.api.call(self.api.save)

    def active(self, name):
        p = self.api.cfg["presets"].get(name) or {}
        if not self.mons or not p:
            return False
        for m in self.mons:
            want = p.get(m["key"]) or p.get("*") or {}
            for code, v in want.items():
                cur = m["vals"].get(int(code))
                if cur is None or cur[0] != int(v):
                    return False
        return True

    def names(self):
        c = self.api.cfg
        return [n for n in c["order"] if n in c["presets"]] + [n for n in c["presets"] if n not in c["order"]]

    def mon(self, key):
        return next((m for m in self.mons if m["key"] == key), None)

    # ---------- телефон ----------
    def action(self, aid, value):
        c = self.api.cfg
        if aid == "refresh":
            self.q.put(("probe",))
        elif aid.startswith("p:"):
            if aid[2:] in c["presets"]:
                self.q.put(("apply", aid[2:]))
        elif aid.startswith("set:"):                   # set:<номер монитора>:<код>
            _, i, code = aid.split(":")
            m = self.mons[int(i)] if int(i) < len(self.mons) else None
            if m and isinstance(value, (int, float)):
                with self.lock:
                    self.pending[(m["key"], int(code))] = value
                m["vals"].setdefault(int(code), [0, 100])[0] = int(value)
                self.q.put(("tick",))
        elif aid == "save":                            # «Сохранить текущее как…»
            name = str(value or "").strip()[:30]
            if not name or not self.mons:
                return
            c["presets"][name] = {m["key"]: {str(k): v[0] for k, v in m["vals"].items() if k != 0x62} for m in self.mons}
            if name not in c["order"]:
                c["order"].append(name)
            self.api.save()
            self.api.toast(self.api.tr("Preset «{0}» saved", "Пресет «{0}» збережено", name))
        elif aid.startswith("del:"):
            name = aid[4:]
            c["presets"].pop(name, None)
            c["order"] = [n for n in c["order"] if n != name]
            self.api.save()

    def ui(self):
        tr = self.api.tr
        names = self.names()
        presets = {"type": "row", "items": [
            {"type": "button", "id": "p:" + n, "label": n, "on": self.active(n), "disabled": bool(self.busy)} for n in names]}
        status = []
        if self.busy:
            status.append({"type": "label", "label": self.busy})
        if self.err:
            status.append({"type": "text", "label": tr("Monitor", "Монітор"), "value": self.err})

        def slider(i, m, code):
            cur, mx = m["vals"][code]
            ru, uk = CODES[code]
            return {"type": "slider", "id": f"set:{i}:{code}", "label": tr(ru, uk), "value": cur, "min": 0, "max": mx or 100,
                    "unit": "" if mx and mx != 100 else "%", **({"color": COLOR[code]} if code in COLOR else {})}

        dock = list(status)
        for i, m in enumerate(self.mons):
            if len(self.mons) > 1:
                dock.append({"type": "label", "label": m["name"]})
            if 0x10 in m["vals"]:
                dock.append(slider(i, m, 0x10))
        if names:
            dock.append(presets)

        cards = []
        for i, m in enumerate(self.mons):
            items = [slider(i, m, c) for c in CODES if c in m["vals"]]
            cards.append({"type": "card", "title": m["name"], "icon": "monitor", "items": items})
        screen = list(status) + [
            {"type": "grid", "cols": max(1, min(3, len(cards))), "items": cards} if cards else
            {"type": "text", "label": tr("No monitors", "Моніторів немає"), "value": ""},
            {"type": "label", "label": tr("Presets", "Пресети")}, presets,
            {"type": "input", "id": "save", "placeholder": tr("Preset name", "Назва пресета"),
             "button": tr("Save current", "Зберегти поточне")},
            {"type": "button", "id": "refresh", "label": tr("Find monitors again", "Знайти монітори знову"), "style": "dashed"}]
        settings = list(status) + [
            {"type": "text", "label": tr("Monitors", "Монітори"), "value": ", ".join(m["name"] for m in self.mons) or "—"},
            {"type": "input", "id": "save", "placeholder": tr("Preset name", "Назва пресета"),
             "button": tr("Save current", "Зберегти поточне")}] + [
            {"type": "row", "items": [{"type": "text", "label": n, "value": ""},
                                      {"type": "button", "id": "del:" + n, "label": tr("Delete", "Видалити")}]} for n in names] + [
            {"type": "button", "id": "refresh", "label": tr("Find monitors again", "Знайти монітори знову")}]
        tiles = [{"id": "screen", "label": tr("Monitor", "Монітор"), "icon": "monitor", "screen": True,
                  "sub": self.mons[0]["name"] if self.mons else tr("no connection", "немає зв'язку")}]
        tiles += [{"id": "p:" + n, "label": n, "icon": "monitor", "on": self.active(n), "sub": tr("monitor preset", "пресет монітора")}
                  for n in names]
        return {"dock": dock, "screen": screen, "settings": settings, "tiles": tiles}
