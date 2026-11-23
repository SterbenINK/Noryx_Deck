"""Образец «вида как в игре»: панель персонажа в духе Vintage Story — шкалы здоровья и сытости, питание по видам
еды, температура, время и сезон, координаты. Файлы на «_» дек не загружает: чтобы попробовать, скопируй как
plugins/vintage.py. Это заготовка под мод игры: данные ждёт JSON-ом по UDP на 127.0.0.1:47811 (мод шлёт раз
в секунду); пока игры нет — показывает пример, помеченный «нет связи с игрой». Игровых картинок тут нет — только
свои цвета и шрифт; свои значки и текстуру можно положить в папку плагина (plugins/vintage/…, см. README).

Пример пакета от мода:
  {"hp": 12.5, "hpMax": 15, "sat": 1100, "satMax": 1500,
   "food": {"fruit": 300, "veg": 900, "grain": 1200, "protein": 400, "dairy": 0},  # из 1500
   "temp": 36.6, "air": 14, "time": "14:20", "day": 12, "season": "Spring", "x": 512, "y": 110, "z": -2048}"""
import json
import socket
import threading
import time

NAME = {"en": "Vintage Story (sample)", "uk": "Vintage Story (зразок)"}
ABOUT = {"en": "Character panel from the game: bars, nutrition, time, coordinates", "uk": "Панель персонажа з гри: шкали, харчування, час, координати"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
LANGS = ["en", "uk"]                     # на каких языках свои тексты (первый — основной); другие — из языковых пакетов дека
PERMS = ["network"]
PORT = 47811

# цвета в духе интерфейса игры: тёмное дерево, пергаментный текст
THEME = {"bg": "#1E1812", "panel": "#2B221A", "border": "#4A3A2A", "text": "#E8D9B5", "muted": "#A8977A",
         "accent": "#D9B26A", "radius": 6, "font": "serif"}
FOOD = [("fruit", "Fruit", "Фрукти", "#C9563C"), ("veg", "Vegetables", "Овочі", "#5E9A3A"),
        ("grain", "Grain", "Зерно", "#D2AE52"), ("protein", "Protein", "Білки", "#9C5B3B"),
        ("dairy", "Dairy", "Молочне", "#DCD6C4")]
DEMO = {"hp": 11.5, "hpMax": 15, "sat": 1040, "satMax": 1500,
        "food": {"fruit": 420, "veg": 880, "grain": 1210, "protein": 360, "dairy": 90},
        "temp": 36.9, "air": 12, "time": "14:20", "day": 12, "season": "Spring", "x": 512, "y": 110, "z": -2048}


class Plugin:
    def __init__(self, api):
        self.api = api
        self.data, self.seen = dict(DEMO), 0.0
        self.sock, self.run = None, False

    def start(self):
        self.run = True
        threading.Thread(target=self.listen, daemon=True).start()
        self.tick()

    def stop(self):
        self.run = False
        if self.sock:
            self.sock.close()

    # ----- данные от мода -----
    def listen(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.bind(("127.0.0.1", PORT))
        except OSError as e:
            self.api.log.warning("vintage: порт %s занят: %s", PORT, e)
            return
        while self.run:
            try:
                raw, _ = self.sock.recvfrom(65536)
                d = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict):
                self.api.call(lambda d=d: self.got(d))

    def got(self, d):
        self.data.update(d)
        self.seen = time.monotonic()
        self.api.update()

    def tick(self):                       # раз в 3 с — чтобы «нет связи» появлялось само, когда игру закрыли
        if self.run:
            self.api.update()
            self.api.later(3, self.tick)

    # ----- вид -----
    def ui(self):
        tr, d = self.api.tr, self.data
        live = time.monotonic() - self.seen < 5
        hp = {"type": "bar", "label": tr("Health", "Здоров'я"), "icon": "❤", "value": d["hp"], "max": d["hpMax"],
              "color": "#C8432F", "text": f'{d["hp"]:.1f} / {d["hpMax"]:g}', "size": 14}
        sat = {"type": "bar", "label": tr("Satiety", "Ситість"), "icon": "🍖", "value": d["sat"], "max": d["satMax"],
               "color": "#D9B26A", "text": f'{d["sat"]:.0f} / {d["satMax"]:.0f}', "size": 14}
        temp = {"type": "stat", "icon": "🌡", "label": tr("Body", "Тіло"), "value": f'{d["temp"]:.1f}', "unit": "°C",
                "color": "#E07A4F" if d["temp"] > 37.5 else "#7FB2D9" if d["temp"] < 35.5 else "#E8D9B5"}
        note = [] if live else [{"type": "label", "label": tr("no connection to the game — sample", "немає зв'язку з грою — приклад"),
                                 "color": "#A8977A"}]
        food = [{"type": "bar", "label": tr(ru, uk), "value": d["food"].get(k, 0), "max": 1500, "color": c, "size": 10,
                 "text": f'{d["food"].get(k, 0) / 15:.0f}%'} for k, ru, uk, c in FOOD]
        return {
            "theme": THEME,
            "dock": note + [hp, sat, {"type": "row", "items": [
                dict(temp, label=""), {"type": "stat", "icon": "🕑", "value": d["time"]}]},
                {"type": "text", "label": d["season"], "value": f'{tr("day", "день")} {d["day"]}'}],
            "screen": note + [{"type": "grid", "cols": 3, "items": [
                {"type": "card", "title": tr("Character", "Персонаж"), "icon": "🧍", "items": [hp, sat, temp]},
                {"type": "card", "title": tr("Nutrition", "Харчування"), "icon": "🍞", "items": [
                    {"type": "bar", "label": tr("Everything", "Усе разом"), "max": 1500 * 5, "size": 16,
                     "parts": [{"value": d["food"].get(k, 0), "color": c, "label": tr(ru, uk)} for k, ru, uk, c in FOOD],
                     "text": ""}] + food},
                {"type": "card", "title": tr("World", "Світ"), "icon": "🌍", "items": [
                    {"type": "grid", "cols": 2, "items": [
                        {"type": "stat", "icon": "🕑", "label": f'{d["season"]}, {tr("day", "день")} {d["day"]}', "value": d["time"]},
                        {"type": "stat", "icon": "☁", "label": tr("Air", "Повітря"), "value": f'{d["air"]:g}', "unit": "°C"}]},
                    {"type": "label", "label": tr("Position", "Позиція")},
                    {"type": "grid", "cols": 3, "items": [
                        {"type": "stat", "label": a.upper(), "value": f'{d[a]:g}'} for a in ("x", "y", "z")]}]}]}],
            "tiles": [{"id": "screen", "label": "Vintage Story", "icon": "🌍", "screen": True,
                       "sub": tr("in game", "у грі") if live else tr("no connection", "немає зв'язку"), "on": live}],
        }

    def action(self, aid, value):
        pass
