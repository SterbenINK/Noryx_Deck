"""Плитки с телефона (сообщение layout): страница шлёт раскладку режима целиком, дек кладёт её в конфиг. Здесь —
чистка: только известные виды плиток и простые значения в разумных пределах. Это защита от испорченного конфига и
ошибок страницы, а не от чужих: путь программы и скрипт задаёт сам хозяин (чужих не пускает server/pair.py)."""
import logging
import math
import re

log = logging.getLogger("deck.tiles")

KINDS = ("app", "group", "macro", "toggle", "plugin")
MAX_TILES = 256            # плиток в режиме (страниц может быть несколько)
MAX_GROUP = 64             # программ в группе
MAX_LIST = 64              # прочие списки внутри плитки (контекстные кнопки, клавиши)
MAX_KEYS = 40              # полей у плитки
LIMITS = {"script": 20000, "path": 2000, "target": 2000, "label": 120, "text": 4000}   # длина строк; прочие — STR
STR = 500
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,31}")


def _value(key, v, depth):
    """Значение поля: None / bool / число / строка / список / словарь — без вложенности глубже 3."""
    if v is None or isinstance(v, bool):
        return v
    if isinstance(v, int):
        return v if abs(v) < 2 ** 53 else None
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, str):
        return v[:LIMITS.get(key, STR)]
    if depth >= 3:
        return None
    if isinstance(v, list):
        return [_value(key, x, depth + 1) for x in v[:MAX_LIST]]
    if isinstance(v, dict):
        return _fields(v, depth + 1)
    return None


def _fields(d, depth):
    out = {}
    for k, v in list(d.items())[:MAX_KEYS]:
        if isinstance(k, str) and KEY.fullmatch(k):
            out[k] = _value(k, v, depth)
    return out


def clean_items(items, inner=False):
    """Раскладка режима с телефона → список плиток. Не список — None (раскладку не трогаем)."""
    if not isinstance(items, list):
        return None
    limit = MAX_GROUP if inner else MAX_TILES
    out = []
    for it in items[:limit * 4]:
        if len(out) >= limit:
            break
        if not isinstance(it, dict) or it.get("kind") not in (("app",) if inner else KINDS):
            continue
        x = _fields({k: v for k, v in it.items() if k != "items"}, 0)
        if it["kind"] == "group":
            x["items"] = clean_items(it.get("items"), True) or []
        out.append(x)
    dropped = len(items) - len(out)
    if dropped and not inner:
        log.warning("Раскладка с телефона: отброшено плиток — %d", dropped)
    return out
