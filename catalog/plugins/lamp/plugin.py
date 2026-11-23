"""Лампа на Arduino (скетч ClaudeLamp/ClaudeLamp.ino в этой папке) — плагин дека (catalog/plugins/lamp, ставится из каталога).

Всё про Arduino — только здесь: выключен плагин — ни его кода, ни pyserial дек не загружает (pyserial ставится
сам при первом включении). Работу с платой делает lamp.py рядом (поиск порта, отпускание порта для прошивки, переподключение); здесь —
то, что видно на телефоне: ползунок яркости с кнопкой в доке, «Проверить Arduino» в настройках, шаг сценария
«лампа 60» (действие level), события «Лампа не отвечает / снова на связи». Выключил плагин — порт отпущен,
блок в доке и настройки лампы пропадают."""
NAME = {"en": "Lamp (Arduino)", "uk": "Лампа (Arduino)"}
ABOUT = {"en": "Arduino lamp brightness: a slider in the dock, scenario step «lamp 60»",
         "uk": "Яскравість лампи на Arduino: повзунок у доці, крок сценарію «лампа 60»"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
LANGS = ["en", "uk"]                     # на каких языках свои тексты (первый — основной); другие — из языковых пакетов дека
PERMS = ["devices", "launch"]
VERSION = "1.1.1"                        # версия плагина в каталоге (catalog/index.json): подними — деки предложат обновиться
CHANGES = [("1.1.1", "Instructions (README) in English", "Інструкція (README) — англійською й українською"),
           ("1.1.0", "English is now the main language: the plugin speaks English by default, Ukrainian is built in, other languages come from the deck's language packs",
            "Основна мова тепер англійська: плагін англійською за замовчуванням, українська вбудована, інші мови — з мовних пакетів деку"),
           ("1.0.1", "Instructions — in the plugin README", "Інструкція — у README плагіна"),
           ("1.0.0", "First version in the catalog (used to ship with the deck)",
            "Перша версія в каталозі (раніше йшла в збірці дека)")]
CHECK_WAIT = 20.0          # «Проверить»: столько ждать ответа платы


class Plugin:
    def __init__(self, api):
        self.api = api
        self.lamp = None
        self.state = {"status": "search", "port": None, "level": 600, "on": False}
        self.checking = None       # «Проверить» нажали — ждём, найдётся ли плата
        self.seen = None           # была ли лампа на связи (для событий «не отвечает / снова на связи»)
        c = api.cfg
        if "found" not in c:       # переезд с прежних настроек дека (до плагинов)
            c["found"] = bool(api.deck_cfg.get("arduino"))
            c["port"] = api.deck_cfg.get("lampPort", "")
            api.save()

    def start(self):
        from lamp import Lamp              # только при включении: модуль лампы и pyserial не грузятся зря
        self.lamp = Lamp(self.api.cfg.get("port", ""), True, self.on_state, self.on_port)

    def stop(self):
        if self.lamp:
            self.lamp.enable(False)            # порт отпускаем, поток засыпает
            self.lamp = None

    # ---------- из потока лампы ----------
    def on_state(self, st):
        self.api.call(lambda: self.changed(st))

    def on_port(self, dev):
        def remember():
            self.api.cfg["port"] = dev
            self.api.save()
        self.api.call(remember)

    # ---------- в потоке дека ----------
    def changed(self, st):
        self.state = st
        s = st.get("status")
        tr = self.api.tr
        if s == "ok" and not self.api.cfg.get("found"):
            self.api.cfg["found"] = True               # нашлась сама — блок в доке появится
            self.api.save()
        if self.checking and s in ("ok", "none", "busy", "perm", "nolib"):
            self.found(s == "ok", st.get("port"))
        ok = s == "ok"
        if self.seen and not ok and s != "off":
            self.api.event(tr("The lamp isn't responding", "Лампа не відповідає"), level="warn", notify=True)
        elif self.seen is False and ok:
            self.api.event(tr("The lamp is back online", "Лампа знову на зв’язку"), notify=True)
        if s != "off":
            self.seen = ok
        self.api.update()

    def found(self, ok, port):
        self.checking = None
        self.api.cfg["found"] = ok
        self.api.save()
        tr = self.api.tr
        self.api.toast(tr("Arduino found: ", "Arduino знайдено: ") + str(port) if ok
                       else tr("Arduino not found", "Arduino не знайдено"))
        self.api.update()

    def check(self):
        if not self.lamp:
            return
        self.checking = started = object()
        self.api.toast(self.api.tr("Looking for the Arduino…", "Шукаю Arduino…"))
        self.lamp.scan()
        self.api.later(CHECK_WAIT, lambda: self.checking is started and self.found(False, None))

    # ---------- телефон ----------
    def pct(self):
        lv = self.state.get("level") or 0
        return max(1, round(lv / 10)) if lv > 0 else 0

    def ui(self):
        tr, st, s = self.api.tr, self.state, self.state.get("status")
        settings = [{"type": "row", "items": [
            {"type": "text", "label": "Arduino", "value": (tr("Found", "Знайдено") + (f" · {st.get('port')}" if st.get("port") else ""))
             if s == "ok" else tr("Searching…", "Шукаю…") if self.checking or s == "search"
             else tr("Not found — no lamp", "Не знайдено — лампи немає")},
            {"type": "button", "id": "check", "label": tr("Check", "Перевірити"), "disabled": bool(self.checking)}]}]
        if not self.api.cfg.get("found"):
            return {"settings": settings}                  # платы ещё не было — в доке пусто
        if s != "ok":
            txt = {"search": tr("Looking for the lamp…", "Шукаю лампу…"),
                   "busy": f"{st.get('port') or tr('Port', 'Порт')} " + tr("busy · try again", "зайнятий · ще раз"),
                   "perm": f"{st.get('port') or tr('Port', 'Порт')}: " + tr("no access", "немає доступу") + " (dialout)",
                   "nolib": tr("No pyserial · try again", "Немає pyserial · ще раз")}.get(s, tr("No lamp · search", "Немає лампи · шукати"))
            dock = [{"type": "button", "id": "scan", "label": txt, "icon": "bulb", "style": "dashed", "wide": True,
                     "disabled": s == "search"}]
        else:
            on = bool(st.get("on"))
            dock = [{"type": "row", "items": [
                {"type": "button", "id": "toggle", "icon": "bulb", "on": on, "style": "lamp",
                 "label": tr("Turn the lamp off", "Вимкнути лампу") if on else tr("Turn the lamp on", "Увімкнути лампу")},
                {"type": "slider", "id": "level", "label": tr("Lamp", "Лампа"), "value": self.pct() if on else 0,
                 "min": 0, "max": 100, "unit": "%", "off": not on, "hint": tr("off", "вимк")}]}]
        return {"dock": dock, "settings": settings}

    def action(self, aid, value):
        if not self.lamp:
            return
        if aid == "toggle":
            self.lamp.toggle()
        elif aid == "level":                       # 0–100 % (ползунок, шаг сценария «лампа 60»)
            self.lamp.set(float(value or 0) * 10)
        elif aid == "scan":
            self.lamp.scan()
        elif aid == "check":
            self.check()
