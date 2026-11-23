"""Моды — изменения самого дека (не плагины): вид (цвета, формы, шрифты — CSS и переменные --acc, --card, …) и
свои функции страницы (JS). Плагин — отдельная штука со своим кодом на ПК (лампа, игры); мод меняет интерфейс.

Мод — папка mods/<id>/ в папке дека (имена на «_» / «.» — мимо):
    mod.json   {"name": {"ru", "uk"} | "…", "about": …, "version": "1.0.0", "author": "…",
                "vars": {"--acc": "#FF7A3D", "--card": "#1B1F27"},   ← переменные оформления (только цвет / размер / шрифт)
                "css": "style.css", "js": "mod.js"}                 ← файлы (по умолчанию — эти имена, если они есть)
    картинки и шрифты рядом — страница берёт их по /mod/<id>/<путь>.
Новый или изменённый мод (отпечаток файлов не тот, что в cfg.mods) — «ждёт выбора»: основной дек показывает его
вживую с переключателем «Было / Стало» и кнопками «Оставить новый» / «Вернуть как было». Выбор — cfg.mods[id] =
{on, hash}; передумал — Настройки → «Моды». JS мода — код на телефоне с правами страницы дека: README предупреждает,
в окне выбора помечено.
"""
import base64
import hashlib
import json
import logging
import re
import shutil
from pathlib import Path, PurePosixPath

import i18n

log = logging.getLogger("mods")

ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
VAR_RE = re.compile(r"--[a-z0-9][a-z0-9-]{0,30}")
# значение переменной: цвет, размер, число, имя шрифта — без того, чем можно выйти из правила (; { } < > \ url( и т. п.)
VAL_RE = re.compile(r"[#A-Za-z0-9 .,%()+\-\"'/]{1,120}")
FILE_TYPES = {".css": "text/css; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".json": "application/json",
              ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif",
              ".svg": "image/svg+xml", ".woff2": "font/woff2", ".woff": "font/woff", ".ttf": "font/ttf", ".otf": "font/otf"}
MAX_FILES, MAX_SIZE = 200, 16 * 1024 * 1024
CSS_MAX, IMG_MAX = 64 * 1024, 4 * 1024 * 1024        # редактор вида: style.css и фон


def text(v, lang, mid=""):
    """Название / описание мода: строка или {"en": …, "uk": …} (старые — {"ru", "uk"}); на языке дека — i18n.ptext
    (свой текст, перевод из мовного пакета, английский)."""
    if lang == i18n.LANG:
        return i18n.ptext(mid, v)[:200]
    if isinstance(v, dict):
        return str(v.get(lang) or v.get("en") or v.get("ru") or next(iter(v.values()), ""))[:200]
    return str(v or "")[:200]


def clean_title(v):
    """Заголовок панели компоновки: строка или {"en": …, "uk": …, "<код>": …} (страница берёт язык дека — lyNm)."""
    if isinstance(v, dict):
        return {k: str(x)[:40] for k, x in v.items() if k in i18n.NAMES and isinstance(x, str) and x} or ""
    return str(v or "")[:40]


def mod_langs(v):
    return [x for x in dict.fromkeys(v if isinstance(v, list) else []) if isinstance(x, str) and x in i18n.NAMES][:10]


def clean_vars(v):
    out = {}
    for k, x in (v.items() if isinstance(v, dict) else ()):
        if isinstance(k, str) and VAR_RE.fullmatch(k) and isinstance(x, (str, int, float)) \
                and VAL_RE.fullmatch(str(x)) and "url(" not in str(x).lower() and "expression" not in str(x).lower():
            out[k] = str(x)
        if len(out) >= 60:
            break
    return out


PANEL_RE = re.compile(r"[a-z0-9][a-z0-9:_-]{0,40}")


def clean_layout(v):
    """Компоновка главного экрана из мода: сетка cols × rows, панели {id, kind, x, y, w, h, title, opts}.
    Что умеет kind — решает страница (свои виды панелей добавляют JS-моды); здесь — только форма и границы."""
    if not isinstance(v, dict):
        return None
    num = lambda x, lo, hi, d: max(lo, min(hi, int(x))) if isinstance(x, (int, float)) and not isinstance(x, bool) else d  # noqa: E731
    cols, rows = num(v.get("cols"), 4, 24, 12), num(v.get("rows"), 2, 12, 6)
    out = {"cols": cols, "rows": rows, "gap": num(v.get("gap"), 0, 24, 10), "dock": v.get("dock") is not False, "panels": []}
    seen = set()
    for p in v.get("panels") or []:
        if not isinstance(p, dict) or not isinstance(p.get("kind"), str) or not PANEL_RE.fullmatch(p["kind"]):
            continue
        pid = str(p.get("id") or "")[:20] or f"p{len(out['panels'])}"
        if pid in seen:
            continue
        seen.add(pid)
        x, y = num(p.get("x"), 0, cols - 1, 0), num(p.get("y"), 0, rows - 1, 0)
        opts = {}
        for k, val in (p.get("opts") or {}).items() if isinstance(p.get("opts"), dict) else ():
            if isinstance(k, str) and len(k) <= 30 and (isinstance(val, (bool, int, float)) or (isinstance(val, str) and len(val) <= 300)):
                opts[k] = val
            elif k == "text" and isinstance(val, dict):        # текст панели на разных языках {"en": …, "uk": …}
                opts[k] = {c: x[:300] for c, x in val.items() if c in i18n.NAMES and isinstance(x, str)}
            if len(opts) >= 30:
                break
        out["panels"].append({"id": pid, "kind": p["kind"], "x": x, "y": y, "w": num(p.get("w"), 1, cols - x, 2),
                              "h": num(p.get("h"), 1, rows - y, 2), "title": clean_title(p.get("title")), "opts": opts})
        if len(out["panels"]) >= 40:
            break
    return out


def safe_rel(rel):
    p = PurePosixPath(str(rel))
    return bool(p.parts) and not p.is_absolute() and ".." not in p.parts and "\\" not in str(rel) and ":" not in str(rel) \
        and len(str(rel)) <= 150


class Mod:
    def __init__(self, mid, folder):
        self.id, self.folder = mid, Path(folder)
        self.name = self.about = self.version = self.author = ""
        self.meta = {}
        self.vars, self.css, self.js = {}, "", ""
        self.layout = None
        self.hash, self.error = "", ""


class Mods:
    def __init__(self, deck, folder):
        self.deck = deck
        self.folder = Path(folder)
        self.items = {}                     # id → Mod

    @property
    def state(self):
        return self.deck.store.cfg.setdefault("mods", {})

    def lang(self):
        i18n.follow(self.deck.store.cfg.get("lang"))
        return i18n.LANG

    # ---------- поиск ----------
    def read(self, mid, d):
        m = Mod(mid, d)
        try:
            files = sorted(p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts)
            if len(files) > MAX_FILES or sum(p.stat().st_size for p in files) > MAX_SIZE:
                raise ValueError(i18n.tr("too large", "завеликий"))
            h = hashlib.sha256()
            for p in files:
                h.update(p.relative_to(d).as_posix().encode("utf-8") + b"\0")
                h.update(p.read_bytes().replace(b"\r\n", b"\n"))
            m.hash = h.hexdigest()[:16]
            meta = json.loads((d / "mod.json").read_text(encoding="utf-8-sig"))
            if not isinstance(meta, dict):
                raise ValueError(i18n.tr("mod.json is not an object", "mod.json — не об’єкт"))
            m.meta = meta
            m.version = str(meta.get("version") or "")[:20]
            m.author = str(meta.get("author") or "")[:60]
            m.vars = clean_vars(meta.get("vars"))
            m.layout = clean_layout(meta.get("layout"))
            for kind, default in (("css", "style.css"), ("js", "mod.js")):
                rel = meta.get(kind) or default
                if isinstance(rel, str) and safe_rel(rel) and (d / rel).is_file() and (d / rel).suffix == "." + kind:
                    setattr(m, kind, rel)
                elif meta.get(kind):
                    raise ValueError(f"{kind}: " + i18n.tr("no file ", "немає файлу ") + str(rel)[:60])
            if not (m.vars or m.css or m.js or (m.layout and m.layout["panels"])):
                raise ValueError(i18n.tr("no vars, css, js or layout", "ні vars, ні css, ні js, ні layout"))
        except (OSError, ValueError) as e:
            m.error = f"{type(e).__name__}: {e}" if not isinstance(e, ValueError) else str(e)
            log.warning("Мод «%s»: %s", mid, m.error)
        return m

    def scan(self):
        """Перечитать папку mods/. True — набор или содержимое поменялись."""
        found = {}
        if self.folder.is_dir():
            for d in sorted(self.folder.iterdir()):
                if d.is_dir() and d.name[:1] not in "._" and ID_RE.fullmatch(d.name) and (d / "mod.json").is_file():
                    found[d.name] = self.read(d.name, d)
        old = {k: (v.hash, v.error) for k, v in self.items.items()}
        self.items = found
        new = {k: (v.hash, v.error) for k, v in found.items()}
        if new != old:
            for mid in self.pending():
                log.info("Мод «%s»: новый или изменился — жду выбора «было / стало»", mid)
        return new != old

    # ---------- выбор ----------
    def on(self, mid):
        s = self.state.get(mid)
        return bool(isinstance(s, dict) and s.get("on")) and mid in self.items and not self.items[mid].error

    def pending(self):
        """Новые / изменённые с прошлого выбора — показать «было / стало»."""
        out = []
        for mid, m in self.items.items():
            s = self.state.get(mid)
            if not m.error and not (isinstance(s, dict) and s.get("hash") == m.hash):
                out.append(mid)
        return out

    def choose(self, mid, on):
        m = self.items.get(mid)
        if m is None or m.error:
            return False
        self.state[mid] = {"on": bool(on), "hash": m.hash}
        self.deck.store.save()
        log.info("Мод «%s»: %s", mid, i18n.tr("on", "увімкнено") if on else i18n.tr("off", "вимкнено"))
        return True

    # ---------- редактор вида (Настройки → «Моды» → «Новый вид»): мод собирается с телефона ----------
    def save_look(self, mid, name, look, css, img=None, layout=None):
        """Записать мод вида: mod.json (с look — чтобы редактор открыл его снова), style.css, фон bg.jpg (base64).
        Вернёт id или текст ошибки (ValueError). CSS — от своего телефона, но лишнего не пускаем: без @import и внешних url."""
        if not (isinstance(css, str) and len(css) <= CSS_MAX):
            raise ValueError("css")
        low = css.lower()
        if "@import" in low or "expression(" in low or "<" in css or "javascript:" in low:
            raise ValueError("css")
        for u in re.findall(r"url\(\s*['\"]?([^'\")]*)", css, re.I):
            if u not in ("bg.jpg", "bg.png"):
                raise ValueError("url")
        if mid:
            if not ID_RE.fullmatch(str(mid)):
                raise ValueError("id")
        else:
            n = 1
            while (self.folder / f"look-{n}").exists():
                n += 1
            mid = f"look-{n}"
        d = self.folder / mid
        old = {}
        try:
            old = json.loads((d / "mod.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            pass
        ver = str(old.get("version") or "1.0.-1").split(".")
        try:
            ver = f"{ver[0]}.{ver[1]}.{int(ver[2]) + 1}"
        except (IndexError, ValueError):
            ver = "1.0.0"
        meta = {"name": str(name or "").strip()[:60] or mid, "version": ver,
                "about": old.get("about") or {"en": "Look from the deck editor", "uk": "Вигляд з редактора дека"},
                "author": old.get("author") or "Noryx", "look": look if isinstance(look, dict) else {}, "css": "style.css"}
        lay = clean_layout(layout)
        if lay and lay["panels"]:
            meta["layout"] = lay
        if len(json.dumps(meta["look"])) > 8000:
            raise ValueError("look")
        d.mkdir(parents=True, exist_ok=True)
        if img is not None:
            m = re.fullmatch(r"data:image/(jpeg|png);base64,([A-Za-z0-9+/=]+)", str(img))
            raw = base64.b64decode(m.group(2)) if m else b""
            if not raw or len(raw) > IMG_MAX:
                raise ValueError("img")
            for old_img in ("bg.jpg", "bg.png"):
                (d / old_img).unlink(missing_ok=True)
            (d / ("bg.png" if m.group(1) == "png" else "bg.jpg")).write_bytes(raw)
        (d / "style.css").write_text(css, encoding="utf-8")
        (d / "mod.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
        log.info("Мод «%s»: сохранён редактором вида (%s)", mid, ver)
        return mid

    def looks(self):
        """Моды, сделанные редактором вида (в mod.json есть look)."""
        return [mid for mid, m in self.items.items() if isinstance(m.meta.get("look"), dict)]

    # ---------- телефону ----------
    @staticmethod
    def langs_of(mid, v):
        """Языки мода ("langs" в mod.json) и говорит ли он на языке дека (i18n.speaks)."""
        L = mod_langs(v)
        return {"langs": L, "langOk": i18n.speaks(mid, L)}

    def msg(self):
        lang, pend = self.lang(), set(self.pending())
        out = []
        for mid, m in sorted(self.items.items()):
            out.append({"id": mid, "name": text(m.meta.get("name"), lang, mid) or mid, "about": text(m.meta.get("about"), lang, mid),
                        **self.langs_of(mid, m.meta.get("langs")),
                        "version": m.version, "author": m.author, "vars": m.vars, "css": m.css, "js": m.js, "hash": m.hash,
                        "on": self.on(mid), "pending": mid in pend, "error": m.error,
                        "look": m.meta.get("look") if isinstance(m.meta.get("look"), dict) else None, "layout": m.layout,
                        "img": next((f for f in ("bg.jpg", "bg.png") if (m.folder / f).is_file()), "")})
        return {"t": "mods", "list": out, "folder": str(self.folder)}

    def file(self, mid, rel):
        """Файл мода для страницы — только из его папки и только понятных типов."""
        m = self.items.get(mid)
        if m is None or not safe_rel(rel):
            return None
        f = (m.folder / rel).resolve()
        try:
            f.relative_to(m.folder.resolve())
        except ValueError:
            return None
        return f if f.is_file() and f.suffix.lower() in FILE_TYPES else None


# ---------- порядок в папках: плагины — в plugins/, моды — в mods/ ----------
def _kind(p):
    """Что это: «plugin» (plugin.py / файл .py с class Plugin), «mod» (mod.json), «empty» (папка без файлов), None."""
    if p.is_file():
        if p.suffix == ".py":
            try:
                return "plugin" if "class Plugin" in p.read_text(encoding="utf-8", errors="replace") else None
            except OSError:
                return None
        return None
    plug, mod = (p / "plugin.py").is_file(), (p / "mod.json").is_file()
    if plug != mod:
        return "plugin" if plug else "mod"
    if not plug and not any(f.is_file() for f in p.rglob("*")):
        return "empty"
    return None


def sort_folders(plugins, mods):
    """Плагин, положенный в mods/, — в plugins/; мод в plugins/ — в mods/. Пустые остатки папок
    (git переносит файлы, а пустые папки оставляет) — прочь, если такая же есть по соседству.
    Имя занято — не трогаем. Возвращает [(откуда, куда)]; куда = None — удалена пустая."""
    moves = []
    for src, dst, want in ((Path(mods), Path(plugins), "plugin"), (Path(plugins), Path(mods), "mod")):
        if not src.is_dir():
            continue
        for p in sorted(src.iterdir()):
            if p.name[:1] in "._" or p.is_symlink():
                continue
            try:
                k = _kind(p)
                if k == want:
                    t = dst / p.name
                    if t.exists():
                        log.warning("Не на своём месте: %s — в %s уже есть такое имя, оставляю", p, dst)
                        continue
                    dst.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(p), str(t))
                    log.info("Переложил %s → %s", p, t)
                    moves.append((p, t))
                elif k == "empty" and src == Path(mods) and (dst / p.name).exists():
                    shutil.rmtree(p)
                    log.info("Убрал пустую папку %s", p)
                    moves.append((p, None))
            except OSError as e:
                log.warning("Не получилось разложить %s: %s", p, e)
    return moves
