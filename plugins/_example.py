"""Образец плагина Noryx — счётчик. Файлы, чьё имя начинается с «_», дек не загружает: чтобы попробовать,
скопируй этот файл как plugins/counter.py (или в папку plugins/counter/plugin.py) — через пару секунд в настройках
дека, в разделе «Плагины», появится «Счётчик», а в доке — его блок. Удалишь файл — всё пропадёт.
Подробно — plugins/README.txt."""

NAME = {"ru": "Счётчик", "uk": "Лічильник"}
ABOUT = {"ru": "Образец плагина: кнопки, ползунок, переключатель, плитка", "uk": "Зразок плагіна: кнопки, повзунок, перемикач, плитка"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
PERMS = []                               # ничего особенного не нужно


class Plugin:
    def __init__(self, api):
        self.api = api                       # api.cfg — свои настройки (сохраняются в конфиге дека)
        self.api.cfg.setdefault("n", 0)
        self.api.cfg.setdefault("step", 1)
        self.api.cfg.setdefault("loud", False)

    def start(self):                         # плагин включили (или дек запустился)
        self.api.log.info("counter start")

    def stop(self):                          # выключили / удалили / дек закрывается: отпусти устройства, потоки
        pass

    def ui(self):
        """Что показать на телефоне. Зовётся после каждого действия и после api.update()."""
        tr, c = self.api.tr, self.api.cfg
        return {
            "dock": [
                {"type": "text", "label": tr("Счёт", "Рахунок"), "value": c["n"]},
                {"type": "row", "items": [
                    {"type": "button", "id": "minus", "label": "−"},
                    {"type": "button", "id": "plus", "icon": "plus"},      # только значок
                ]},
                {"type": "slider", "id": "step", "label": tr("Шаг", "Крок"), "value": c["step"], "min": 1, "max": 10},
            ],
            "settings": [
                {"type": "row", "items": [
                    {"type": "text", "label": tr("Счёт", "Рахунок"), "value": c["n"]},
                    {"type": "button", "id": "reset", "label": tr("Сбросить", "Скинути")},
                ]},
                {"type": "toggle", "id": "loud", "label": tr("Подсказка на каждое нажатие", "Підказка на кожне натискання"),
                 "on": c["loud"]},
            ],
            "tiles": [
                {"id": "plus", "label": tr("Счётчик +", "Лічильник +"), "icon": "plus", "sub": str(c["n"]), "on": c["n"] > 0},
            ],
        }

    def action(self, aid, value):
        """Нажали кнопку / плитку (value — None), переключатель (True/False) или ползунок (число)."""
        c = self.api.cfg
        if aid == "plus":
            c["n"] += c["step"]
        elif aid == "minus":
            c["n"] -= c["step"]
        elif aid == "reset":
            c["n"] = 0
            self.api.event(self.api.tr("Счётчик сброшен", "Лічильник скинуто"))
        elif aid == "step":
            c["step"] = int(value)
        elif aid == "loud":
            c["loud"] = bool(value)
        self.api.save()
        if c["loud"] and aid in ("plus", "minus"):
            self.api.toast(f"{c['n']}")
