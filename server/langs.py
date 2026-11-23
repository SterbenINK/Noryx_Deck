"""Мовні пакети з каталогу: catalog/langs/<код>.json в репозитории → langs/<код>.json в папке дека.

Пакет — {"code", "name" (своё название), "version", "strings": {англ. текст: перевод}, "plugins": {id: {…}}}.
Английский и украинский — в самом коде (i18n.BUILTIN), их не качают. Скачивается из того же коммита main, что и
индекс каталога (Center.sha), со сверкой SHA-256 из индекса. Только стандартная библиотека."""
import ast
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

import i18n

log = logging.getLogger("langs")
CODE_RE = re.compile(r"[a-z]{2}(-[A-Z]{2})?")
MAX_PACK = 2 * 1024 * 1024


def clean_index(data):
    """Раздел langs индекса каталога → {код: {name, version, size, sha256}} (только знакомые коды)."""
    out = {}
    items = data.get("langs") if isinstance(data, dict) else None
    for code, m in (items.items() if isinstance(items, dict) else ()):
        if not (isinstance(code, str) and CODE_RE.fullmatch(code) and code in i18n.NAMES and code not in i18n.BUILTIN
                and isinstance(m, dict) and re.fullmatch(r"[0-9a-f]{64}", str(m.get("sha256", "")))
                and isinstance(m.get("size"), int) and 0 < m["size"] <= MAX_PACK):
            continue
        out[code] = {"name": i18n.NAMES[code], "version": str(m.get("version") or ""), "size": m["size"],
                     "sha256": m["sha256"]}
    return out


def build_index(repo):
    """Для catalog.build_index: catalog/langs/<код>.json → {код: {name, version, size, sha256}}."""
    out = {}
    base = Path(repo) / "catalog" / "langs"
    for f in sorted(base.glob("*.json")) if base.is_dir() else ():
        code = f.stem
        if not (CODE_RE.fullmatch(code) and code in i18n.NAMES):
            continue
        data = f.read_bytes().replace(b"\r\n", b"\n")
        try:
            ver = str(json.loads(data.decode("utf-8")).get("version") or "")
        except ValueError:
            continue
        out[code] = {"name": i18n.NAMES[code], "version": ver, "size": len(data),
                     "sha256": hashlib.sha256(data).hexdigest()}
    return out


def check_pack(data):
    """Скачанный пакет: JSON-объект со strings — словарь строка → строка."""
    d = json.loads(data.decode("utf-8"))
    if not (isinstance(d, dict) and isinstance(d.get("strings"), dict)
            and all(isinstance(k, str) and isinstance(v, str) for k, v in d["strings"].items())):
        raise ValueError("strings")
    return d


class Langs:
    def __init__(self, folder=None):
        self.folder = Path(folder) if folder else i18n.LANG_DIR

    def path(self, code):
        return self.folder / f"{code}.json"

    def installed(self):
        """{код: версия} скачанных пакетов (без en / uk — они в коде)."""
        out = {}
        for code in i18n.NAMES:
            if code in i18n.BUILTIN:
                continue
            p = self.path(code)
            if p.is_file():
                try:
                    out[code] = str(json.loads(p.read_text(encoding="utf-8")).get("version") or "")
                except (OSError, ValueError):
                    continue
        return out

    def save(self, code, data, sha256):
        """Пакет из каталога: размер и хэш как в индексе, внутри — словарь строк. Пишем через временный файл."""
        if hashlib.sha256(data).hexdigest() != sha256:
            raise ValueError("sha256")
        check_pack(data)
        self.folder.mkdir(parents=True, exist_ok=True)
        tmp = self.path(code).with_suffix(".tmp")
        tmp.write_bytes(data)
        tmp.replace(self.path(code))
        log.info("Language pack %s installed", code)

    def delete(self, code):
        if code in i18n.BUILTIN:
            return False
        try:
            self.path(code).unlink()
            log.info("Language pack %s deleted", code)
            return True
        except FileNotFoundError:
            return False

    def file(self, code):
        """Файл пакета для телефона (/lang/<код>.json) — только знакомый код и существующий файл."""
        if code in i18n.NAMES and code not in i18n.BUILTIN and self.path(code).is_file():
            return self.path(code)
        return None


# ---------- ключи для переводчиков: все английские строки дека ----------
_JS_STR = r"""'((?:\\.|[^'\\\n])*)'"""


def _js_unescape(s):
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), s)


def keys(repo):
    """Английские строки страницы (первый аргумент T('…', '…'), подписи стандартных плиток LABEL_STD, ошибки
    саундпада PAD_ERR) и сервера (первый аргумент tr / i18n.tr, таблицы MSG / PERMS / LABELS / ABOUT, названия
    встроенных режимов) → отсортированный список. По нему переводят пакеты и проверяют, что в них не устарело."""
    repo = Path(repo)
    out = set()
    page = (repo / "client" / "index.html").read_text(encoding="utf-8")
    out.update(_js_unescape(m.group(1)) for m in re.finditer(r"\bT\(\s*" + _JS_STR + r"\s*,", page))
    for name in ("LABEL_STD", "PAD_ERR"):
        i = page.find(f"const {name} =")
        if i >= 0:
            block = page[i:page.find(";\n", i)]
            out.update(_js_unescape(m.group(1)) for m in re.finditer(r"\[(?:/[^/]+/, )?" + _JS_STR + r",", block))
    for f in sorted((repo / "server").glob("*.py")):
        tree = ast.parse(f.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                    and (getattr(n.func, "attr", None) == "tr" or getattr(n.func, "id", None) == "tr")):
                out.add(n.args[0].value)
            if isinstance(n, ast.Assign) and len(n.targets) == 1 and getattr(n.targets[0], "id", None) in (
                    "MSG", "PERMS", "LABELS", "ABOUT") and isinstance(n.value, ast.Dict) and f.name in ("script.py", "perms.py", "roles.py"):
                for v in n.value.values:
                    if isinstance(v, ast.Tuple):
                        out.update(e.value for i, e in enumerate(v.elts) if i % 2 == 0 and isinstance(e, ast.Constant)
                                   and isinstance(e.value, str))
            if f.name == "store.py" and isinstance(n, ast.Dict):
                ks = [getattr(k, "value", None) for k in n.keys]
                if "name" in ks and "uk" in ks and "icon" in ks:
                    out.add(n.values[ks.index("name")].value)
    out.update(i18n.EN.values())                      # шаблоны лога (logs/server.log) — тоже по английскому
    try:                                                 # лог изменений дека (Настройки → Обновления)
        import changelog
        out.update(i["en"] for e in changelog.parse((repo / "server" / "changelog.py").read_text(encoding="utf-8"))
                   for i in e["items"])
    except OSError:
        pass
    return sorted(x for x in out if x.strip())


_UK = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")


def plugin_keys(folder):
    """Английские строки плагина (папка или файл): первый аргумент api.tr / tr, "en" в {"en": …, "uk": …}, первая
    строка пары («English», «Українська») в кортежах-таблицах. Их переводят в пакете: plugins[<id>]."""
    out = set()
    folder = Path(folder)
    for f in ([folder] if folder.is_file() else sorted(folder.rglob("*.py"))):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError):
            continue
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) and isinstance(n.args[0].value, str)
                    and (getattr(n.func, "attr", None) == "tr" or getattr(n.func, "id", None) == "tr")):
                out.add(n.args[0].value)
            elif isinstance(n, ast.Dict):
                ks = [getattr(k, "value", None) for k in n.keys]
                if "en" in ks and "uk" in ks and isinstance(n.values[ks.index("en")], ast.Constant):
                    out.add(n.values[ks.index("en")].value)
            elif isinstance(n, (ast.Tuple, ast.List)):
                el = n.elts
                for a, b in zip(el, el[1:]):
                    if (isinstance(a, ast.Constant) and isinstance(b, ast.Constant) and isinstance(a.value, str)
                            and isinstance(b.value, str) and not _UK.search(a.value) and _UK.search(b.value)):
                        out.add(a.value)
    return sorted(x for x in out if isinstance(x, str) and x.strip())


def plugin_dirs(repo):
    """{id: папка или файл} плагинов из репозитория: каталог и образцы plugins/_*.py."""
    repo = Path(repo)
    out = {d.name: d for d in sorted((repo / "catalog" / "plugins").glob("*")) if (d / "plugin.py").is_file()}
    for f in sorted((repo / "plugins").glob("_*.py")):
        out[f.stem.lstrip("_")] = f
    return out


def stale(repo):
    """{код: [строки пакета, которых больше нет в деке]} — для проверки пакетов каталога."""
    ks, res = set(keys(repo)), {}
    for f in sorted((Path(repo) / "catalog" / "langs").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        bad = [k for k in d.get("strings", {}) if k not in ks]
        dirs = plugin_dirs(repo)
        for pid, tr in (d.get("plugins") or {}).items():
            pk = set(plugin_keys(dirs[pid])) if pid in dirs else set()
            bad += [f"{pid}: {k}" for k in tr if k not in pk]
        res[f.stem] = sorted(bad)
    return res


if __name__ == "__main__":
    root = Path(sys.argv[2]) if len(sys.argv) > 2 else Path(__file__).resolve().parent.parent
    if sys.argv[1:2] == ["keys"]:
        print(json.dumps(keys(root), ensure_ascii=False, indent=1))
    elif sys.argv[1:2] == ["plugins"]:
        print(json.dumps({pid: plugin_keys(p) for pid, p in plugin_dirs(Path(__file__).resolve().parent.parent).items()},
                         ensure_ascii=False, indent=1))
    else:
        print("python server/langs.py keys [папка репозитория] — все английские строки дека (JSON) для перевода\n"
              "python server/langs.py plugins — английские строки плагинов из репозитория: {id: [...]}")
