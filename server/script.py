"""Скрипты кнопок — коротко, как в AutoHotkey. Одна строка — один шаг:

    ctrl+a              нажать сочетание (имена клавиш — как у хоткеев)
    пауза 300           подождать 300 мс (можно «1.5с», «2 s»; wait / sleep)
    текст Привет!       набрать текст (text / type)
    держать w 1500      зажать клавишу или сочетание на 1,5 с (hold)
    ; комментарий       строки с ; # // пропускаются

Сценарии — те же скрипты, плюс действия самого дека (имена — как на плитках, в микшере, в режимах):
    режим Хобби         переключить режим (mode)
    запуск OBS          запустить программу, уже запущена — показать её окно (run / open)
    закрыть Discord     закрыть программу (close)
    выход Наушники      устройство вывода звука — по части названия (output)
    громкость 40        общая громкость; «громкость Spotify 30» — громкость программы (volume)
    микрофон выкл       микрофон вкл / выкл (mic on / off)
    лампа 60            яркость лампы, % (lamp)

Сколько раз повторять (или «по кругу» до повторного нажатия) — настройка кнопки, не строка скрипта.
Разбор общий для Windows и Linux; клавиши проверяет функция known() модуля клавиатуры.
"""
import re

import i18n

MAX_STEPS = 200
MAX_MS = 60_000          # одна пауза или удержание — не дольше минуты
MAX_TEXT = 500
MAX_REPEAT = 1000
MIN_LOOP_MS = 20         # «по кругу» без пауз не должен заваливать ПК нажатиями

WAIT = {"пауза", "ждать", "жди", "чекати", "зачекати", "wait", "sleep", "delay"}
TEXT = {"текст", "набрать", "набрати", "text", "type", "send"}
HOLD = {"держать", "зажать", "тримати", "затиснути", "hold"}
MODE = {"режим", "mode"}
RUN = {"запуск", "запустить", "запустити", "открыть", "відкрити", "run", "start", "open"}
CLOSE = {"закрыть", "закрити", "close"}
OUT = {"выход", "вихід", "output"}
VOL = {"громкость", "гучність", "volume"}
MIC = {"микрофон", "мікрофон", "mic"}
LAMP = {"лампа", "lamp"}
ON = {"вкл", "включить", "увімк", "ввімкнути", "on", "1"}
OFF = {"выкл", "выключить", "вимк", "вимкнути", "off", "0"}
DECK = ("mode", "run", "close", "out", "vol", "mic", "lamp")     # шаги-действия дека: сценарий показывает, какой идёт


def parse_ms(s):
    """'500' / '500мс' / '1.5с' / '2 s' → миллисекунды; None — не число."""
    m = re.fullmatch(r"\s*(\d+(?:[.,]\d+)?)\s*(мс|ms|с|сек|s|sec)?\s*", s or "", re.I)
    if not m:
        return None
    v = float(m.group(1).replace(",", "."))
    unit = (m.group(2) or "мс").lower()
    return int(round(v * 1000)) if unit in ("с", "сек", "s", "sec") else int(round(v))


def _combo(s, known):
    """'ctrl + shift+S' → ('ctrl+shift+s', None) или (None, (код ошибки, клавиша))."""
    parts = [p for p in re.sub(r"\s+", "", s or "").lower().split("+") if p]
    if not parts:
        return None, ("empty",)
    bad = [p for p in parts if not known(p)]
    if bad:
        return None, ("key", bad[0])
    return "+".join(parts), None


# (английский — основа и ключ пакета языка, украинский)
MSG = {
    "wait": ("«wait» needs a number: wait 500 or wait 1.5s", "після «пауза» потрібне число: пауза 500 або пауза 1.5с"),
    "wait_max": ("wait — no longer than 60 s", "пауза — не довше 60 с"),
    "text": ("«text» needs the text itself", "після «текст» потрібен сам текст"),
    "text_max": ("text — no longer than {} characters", "текст — не довше {} символів"),
    "hold": ("hold: a key and a time, e.g. «hold w 1500»", "тримати: клавіша й час, наприклад «тримати w 1500»"),
    "hold_max": ("hold — no longer than 60 s", "тримати — не довше 60 с"),
    "empty": ("empty combination", "порожнє сполучення"),
    "key": ("unknown key «{}»", "невідома клавіша «{}»"),
    "line": ("unclear line «{}»", "незрозумілий рядок «{}»"),
    "many": ("more than {} steps", "кроків більше {}"),
    "name": ("«{}» needs a name", "після «{}» потрібна назва"),
    "pct": ("«{}»: a number from 0 to 100", "«{}»: число від 0 до 100"),
    "mic": ("mic: «on» or «off»", "мікрофон: «вкл» або «вимк»"),
}
ARGS = {"text_max": (MAX_TEXT,), "many": (MAX_STEPS,)}


def _pct(s):
    try:
        v = int(str(s).strip().rstrip("%"))
    except ValueError:
        return None
    return v if 0 <= v <= 100 else None


def parse(text, known, uk=False):
    """Текст скрипта → (шаги, ошибки). Шаги: ("keys", сочетание) | ("wait", мс) | ("text", строка) |
    ("hold", сочетание, мс). Ошибки: [(номер строки, что не так)] — на языке интерфейса."""
    steps, errors = [], []

    def err(n, code, *a):
        en, ua = MSG[code]
        errors.append((n, (ua if uk else i18n.PACK.get(en, en)).format(*(a or ARGS.get(code, ())))))

    for n, raw in enumerate(str(text or "").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith((";", "#", "//")):
            continue
        word, _, rest = line.partition(" ")
        w = word.lower().rstrip(":")
        rest = rest.strip()
        if w in MODE | RUN | CLOSE | OUT:
            if not rest:
                err(n, "name", word)
            else:
                steps.append(("mode" if w in MODE else "run" if w in RUN else "close" if w in CLOSE else "out", rest))
        elif w in VOL:
            who, _, num = rest.rpartition(" ")
            v = _pct(num)
            if v is None:
                err(n, "pct", word)
            else:
                steps.append(("vol", who.strip() or None, v))
        elif w in LAMP:
            v = _pct(rest)
            if v is None:
                err(n, "pct", word)
            else:
                steps.append(("lamp", v))
        elif w in MIC:
            if rest.lower() in ON | OFF:
                steps.append(("mic", rest.lower() in ON))
            else:
                err(n, "mic")
        elif w in WAIT:
            ms = parse_ms(rest)
            if ms is None:
                err(n, "wait")
            elif ms > MAX_MS:
                err(n, "wait_max")
            else:
                steps.append(("wait", ms))
        elif w in TEXT:
            parts = raw.split(None, 1)
            t = parts[1] if len(parts) > 1 else ""
            if not t:
                err(n, "text")
            elif len(t) > MAX_TEXT:
                err(n, "text_max")
            else:
                steps.append(("text", t))
        elif w in HOLD:
            m = re.fullmatch(r"(.+?)\s+(\S+)", rest.strip())
            ms = parse_ms(m.group(2)) if m else None
            if not m or ms is None:
                err(n, "hold")
                continue
            combo, e = _combo(m.group(1), known)
            if e:
                err(n, *e)
            elif ms > MAX_MS:
                err(n, "hold_max")
            else:
                steps.append(("hold", combo, ms))
        else:
            combo, e = _combo(line, known)
            if e and " " in line:
                err(n, "line", line[:30])
            elif e:
                err(n, *e)
            else:
                steps.append(("keys", combo))
    if len(steps) > MAX_STEPS:
        err(0, "many")
    return steps, errors


def seconds(steps):
    """Примерная длительность одного прогона (для подсказки в редакторе)."""
    ms = 0
    for st in steps:
        if st[0] == "wait":
            ms += st[1]
        elif st[0] == "hold":
            ms += st[2] + 60
        elif st[0] == "text":
            ms += 20 * len(st[1])
        elif st[0] in DECK:
            ms += 300
        else:
            ms += 20 * (st[1].count("+") + 1) * 2
    return round(ms / 1000, 2)


def repeat_of(it):
    """Сколько раз повторять кнопку-скрипт: (раз, по_кругу)."""
    if it.get("loop"):
        return 0, True
    try:
        return max(1, min(MAX_REPEAT, int(it.get("repeat") or 1))), False
    except (TypeError, ValueError):
        return 1, False
