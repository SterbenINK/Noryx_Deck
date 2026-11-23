"""Vintage Story на деке: свой режим «Vintage Story» и блок в доке.

Данные шлёт мод игры Noryx Link (клиентский) — JSON по UDP на 127.0.0.1:47811 раз в секунду; команды
плагин шлёт ему на 127.0.0.1:47812. Без мода — «нет связи с игрой».

Режим: слева четыре кнопки-стата (здоровье, сытость, температура, хоткеи), справа — меню выбранной.
  * Здоровье — меню мода S&R System (если стоит, 0.5.0+): кукла из шести зон, травмы выбранной зоны и лечение
    (лучшим предметом или выбранным); зона не выбрана (повторный тап, тап мимо кнопок) — общее состояние, как в игре.
    С S&R 0.6.2+: кого лечить — себя или игрока в 5 блоках (K в игре на игроке — сам переключит на него),
    полоса лечения (своего и того, кто лечит тебя). Без S&R — только здоровье.
  * Сытость — общая шкала и по видам еды.
  * Температура — тело, воздух, погода, время и дата, позиция, темпоральный шторм и стабильность.
  * Хоткеи — свои кнопки: скрипт дека (клавиши — в окно, которое на ПК активно, обычно игра) или команды игры
    через мод (/команда, .команда клиента, «слот 3» — слот хотбара); правка — кнопкой «Изменить».
"""
import json
import os
import socket
import threading
import time

NAME = {"en": "Vintage Story", "uk": "Vintage Story"}
ABOUT = {"en": "Game mode: health and treatment (S&R System), nutrition, world, hotkeys. Needs the Noryx"
               " Link game mod",
         "uk": "Режим гри: здоров'я й лікування (S&R System), харчування, світ, хоткеї. Потрібен мод гри Noryx Link"}
STATE_PORT, CMD_PORT = 47811, 47812
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
LANGS = ["en", "uk"]                     # на каких языках свои тексты (первый — основной); другие — из языковых пакетов дека
PERMS = ["network", "input", "files"]     # files — читаем ключ мода игры из его ModConfig
VERSION = "1.4.1"                        # версия плагина в каталоге (catalog/index.json): подними — деки предложат обновиться
CHANGES = [("1.4.1", "Instructions (README) in English", "Інструкція (README) — англійською й українською"),
           ("1.4.0", "English is now the main language: the plugin speaks English by default, Ukrainian is built in, other languages come from the deck's language packs",
            "Основна мова тепер англійська: плагін англійською за замовчуванням, українська вбудована, інші мови — з мовних пакетів деку"),
           ("1.3.0", "Commands to the game — with the Noryx Link 0.3.0 mod key (without it the mod won't run "
                     "them); needs the «Files» permission",
             "Команди грі — з ключем мода Noryx Link 0.3.0 (без нього мод їх не виконає); потрібен дозвіл «Файли»"),
           ("1.2.0", "Full-height doll with only the mod's marks; injury icons as in the game; bigger injury "
                     "name, bolder font; leaving the game resets the values",
             "Лялька на всю висоту, на ній лише значки мода; іконки травм як у грі; назва травми більша, шрифт товщий; вийшли з гри — значення скидаються"),
           ("1.1.0", "Health like the S&R 0.7 tab: zone marks on the doll, overall condition, injury card, "
                     "main and optional treatment with tips (needs S&R 0.7.18+)",
             "Здоров'я як у вкладці S&R 0.7: позначки зон на ляльці, загальний стан, картка травми, основне й додаткове лікування з підказками (потрібен S&R 0.7.18+)"),
           ("1.0.0", "First version in the catalog: health (S&R), satiety, world, hotkeys",
            "Перша версія в каталозі: здоров'я (S&R), ситість, світ, хоткеї")]
LIVE = 5.0                                  # столько секунд без пакета — «нет связи с игрой»

# цвета в духе интерфейса игры: тёмное дерево, пергаментный текст
THEME = {"bg": "#1E1812", "panel": "#2B221A", "border": "#4A3A2A", "text": "#E8D9B5", "muted": "#A8977A",
         "accent": "#D9B26A", "radius": 6, "font": "serif", "weight": 600}      # шрифт потолще — как в игре
INJURY_ICONS = {"wound", "bruise", "fracture", "burn", "frostbite", "concussion", "dislocation"}   # icons/<тип>.png — рисунки S&R
FOOD = [("fruit", "Fruit", "Фрукти", "#C9563C"), ("veg", "Vegetables", "Овочі", "#5E9A3A"),
        ("grain", "Grain", "Зерно", "#D2AE52"), ("protein", "Protein", "Білки", "#9C5B3B"),
        ("dairy", "Dairy", "Молочне", "#DCD6C4")]
SEASONS = {"spring": ("Spring", "Весна"), "summer": ("Summer", "Літо"), "fall": ("Autumn", "Осінь"), "winter": ("Winter", "Зима")}
PRECIP = {"none": ("clear", "ясно"), "rain": ("rain", "дощ"), "snow": ("snow", "сніг"), "hail": ("hail", "град")}

# кукла S&R System — как в игре (GuiElementBodyDoll): 150×290, правая рука персонажа — слева на экране
DOLL = {0: (55, 10, 40, 40), 1: (50, 54, 50, 88), 3: (28, 54, 19, 84), 2: (103, 54, 19, 84), 5: (51, 145, 23, 124), 4: (76, 145, 23, 124)}
DOLL_ORDER = [1, 0, 5, 4, 3, 2]             # торс, голова, ноги, руки — как рисует игра
HEALTHY, OCHRE, RUST, BLOOD = (0.62, 0.52, 0.38), (0.78, 0.58, 0.24), (0.62, 0.29, 0.14), (0.45, 0.09, 0.06)


def _lerp(a, b, t):
    return tuple(x + (y - x) * t for x, y in zip(a, b))


def zone_color(sev):
    """Цвет зоны по тяжести 0..1 — как ZoneColor в моде: кожа → охра → ржавчина → кровь."""
    if sev <= 0:
        c = HEALTHY
    else:
        sev = min(1.0, sev)
        tint = _lerp(OCHRE, RUST, sev / 0.5) if sev < 0.5 else _lerp(RUST, BLOOD, (sev - 0.5) / 0.5)
        c = _lerp(HEALTHY, tint, 0.55 + 0.45 * sev)
    return "#" + "".join(f"{round(max(0, min(1, x)) * 255):02X}" for x in c)


class Plugin:
    def __init__(self, api):
        self.api = api
        self.data, self.seen = {}, 0.0
        self.sock, self.run = None, False
        self.tab = api.cfg.get("tab") or "health"
        self.zone = None                     # выбранная зона куклы (None — общее состояние)
        self.target = 0                      # кого лечим: 0 — себя, иначе id игрока (S&R 0.6.2+)
        self.auto = False                    # цель выбрал K в игре (закрыли окно — назад к себе)
        self.examined = 0                    # кого в игре последним смотрели по K
        self.got_at = 0.0                    # когда пришёл пакет (полоса лечения идёт сама между пакетами)
        self.ticking = False                 # обновлять полосу 4 раза в секунду, пока идёт лечение
        self.target_sent = 0.0
        self.open = set()                    # раскрытые списки предметов: "зона|травма|метод"; подсказки «i»: "tip|…"
        self.inj = {}                        # S&R 0.7.18+: какая травма зоны выбрана (зона → тип), как в игре
        self.cmd_id = 0
        self.seen_result = None              # номер последнего ответа S&R (показан тостом)
        self.seen_ack = 0
        self.edit = False                    # хоткеи: правка
        self.pick = 0                        # хоткеи: какую кнопку правим
        self.key = None                      # ключ команд из ModConfig игры (None — ещё не читали)

    # ---------- жизнь ----------
    def start(self):
        self.run = True
        threading.Thread(target=self.listen, name="vintage", daemon=True).start()
        self.tick()

    def stop(self):
        self.run = False
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass

    def listen(self):
        # перезагрузка плагина: прежний экземпляр отпускает порт не сразу (его recvfrom ждёт до 1 с) — пробуем ещё
        for attempt in range(20):
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                self.sock.bind(("127.0.0.1", STATE_PORT))
                self.sock.settimeout(1.0)
                break
            except OSError as e:
                self.sock.close()
                if not self.run:
                    return
                if attempt == 19:
                    self.api.log.warning("vintage: порт %s занят: %s", STATE_PORT, e)
                    return
                time.sleep(0.25)
        while self.run:
            try:
                raw, _ = self.sock.recvfrom(65536)
                d = json.loads(raw.decode("utf-8"))
            except socket.timeout:
                continue
            except (OSError, ValueError):
                if not self.run:
                    return
                continue
            if isinstance(d, dict):
                self.api.call(lambda d=d: self.got(d))

    def got(self, d):
        first = not self.live()
        self.data, self.seen = d, time.monotonic()
        r = (d.get("snrs") or {}).get("result")
        if r and r.get("id") != self.seen_result:
            if self.seen_result is not None and r.get("text"):     # первый пакет — старый ответ, не показываем
                self.api.toast(r["text"])
            self.seen_result = r.get("id")
        a = d.get("ack")
        if a and a.get("id") and a.get("id") != self.seen_ack:
            self.seen_ack = a["id"]
            if not a.get("ok") and a.get("text") == "key":
                self.key = None                  # перечитаем: ключ могли пересоздать (удалили файл, новая игра)
                self.api.toast(self.api.tr("The game mod didn't accept the key — enter the world again or restart the game",
                                           "Мод гри не прийняв ключ — зайди у світ ще раз або перезапусти гру"))
            elif not a.get("ok") and a.get("text"):
                self.api.toast(self.api.tr("Game: ", "Гра: ") + str(a["text"]))
        if first:
            self.api.log.info("vintage: игра на связи")
        self.got_at = time.monotonic()
        self.follow(d.get("snrs") if isinstance(d.get("snrs"), dict) else {})
        self.api.update()
        if self.progress() and not self.ticking:
            self.ticking = True
            self.api.later(0.25, self.smooth)

    def follow(self, snr):
        """Цель лечения: K в игре на игроке — к нему; окно закрыли / игрок ушёл — к себе; мост забыл — напомнить."""
        ex = snr.get("examined") or 0
        if ex and ex != self.examined:
            self.set_target(ex, auto=True)
            self.tab = "health"
        elif not ex and self.examined and self.auto:
            self.set_target(0)
        self.examined = ex
        t = snr.get("target") or {}
        if self.target and t.get("lost"):
            self.set_target(0)
        elif self.target and t.get("self") and time.monotonic() - self.target_sent > 2:
            self.send(cmd="target", target=self.target)          # мост перезапустили — он о цели не знает
            self.target_sent = time.monotonic()

    def set_target(self, tid, auto=False):
        if tid != self.target:
            self.zone = None
            self.open.clear()
        self.target, self.auto = tid, auto and bool(tid)
        self.target_sent = time.monotonic()
        self.send(cmd="target", target=tid)

    def progress(self):
        p = ((self.data.get("snrs") or {}).get("progress") or {}) if isinstance(self.data.get("snrs"), dict) else {}
        return [x for x in (p.get("mine"), p.get("onMe")) if isinstance(x, dict)]

    def smooth(self):
        """Полоса лечения между пакетами (мост шлёт их 4 раза в секунду, но телефону нужно плавнее)."""
        if not self.run or not self.progress() or not self.live():
            self.ticking = False
            return
        self.api.update()
        self.api.later(0.25, self.smooth)

    def tick(self):                          # «нет связи» появляется само, когда игру закрыли
        if self.run and self.data and not self.live():
            self.reset()                     # вышли из мира / игры — прежние цифры не показываем
        if self.run:
            self.api.update()
            self.api.later(3, self.tick)

    def reset(self):
        """Связь с игрой пропала: забыть её данные и выбор — на деке «нет связи», а не значения прошлого сеанса."""
        self.data, self.zone, self.target, self.auto, self.examined = {}, None, 0, False, 0
        self.inj, self.ticking = {}, False
        self.open = {k for k in self.open if not k.startswith("tip|")}

    def live(self):
        return time.monotonic() - self.seen < LIVE

    # ---------- ключ команд (Noryx Link 0.3.0+) ----------
    def key_files(self):
        """Где игра держит ModConfig/claudedecklink.json: своя папка из настроек, обычная, Flatpak, Windows, macOS."""
        home = os.path.expanduser("~")
        dirs = [self.api.cfg.get("dataDir"), os.environ.get("VINTAGE_STORY_DATA"),
                os.path.join(os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config"), "VintagestoryData"),
                os.path.join(home, ".var", "app", "at.vintagestory.VintageStory", "config", "VintagestoryData"),
                os.path.join(os.environ.get("APPDATA") or "", "VintagestoryData") if os.environ.get("APPDATA") else None,
                os.path.join(home, "Library", "Application Support", "VintagestoryData")]
        return [os.path.join(d, "ModConfig", "claudedecklink.json") for d in dirs if d]

    def load_key(self):
        """Ключ из файла мода игры (тот пишет его сам при первом запуске). Нет файла — пусто: мод 0.2.0 ключа не ждёт."""
        if self.key is None:
            self.key = ""
            for f in self.key_files():
                try:
                    with open(f, encoding="utf-8-sig") as fh:
                        k = str((json.load(fh) or {}).get("Key") or "").strip()
                except (OSError, ValueError, AttributeError):
                    continue
                if k:
                    self.key = k
                    break
        return self.key

    def send(self, **cmd):
        self.cmd_id += 1
        cmd["id"] = self.cmd_id
        key = self.load_key()
        if key:
            cmd["key"] = key
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.sendto(json.dumps(cmd, ensure_ascii=False).encode("utf-8"), ("127.0.0.1", CMD_PORT))
        except OSError as e:
            self.api.toast(self.api.tr("The game mod isn't responding: ", "Мод гри не відповідає: ") + str(e))

    # ---------- нажатия ----------
    def action(self, aid, value):
        tr = self.api.tr
        if aid.startswith("tab:"):
            self.tab = aid[4:]
            self.api.cfg["tab"] = self.tab
            self.api.save()
        elif aid == "zone:none":
            self.zone = None                                       # тап мимо кнопок — общее состояние
        elif aid.startswith("zone:"):
            z = int(aid[5:])
            self.zone = None if z == self.zone else z              # повторный тап — общее состояние
        elif aid.startswith("who:"):
            self.set_target(int(aid[4:]))
        elif aid.startswith("inj|"):                               # несколько травм на зоне — какую показать
            _, z, typ = aid.split("|", 2)
            self.inj[int(z)] = typ
        elif aid.startswith("exp|"):
            k = aid[4:]
            self.open.symmetric_difference_update({k})
        elif aid.startswith("treat|") or aid.startswith("item|"):
            if not self.live():
                self.api.toast(tr("No connection to the game", "Немає зв'язку з грою"))
                return
            parts = aid.split("|", 4)
            zone, typ, treat = int(parts[1]), parts[2], parts[3]
            item = parts[4] if len(parts) > 4 else None
            if not treat:                                          # основное лечение, а предметов нет — кнопка выключена
                return
            self.send(cmd="treat", zone=zone, type=typ, treatment=treat, item=item, target=self.target)
        elif aid.startswith("hk:"):
            self.hotkey(int(aid[3:]))
        elif aid == "hkEdit":
            self.edit = not self.edit
        elif aid.startswith("hkPick:"):
            self.pick = int(aid[7:])
        elif aid == "hkNew":
            keys = self.keys()
            keys.append({"name": tr("New", "Нова"), "kind": "game", "text": ""})
            self.pick, self.edit = len(keys) - 1, True
            self.save_keys(keys)
        elif aid == "hkDel":
            keys = self.keys()
            if 0 <= self.pick < len(keys):
                keys.pop(self.pick)
                self.pick = max(0, self.pick - 1)
                self.save_keys(keys)
        elif aid in ("hkName", "hkKind", "hkText"):
            keys = self.keys()
            if 0 <= self.pick < len(keys):
                field = {"hkName": "name", "hkKind": "kind", "hkText": "text"}[aid]
                v = str(value or "")
                if field == "name":
                    v = v.strip()[:30] or keys[self.pick]["name"]
                if field == "kind" and v not in ("keys", "game"):
                    return
                keys[self.pick][field] = v[:2000]
                self.save_keys(keys)
                if field == "text":
                    self.api.toast(tr("Saved", "Збережено"))

    # ---------- хоткеи ----------
    def keys(self):
        k = self.api.cfg.get("keys")
        return [dict(x) for x in k if isinstance(x, dict)] if isinstance(k, list) else []

    def save_keys(self, keys):
        self.api.cfg["keys"] = keys[:24]
        self.api.save()

    def hotkey(self, i):
        tr = self.api.tr
        keys = self.keys()
        if not 0 <= i < len(keys):
            return
        k = keys[i]
        if not k.get("text", "").strip():
            self.api.toast(tr("Empty button — press «Edit»", "Порожня кнопка — натисни «Змінити»"))
            return
        if k.get("kind") == "keys":                         # скрипт дека: клавиши в активное окно
            self.api.script(k["text"], label=k.get("name") or "Vintage Story", key=f"vs{i}")
            return
        if not self.live():
            self.api.toast(tr("No connection to the game", "Немає зв'язку з грою"))
            return
        for line in k["text"].splitlines():                # команды игры через мод — по строке
            line = line.strip()
            if not line or line.startswith(";"):
                continue
            low = line.lower()
            for word in ("слот ", "slot "):
                if low.startswith(word) and low[len(word):].strip().isdigit():
                    self.send(cmd="hotbar", slot=int(low[len(word):].strip()))
                    break
            else:
                self.send(cmd="chat", text=line)

    # ---------- вид ----------
    def ui(self):
        tr, live = self.api.tr, self.live()
        note = [] if live else [{"type": "label", "label": tr("no connection to the game", "немає зв'язку з грою"), "color": "#A8977A"}]
        return {
            "theme": THEME,
            "mode": {"name": "Vintage Story", "icon": "gamepad", "color": "#D9B26A",
                     "sub": {"en": "Health, nutrition, world, hotkeys" if live else "No connection to the game",
                             "uk": "Здоров'я, харчування, світ, хоткеї" if live else "Немає зв'язку з грою"}},
            "dock": [{"type": "card", "go": "mode", "title": "Vintage Story", "icon": "🌍", "items": note + [
                self.hp_bar(), self.sat_bar(), {"type": "text", "label": self.season(), "value": self.when()}]}],
            "screen": [{"type": "grid", "widths": [1, 3.2], "fill": True, "items": [
                {"type": "grid", "cols": 1, "items": self.stats()},
                {"type": "card", "scroll": self.tab != "health", "items": note + self.menu()}]}],
            "settings": [{"type": "text", "label": tr("Game mod", "Мод гри"),
                          "value": tr("online", "на зв'язку") if live else tr("no connection", "немає зв'язку")},
                         {"type": "label", "label": tr("Needs the Noryx Link mod in the game. Data — UDP {0}, commands — {1} (127.0.0.1)", "Потрібен мод Noryx Link у грі. Дані — UDP {0}, команди — {1} (127.0.0.1)", STATE_PORT, CMD_PORT)}],
        }

    def num(self, k, default=0.0):
        v = self.data.get(k)
        return v if isinstance(v, (int, float)) else default

    def hp_bar(self, **kw):
        hp, mx = self.num("hp"), self.num("hpMax", 15) or 15
        return {"type": "bar", "label": self.api.tr("Health", "Здоров'я"), "icon": "❤", "value": hp, "max": mx,
                "color": "#C8432F", "text": f"{hp:g} / {mx:g}" if self.data else "—", "size": 12, **kw}

    def sat_bar(self, **kw):
        s, mx = self.num("sat"), self.num("satMax", 1500) or 1500
        return {"type": "bar", "label": self.api.tr("Satiety", "Ситість"), "icon": "🍖", "value": s, "max": mx,
                "color": "#D9B26A", "text": f"{s:.0f} / {mx:.0f}" if self.data else "—", "size": 12, **kw}

    def season(self):
        s = SEASONS.get(str(self.data.get("season")))
        return self.api.tr(*s) if s else self.api.tr("World", "Світ")

    def when(self):
        d = self.data
        if not d:
            return "—"
        return f'{self.api.tr("day", "день")} {d.get("day", "?")} · {d.get("time", "")}'

    def stats(self):
        tr, t = self.api.tr, self.tab
        temp = self.num("temp", 37)
        hot, cold = temp > 37.5, temp < 35.5
        return [
            self.hp_bar(id="tab:health", on=t == "health"),
            self.sat_bar(id="tab:food", on=t == "food"),
            {"type": "stat", "id": "tab:world", "on": t == "world", "icon": "🌡", "label": tr("Temperature", "Температура"),
             "value": f"{temp:.1f}" if self.data else "—", "unit": "°C",
             "sub": tr("air ", "повітря ") + (f'{self.num("air"):g}°C' if "air" in self.data else "—"),
             "color": "#E07A4F" if hot else "#7FB2D9" if cold else "#E8D9B5"},
            {"type": "stat", "id": "tab:keys", "on": t == "keys", "icon": "⌨", "label": tr("your buttons", "свої кнопки"),
             "value": tr("Hotkeys", "Хоткеї")},
        ]

    def menu(self):
        return {"health": self.health, "food": self.food, "world": self.world, "keys": self.hotkeys}.get(self.tab, self.health)()

    # ---------- сытость ----------
    def food(self):
        tr, d = self.api.tr, self.data
        mx = self.num("foodMax", 1500) or 1500
        food = d.get("food") or {}
        out = [{"type": "label", "label": tr("Satiety", "Ситість")}, self.sat_bar(size=16),
               {"type": "label", "label": tr("Nutrition", "Харчування")},
               {"type": "bar", "label": tr("Everything", "Усе разом"), "max": mx * 5, "size": 14, "text": "",
                "parts": [{"value": food.get(k, 0), "color": c, "label": tr(ru, uk)} for k, ru, uk, c in FOOD]}]
        out += [{"type": "bar", "label": tr(ru, uk), "value": food.get(k, 0), "max": mx, "color": c, "size": 10,
                 "text": f'{food.get(k, 0) / mx * 100:.0f}%'} for k, ru, uk, c in FOOD]
        return out

    # ---------- мир ----------
    def world(self):
        tr, d = self.api.tr, self.data
        temp = self.num("temp", 37)
        pr = PRECIP.get(str(d.get("precip") or "none"), PRECIP["none"])
        storm = d.get("storm") or {}
        hpd = storm.get("hoursPerDay") or 24
        if not storm:
            st, sub = "—", ""
        elif storm.get("on") is False:
            st, sub = tr("off", "вимк"), tr("no storms in this world", "у цьому світі штормів немає")
        elif storm.get("active"):
            st, sub = tr("now", "зараз"), tr("temporal storm in progress", "темпоральний шторм триває")
        else:
            hrs = max(0.0, float(storm.get("days") or 0) * hpd)
            st = f"{hrs / hpd:.1f} {tr('d', 'дн')}" if hrs >= hpd else f"{hrs:.0f} {tr('h', 'год')}"
            sub = tr("until the temporal storm", "до темпорального шторму")
        stab = self.num("stability", 1.0)
        return [
            {"type": "grid", "cols": 3, "items": [
                {"type": "stat", "icon": "🌡", "label": tr("Body", "Тіло"), "value": f"{temp:.1f}", "unit": "°C",
                 "color": "#E07A4F" if temp > 37.5 else "#7FB2D9" if temp < 35.5 else "#E8D9B5"},
                {"type": "stat", "icon": "☁", "label": tr("Air", "Повітря"), "value": f'{self.num("air"):g}' if "air" in d else "—", "unit": "°C"},
                {"type": "stat", "icon": "🌧", "label": tr("Weather", "Погода"), "value": tr(*pr),
                 "sub": tr("wind ", "вітер ") + f'{self.num("wind"):.1f}' if "wind" in d else ""},
                {"type": "stat", "icon": "🕑", "label": f'{self.season()}, {tr("day", "день")} {d.get("day", "?")}',
                 "value": d.get("time", "—"), "sub": str(d.get("date") or "")},
                {"type": "stat", "icon": "🌀", "label": tr("Storm", "Шторм"), "value": st, "sub": sub,
                 "color": "#C8432F" if storm.get("active") else ""},
                {"type": "stat", "icon": "⏳", "label": tr("Stability", "Стабільність"), "value": f"{stab * 100:.0f}", "unit": "%",
                 "color": "#C8432F" if stab < 0.25 else "#D9B26A" if stab < 0.6 else ""}]},
            {"type": "label", "label": tr("Position", "Позиція")},
            {"type": "grid", "cols": 3, "items": [
                {"type": "stat", "label": a.upper(), "value": f'{d[a]:g}' if isinstance(d.get(a), (int, float)) else "—"} for a in ("x", "y", "z")]},
            {"type": "bar", "label": tr("Temporal stability", "Темпоральна стабільність"), "value": stab * 100, "max": 100,
             "color": "#6FA8DC", "text": f"{stab * 100:.0f}%", "size": 10},
        ]

    # ---------- здоровье (S&R System) ----------
    def health(self):
        tr, snr = self.api.tr, self.data.get("snrs")
        if not isinstance(snr, dict) or not snr.get("zones"):
            return [self.hp_bar(size=16), {"type": "label", "label": tr(
                "S&R System (0.5.0+) not found in the game — showing health only",
                "S&R System (0.5.0+) у грі не знайдено — показую лише здоров'я")}]
        if (snr.get("format") or 0) >= 3:
            return self.health07(snr)
        zones = {z["id"]: z for z in snr["zones"] if isinstance(z, dict) and isinstance(z.get("id"), int)}
        if self.zone not in zones:
            self.zone = None
        t = snr.get("target") or {}
        other = bool(self.target) and not t.get("self")
        # фон куклы: тап мимо зон — общее состояние
        doll = [{"id": "zone:none", "x": -6, "y": -6, "w": 162, "h": 302, "r": 0, "fill": "#00000000", "stroke": "#00000000"},
                {"kind": "ellipse", "x": 41, "y": 266, "w": 68, "h": 11, "fill": "#000000", "opacity": 0.35}]
        for zid in DOLL_ORDER:
            z = zones.get(zid)
            if not z:
                continue
            x, y, w, h = DOLL[zid]
            badges = (["blood"] if z.get("bleeding") else []) + (["alert"] if z.get("blocked") else [])
            doll.append({"id": f"zone:{zid}", "x": x, "y": y, "w": w, "h": h, "r": 3, "fill": zone_color(z.get("severity", 0)),
                         "stroke": "#2B1F14", "bevel": True, "tex": True, "sel": zid == self.zone, "splint": bool(z.get("splinted")),
                         **({"band": 0.5, "bandH": 0.14 if h > 60 else 0.3} if z.get("dressed") else {}),
                         "badges": badges, "label": z.get("name", "")})
        texts = snr.get("texts") or {}
        if other:
            hp, mx = t.get("hp") or 0, t.get("hpMax") or 15
            hpbar = {"type": "bar", "label": t.get("name", "?"), "icon": "❤", "value": hp, "max": mx, "color": "#C8432F",
                     "text": f"{hp:g} / {mx:g}", "size": 12}
        else:
            hpbar = self.hp_bar(size=12)
        left = [hpbar, {"type": "shapes", "vw": 150, "vh": 290, "h": 300, "items": doll}]
        right = self.zone_menu(zones[self.zone], texts) if self.zone is not None else self.summary(snr, zones, texts)
        return self.who(snr) + self.bars() + [{"type": "grid", "widths": [1, 1.5], "fill": True, "items": [
            {"type": "card", "id": "zone:none", "scroll": True, "items": left},
            {"type": "card", "id": "zone:none", "scroll": True, "items": right}]}]

    # ---------- здоровье как во вкладке S&R 0.7 (формат 3, S&R 0.7.18+) ----------
    def health07(self, snr):
        """Слева — здоровье, кукла с отметками зон (✓ здорова, • заживает, ! нужно внимание) и «Общее состояние»;
        справа — обзор (травмированные зоны, условия, бонусы) или выбранная зона: карточка травмы, основное и
        дополнительное лечение. Тап по той же зоне или мимо кнопок — снова обзор."""
        tr = self.api.tr
        zones = {z["id"]: z for z in snr["zones"] if isinstance(z, dict) and isinstance(z.get("id"), int)}
        if self.zone not in zones:
            self.zone = None
        t, texts = snr.get("target") or {}, snr.get("texts") or {}
        other = bool(self.target) and not t.get("self")
        doll = [{"id": "zone:none", "x": -6, "y": -6, "w": 162, "h": 302, "r": 0, "fill": "#00000000", "stroke": "#00000000"},
                {"kind": "ellipse", "x": 41, "y": 266, "w": 68, "h": 11, "fill": "#000000", "opacity": 0.35}]
        for zid in DOLL_ORDER:
            z = zones.get(zid)
            if not z:
                continue
            x, y, w, h = DOLL[zid]
            # значки — как на кукле в игре: кровотечение и «!» (не заживает: нужно лечение или тепло)
            badges = (["blood"] if z.get("bleeding") else []) + (["alert"] if z.get("blocked") or (z.get("status") == "attention" and not z.get("bleeding")) else [])
            doll.append({"id": f"zone:{zid}", "x": x, "y": y, "w": w, "h": h, "r": 3, "fill": zone_color(z.get("severity", 0)),
                         "stroke": "#2B1F14", "bevel": True, "tex": True, "sel": zid == self.zone, "splint": bool(z.get("splinted")),
                         **({"band": 0.5, "bandH": 0.14 if h > 60 else 0.3} if z.get("dressed") else {}),
                         "badges": badges, "label": z.get("name", "")})
        if other:
            hp, mx = t.get("hp") or 0, t.get("hpMax") or 15
            hpbar = {"type": "bar", "label": t.get("name", "?"), "icon": "❤", "value": hp, "max": mx, "color": "#C8432F",
                     "text": f"{hp:g} / {mx:g}", "size": 12}
        else:
            hpbar = self.hp_bar(size=12)
        ov = snr.get("overall") or {}
        left = [hpbar, {"type": "label", "style": "bold", "label": (texts.get("overall") or tr("Overall condition", "Загальний стан"))
                        + ": " + str(ov.get("text", "")), **self.col(ov.get("color"))},
                {"type": "shapes", "vw": 150, "vh": 290, "fill": True, "h": 250, "items": doll}]      # во всю высоту (старый дек — 250)
        right = self.zone07(zones[self.zone], snr, texts) if self.zone is not None else self.overview07(snr, zones, texts)
        return self.who(snr) + self.bars() + [{"type": "grid", "widths": [1, 1.6], "fill": True, "items": [
            {"type": "card", "id": "zone:none", "scroll": True, "items": left},
            {"type": "card", "id": "zone:none", "scroll": True, "items": right}]}]

    @staticmethod
    def col(c, fallback=None):
        c = c if isinstance(c, str) and len(c) == 7 and c.startswith("#") else fallback
        return {"color": c} if c else {}

    def lines(self, xs, ink=None):
        """Строки S&R ({text, color, bold}) — мелким текстом; ink — цвет по умолчанию (на пергаменте — тёмный)."""
        return [{"type": "label", "style": "line", "label": str(x.get("text") or ""), **self.col(x.get("color"), ink)}
                for x in xs or [] if isinstance(x, dict) and x.get("text")]

    def overview07(self, snr, zones, texts):
        """Без выбранной зоны: травмированные зоны (нажимаются), условия восстановления, бонусы, подсказка."""
        tr = self.api.tr
        out = []
        hurt = sorted((z for z in zones.values() if z.get("status") not in (None, "healthy")),
                      key=lambda z: (-(z.get("severity") or 0), z.get("id", 0)))
        if not hurt:
            out.append({"type": "label", "style": "line", "color": "#88CC88", "label": texts.get("healthy") or tr("No injuries", "Травм немає")})
        for z in hurt:
            b = z.get("badge") or {}
            out.append({"type": "card", "id": f'zone:{z["id"]}', "items": [
                {"type": "label", "style": "bold", "label": z.get("name", ""), "color": "#E8D9B5"},
                {"type": "label", "style": "line", "label": z.get("sub", ""), **self.col(b.get("color"), "#A8977A")}]})
        out.append({"type": "label", "label": texts.get("conditions") or tr("Recovery conditions", "Умови відновлення")})
        out += self.lines(snr.get("conditions"))
        if snr.get("bonuses"):
            out.append({"type": "label", "label": texts.get("bonuses") or tr("Bonuses", "Бонуси")})
            out += self.lines(snr["bonuses"])
        out.append({"type": "label", "style": "line", "color": "#9A9080",
                    "label": texts.get("hint") or tr("Tap a zone — its injuries and treatment", "Тап по зоні — її травми й лікування")})
        return out

    def zone07(self, z, snr, texts):
        """Выбранная зона: выбор травмы (если их несколько), карточка, основное и дополнительное лечение."""
        tr = self.api.tr
        b = z.get("badge") or {}
        out = [{"type": "label", "style": "title", "label": z.get("name") or "?"},
               {"type": "label", "style": "bold", "label": b.get("text", ""), **self.col(b.get("color"))}]
        injs = [i for i in z.get("injuries") or [] if isinstance(i, dict) and isinstance(i.get("card"), dict)]
        if not injs:
            return out + [{"type": "label", "style": "line", "color": "#88CC88", "label": texts.get("zoneHealthy") or tr("Zone is healthy", "Зона здорова")}]
        injs.sort(key=lambda i: -(i["card"].get("severity") or 0))
        zid = z["id"]
        types = [i["type"] for i in injs]
        if self.inj.get(zid) not in types:
            self.inj[zid] = types[0]
        if len(injs) > 1:
            out.append({"type": "row", "items": [
                {"type": "button", "id": f'inj|{zid}|{i["type"]}', "on": i["type"] == self.inj[zid],
                 "label": f'{i.get("stageName", "")} · {i["card"].get("status", "")}'} for i in injs]})
        inj = next(i for i in injs if i["type"] == self.inj[zid])
        c, ink = inj["card"], "#3A2A1A"
        icon = inj["type"] if inj["type"] in INJURY_ICONS else "other"
        card = [{"type": "row", "items": [{"type": "image", "src": f"icons/{icon}.png", "w": 34, "h": 34},
                                          {"type": "label", "style": "title", "label": inj.get("stageName", ""), "color": "#2B1F14"}]},
                {"type": "label", "style": "bold", "label": c.get("subtitle", ""), "color": "#4A3A28"}]
        card += self.lines(c.get("desc"), ink)
        card.append({"type": "label", "style": "line", "color": "#2B1F14",
                     "label": f'{texts.get("recovery") or tr("Recovery", "Відновлення")}: {c.get("percent") or 0}%'})
        card.append({"type": "bar", "value": c.get("percent") or 0, "max": 100, "size": 10, "text": " ",
                     "color": "#8A1A10" if inj.get("blocked") else "#5E8A3A"})
        card += self.lines(c.get("recovery"), ink)
        if c.get("applied"):
            card.append({"type": "label", "style": "line", "label": c["applied"], "color": "#2F5F20"})
        out.append({"type": "card", "bg": "#D8C8A4", "items": card})
        key = f'{zid}|{inj["type"]}'
        # основное лечение
        out.append({"type": "label", "label": texts.get("mandatory") or tr("Main treatment", "Основне лікування")})
        if not c.get("mandatory"):
            out.append({"type": "label", "style": "line", "color": "#B8B0A0", "label": texts.get("mandatoryNone") or "—"})
        for m in c.get("mandatory") or []:
            tip = f'tip|{key}|m|{m.get("tag")}'
            label = m.get("title", "") + (f' · {m["duration"]}' if m.get("duration") else "")
            out.append({"type": "row", "items": [
                {"type": "button", "id": f'treat|{key}|{m.get("treatment") or ""}', "label": label, "wide": True,
                 "disabled": not (m.get("ok") and m.get("treatment"))},
                {"type": "button", "id": f"exp|{tip}", "label": "i", "on": tip in self.open}]})
            out.append({"type": "label", "style": "line", "label": m.get("sub", ""), "color": "#FF8A70" if m.get("lacking") else "#A8977A"})
            if tip in self.open:
                out.append({"type": "card", "items": self.lines(m.get("tip"))})
        for d in c.get("done") or []:
            out.append({"type": "label", "style": "line", "label": "✓ " + str(d), "color": "#A8D890"})
        # дополнительные средства
        out.append({"type": "label", "label": texts.get("optional") or tr("Optional treatment", "Додаткові засоби")})
        if not c.get("optional"):
            out.append({"type": "label", "style": "line", "color": "#B8B0A0", "label": texts.get("optionalNone") or "—"})
        for n, o in enumerate(c.get("optional") or []):
            tip = f'tip|{key}|o|{n}'
            act = f'item|{key}|{o.get("treatment")}|{o["item"]}' if o.get("item") else f'treat|{key}|{o.get("treatment")}'
            out.append({"type": "row", "items": [
                {"type": "button", "id": act, "label": o.get("name", "") + (f'  {o["bonus"]}' if o.get("bonus") else ""), "wide": True,
                 "disabled": not o.get("ok")},
                {"type": "button", "id": f"exp|{tip}", "label": "i", "on": tip in self.open}]})
            if tip in self.open:
                out.append({"type": "card", "items": self.lines(o.get("tip"))})
        t = snr.get("target") or {}
        if not t.get("self", True) and not t.get("inRange", True):
            out.append({"type": "label", "style": "line", "color": "#FF8080", "label": texts.get("tooFar") or tr("Get closer", "Підійди ближче")})
        return out

    def who(self, snr):
        """Кого лечить: себя или игрока в 5 блоках (S&R 0.6.2+ шлёт список). Дальше 3,5 блока — лечить нельзя."""
        tr = self.api.tr
        players = [p for p in snr.get("players") or [] if isinstance(p, dict) and p.get("id")]
        if not players and not self.target:
            return []
        t = snr.get("target") or {}
        items = [{"type": "button", "id": "who:0", "label": tr("Me", "Я"), "on": not self.target}]
        for p in players[:5]:
            inj = p.get("injuries") or 0
            items.append({"type": "button", "id": f'who:{p["id"]}', "on": p["id"] == self.target,
                          "label": f'{p.get("name", "?")} · {inj if inj else "✓"} · {p.get("dist", 0):g} {tr("m", "м")}'})
        out = [{"type": "row", "items": items}]
        if self.target and not t.get("self") and not t.get("inRange"):
            out.append({"type": "label", "style": "line", "color": "#FF8080", "label": tr(
                "{0} is too far ({1:g} m) — get closer than {2:g} m to treat", "{0} далеко ({1:g} м) — підійди ближче {2:g} м, щоб лікувати", t.get('name', ''), t.get('dist', 0), t.get('range', 3.5))})
        elif self.target and t.get("self") and not t.get("lost"):
            out.append({"type": "label", "style": "line", "color": "#A8977A", "label": tr("Loading…", "Завантажую…")})
        return out

    def bars(self):
        """Полосы лечения: своё (кого и чем) и то, что делает над тобой другой игрок. Идут сами между пакетами."""
        tr = self.api.tr
        p = ((self.data.get("snrs") or {}).get("progress") or {})
        since = max(0.0, time.monotonic() - self.got_at)
        out = []
        for key in ("mine", "onMe"):
            a = p.get(key)
            if not isinstance(a, dict):
                continue
            dur = float(a.get("duration") or 0) or 0.1
            done = min(dur, float(a.get("elapsed") or 0) + since)
            what = f'{a.get("treatment", "")} — {a.get("zone", "")}'
            if key == "mine":
                label = what + ("" if a.get("self") else f' ({a.get("patient", "")})')
            else:
                label = tr("{0} is treating you: ", "{0} лікує тебе: ", a.get('healer', '?')) + what
            out.append({"type": "bar", "icon": "✚", "label": label, "value": done, "max": dur, "size": 10,
                        "color": "#7FB2D9" if key == "mine" else "#D9B26A", "text": f"{done:.1f} / {dur:.1f} {tr('s', 'с')}"})
        return out

    def summary(self, snr, zones, texts):
        """Общее состояние, как в игре без выбранной зоны: травмы по тяжести, выздоровление, питание, бонусы."""
        tr = self.api.tr
        out = [{"type": "label", "label": texts.get("status") or tr("Condition", "Стан")}]
        hurt = []
        for z in zones.values():
            for inj in z.get("injuries") or []:
                hurt.append((z.get("severity", 0), z, inj))
        if not hurt:
            out.append({"type": "label", "style": "line", "color": "#88CC88",
                        "label": texts.get("healthy") or tr("No injuries", "Травм немає")})
        for _, z, inj in sorted(hurt, key=lambda x: (-x[0], x[1].get("id", 0))):
            mark = f' ({texts.get("bleedingShort") or tr("bleeding", "кровотеча")})' if inj.get("bleeding") else " (!)" if inj.get("blocked") else ""
            col = inj["color"] if str(inj.get("color", "")).startswith("#") and len(inj["color"]) == 7 else None
            out.append({"type": "label", "style": "line", "label": f'• {z.get("name", "")}: {inj.get("stageName", "")}{mark}',
                        **({"color": col} if col else {})})
        out += [self.line(x) for x in snr.get("summary") or []]
        if snr.get("bonuses"):
            out += [{"type": "label", "label": texts.get("bonuses") or tr("Bonuses", "Бонуси")}] + [self.line(x) for x in snr["bonuses"]]
        out.append({"type": "label", "style": "line", "color": "#A8977A",
                    "label": tr("Tap a zone — its injuries and treatment", "Тап по зоні — її травми й лікування")})
        return out

    def line(self, x, **kw):
        """Строка от S&R ({text, color}) — мелким текстом."""
        return {"type": "label", "style": "line", "label": str(x.get("text") or ""), **({"color": x["color"]} if x.get("color") else {}), **kw}

    def zone_menu(self, z, texts):
        tr = self.api.tr
        out = [{"type": "label", "label": z.get("name") or "?"}]
        if not z.get("injuries"):
            return out + [{"type": "label", "style": "line", "label": texts.get("zoneHealthy") or tr("Healthy", "Здорова"), "color": "#88CC88"}]
        zid = z["id"]
        for inj in z["injuries"]:
            card = [{"type": "label", "style": "line", "label": f'{inj.get("stageName", "")} ({inj.get("typeName", "")})',
                     **({"color": inj["color"]} if str(inj.get("color", "")).startswith("#") and len(inj["color"]) == 7 else {})}]
            card += [self.line(x) for x in inj.get("lines") or []]
            for t in inj.get("treatments") or []:
                key = f'{zid}|{inj["type"]}|{t["code"]}'
                label = t.get("name", t["code"]) + (f' +{t["bonus"]}%' if t.get("bonus") else "")
                many = len(t.get("variants") or []) > 1
                row = [{"type": "button", "id": f"treat|{key}", "label": label, "wide": True, "disabled": not t.get("ok")}]
                if many:
                    row.append({"type": "button", "id": f"exp|{key}", "label": "▴" if key in self.open else "▾",
                                "on": key in self.open})
                card.append({"type": "row", "items": row} if many else row[0])
                if many and key in self.open:
                    for v in t["variants"]:
                        card.append({"type": "button", "id": f'item|{key}|{v["code"]}', "wide": True,
                                     "label": f'  {v.get("name", v["code"])} ×{v.get("count", 0)}' + (f' +{v["bonus"]}%' if v.get("bonus") else "")})
                if not t.get("ok") and t.get("lacks"):
                    card.append({"type": "label", "style": "line", "label": t["lacks"], "color": "#999999"})
            out.append({"type": "card", "items": card})
        return out

    # ---------- хоткеи ----------
    def hotkeys(self):
        tr, keys = self.api.tr, self.keys()
        out = [{"type": "row", "items": [
            {"type": "label", "label": tr("Your buttons for the game", "Свої кнопки для гри")},
            {"type": "button", "id": "hkNew", "icon": "plus", "label": tr("New", "Нова")},
            {"type": "button", "id": "hkEdit", "icon": "edit", "label": tr("Done", "Готово") if self.edit else tr("Edit", "Змінити"),
             "on": self.edit}]}]
        if not keys:
            out.append({"type": "label", "label": tr(
                "Empty so far. «New» — a button: game commands (/home, .command, «slot 3») or a deck "
                "script (keys).",
                "Поки порожньо. «Нова» — кнопка: команди гри (/home, .команда, «слот 3») або скрипт дека (клавіші).")})
            return out
        out.append({"type": "grid", "cols": 3, "items": [
            {"type": "button", "id": f"hkPick:{i}" if self.edit else f"hk:{i}", "wide": True,
             "label": (k.get("name") or "?") + ("" if k.get("kind") == "keys" else " ⚑"),
             "on": self.edit and i == self.pick} for i, k in enumerate(keys)]})
        if self.edit and 0 <= self.pick < len(keys):
            k = keys[self.pick]
            out += [{"type": "input", "id": "hkName", "label": tr("Name", "Назва"), "placeholder": k.get("name", ""),
                     "value": k.get("name", ""), "button": tr("Rename", "Перейменувати")},
                    {"type": "select", "id": "hkKind", "label": tr("What it does", "Що робить"), "value": k.get("kind", "game"),
                     "options": [{"value": "game", "label": tr("Game commands (through the mod)", "Команди гри (через мод)")},
                                 {"value": "keys", "label": tr("Deck script (keys)", "Скрипт дека (клавіші)")}]},
                    {"type": "textarea", "id": "hkText", "rows": 5, "value": k.get("text", ""), "button": tr("Save", "Зберегти"),
                     "placeholder": tr("/home\nslot 3", "/home\nслот 3") if k.get("kind") != "keys" else tr("e\nwait 200\nctrl+1", "e\nпауза 200\nctrl+1"),
                     "label": tr("One per line: /command, .client command, «slot 3»", "По рядку: /команда, .команда клієнта, «слот 3»")
                     if k.get("kind") != "keys" else tr("Like a deck script button: keys are pressed into the active window (the game)",
                                                         "Як кнопка-скрипт дека: клавіші тиснуться в активне вікно (гра)")},
                    {"type": "button", "id": "hkDel", "label": tr("Delete the button", "Видалити кнопку"), "style": "dashed"}]
        else:
            out.append({"type": "label", "label": tr("⚑ — game commands through the mod; no mark — keys (the game must be the active window)",
                                                     "⚑ — команди гри через мод; без значка — клавіші (гра має бути активним вікном)")})
        return out
