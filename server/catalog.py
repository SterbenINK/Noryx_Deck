"""Каталог (центр обновлений): плагины и моды из папки catalog/ репозитория — поставить, обновить, откатить.

Плагин (catalog/plugins/<id>/plugin.py → plugins/<id>/) — своя программа на ПК: лампа, игры, монитор. Мод
(catalog/mods/<id>/mod.json → mods/<id>/) — изменение самого дека: вид (CSS, переменные) и функции страницы (JS),
см. deckmods.py. Что есть — catalog/index.json в ветке main (собирает `python server/catalog.py index`, свежесть сверяет
tests/test_catalog.py): у каждого имя, описание, версия, что нового (CHANGES), у плагина ещё API / PERMS / OS, и список
файлов с размером и SHA-256 (без game-mod/, архивов и кэша). Дек берёт индекс и файлы того же коммита main через
API GitHub (токен — тот же, что у обновления дека), сверяет SHA-256 и раскладывает в свою папку.

Обновление: прежняя версия целиком (файлы + её настройки: у плагина pluginCfg / pluginPerms / вкл, у мода — выбор
cfg.mods) — в <папка>/_backups/<id>/<время>/ (последние KEEP), свои файлы, которых нет в новой версии (history.json
и т. п.), переезжают в новую. «Откатить» — копию назад вместе с настройками (текущая тоже уходит в копии). Новой
версии плагина нужны новые разрешения — ждёт согласия; новый вид мода — «было / стало». Только стандартная библиотека.
"""
import ast
import asyncio
import hashlib
import json
import logging
import os
import re
import shutil
import sys
import time
from pathlib import Path, PurePosixPath

import perms
import update as updmod

log = logging.getLogger("catalog")

ROOT = Path(__file__).resolve().parent.parent
INDEX = "catalog/index.json"
KINDS = {"plugin": ("catalog/plugins", "plugin.py"), "mod": ("catalog/mods", "mod.json")}
ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,39}")
VER_RE = re.compile(r"\d{1,4}(\.\d{1,4}){0,3}")
SHA256_RE = re.compile(r"[0-9a-f]{64}")
STAMP_RE = re.compile(r"\d{8}-\d{6}(-\d+)?")
SKIP_DIRS = {"__pycache__", "game-mod"}           # мод самой игры и кэш — не в деку
SKIP_EXT = (".zip", ".pyc")
MAX_FILE = 16 * 1024 * 1024
MAX_FILES = 200
MAX_TOTAL = 48 * 1024 * 1024
KEEP = 3                                          # копий прежних версий на плагин
CHECK_EVERY = 6 * 3600
OS_NAME = "windows" if sys.platform == "win32" else "linux"
META = ("NAME", "ABOUT", "VERSION", "API", "PERMS", "CHANGES", "OS", "DEFAULT_ON")


# ---------- описание плагина и версии ----------
def meta(path):
    """Заявленное в plugin.py (через ast, без запуска): NAME, ABOUT, VERSION, API, PERMS, CHANGES, OS."""
    out = {}
    try:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return out
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id in META:
            try:
                out[n.targets[0].id] = ast.literal_eval(n.value)
            except ValueError:
                pass
    return out


def vkey(v):
    """«1.2.10» → (1, 2, 10); непонятное — None."""
    v = str(v or "").strip()
    return tuple(int(x) for x in v.split(".")) if VER_RE.fullmatch(v) else None


def newer(a, b):
    """a новее b (b нет / без версии — любая версия новее)."""
    ka, kb = vkey(a), vkey(b)
    return bool(ka) and (kb is None or ka > kb)


def clean_changes(v):
    out = []
    for x in v if isinstance(v, (list, tuple)) else []:
        if isinstance(x, (list, tuple)) and len(x) >= 2 and vkey(x[0]):
            out.append([str(x[0]), str(x[1])[:300], str(x[2] if len(x) > 2 else x[1])[:300]])
    return out[:30]


def text(v, lang):
    if isinstance(v, dict):
        return str(v.get(lang) or v.get("ru") or next(iter(v.values()), ""))
    return str(v or "")


# ---------- индекс (собирается из репозитория) ----------
def mod_files(folder):
    folder = Path(folder)
    # порядок — по строке пути, как на Linux: на Windows Path сравнивается без учёта регистра,
    # и индекс, собранный там, отличался бы от собранного в CI
    for p in sorted(folder.rglob("*"), key=lambda q: q.relative_to(folder).as_posix()):
        rel = p.relative_to(folder)
        if p.is_file() and not (set(rel.parts) & SKIP_DIRS) and not p.name.endswith(SKIP_EXT) and not p.name.startswith("."):
            yield rel.as_posix(), p


def blob(p):
    """Файл как в git (и как его отдаёт GitHub): текст — с LF (на Windows checkout ставит CRLF, * text=auto)."""
    data = Path(p).read_bytes()
    return data if b"\0" in data else data.replace(b"\r\n", b"\n")


def mod_meta(path):
    """mod.json мода: name / about / version / changes и что в нём (vars, css, js)."""
    try:
        m = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}
    return m if isinstance(m, dict) else {}


def build_index(repo=ROOT):
    out = {"v": 2, "plugins": {}, "mods": {}}
    for kind, (src, main) in KINDS.items():
        base = Path(repo) / src
        if not base.is_dir():
            continue
        for d in sorted(base.iterdir(), key=lambda q: q.name):
            if not (d.is_dir() and (d / main).is_file() and ID_RE.fullmatch(d.name)):
                continue
            files = []
            for rel, p in mod_files(d):
                data = blob(p)
                files.append({"p": rel, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()})
            if kind == "plugin":
                m = meta(d / main)
                out["plugins"][d.name] = {"name": m.get("NAME") or d.name, "about": m.get("ABOUT") or "",
                                          "version": str(m.get("VERSION") or ""), "api": m.get("API") or 1,
                                          "perms": [p for p in m.get("PERMS") or [] if p in perms.PERMS],
                                          "os": [o for o in m.get("OS") or [] if o in ("windows", "linux")],
                                          "changes": clean_changes(m.get("CHANGES")), "files": files}
            else:
                m = mod_meta(d / main)
                out["mods"][d.name] = {"name": m.get("name") or d.name, "about": m.get("about") or "",
                                       "version": str(m.get("version") or ""), "code": bool(m.get("js") or (d / "mod.js").is_file()),
                                       "changes": clean_changes(m.get("changes")), "files": files}
    return out


def index_text(repo=ROOT):
    return json.dumps(build_index(repo), ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def safe_file(rel):
    p = PurePosixPath(str(rel))
    return (bool(p.parts) and not p.is_absolute() and ".." not in p.parts and "\\" not in str(rel) and ":" not in str(rel)
            and len(str(rel)) <= 200 and not (set(p.parts) & SKIP_DIRS) and not p.name.startswith("."))


def clean_index(data):
    """Индекс с GitHub — как данные извне: id, пути и хэши проверяем, лишнее выкидываем. → {"plugin:<id>" | "mod:<id>": …}"""
    out = {}
    for kind, sec in (("plugin", "plugins"), ("mod", "mods")):
        items = data.get(sec) if isinstance(data, dict) else None
        for pid, m in (items.items() if isinstance(items, dict) else ()):
            if not (isinstance(pid, str) and ID_RE.fullmatch(pid) and isinstance(m, dict) and vkey(m.get("version"))):
                continue
            files, total = [], 0
            for f in m.get("files") or []:
                if not (isinstance(f, dict) and safe_file(f.get("p")) and SHA256_RE.fullmatch(str(f.get("sha256", "")))
                        and isinstance(f.get("size"), int) and 0 <= f["size"] <= MAX_FILE):
                    files = None
                    break
                total += f["size"]
                files.append({"p": str(f["p"]), "size": f["size"], "sha256": f["sha256"]})
            if not files or len(files) > MAX_FILES or total > MAX_TOTAL or not any(f["p"] == KINDS[kind][1] for f in files):
                continue
            api = m.get("api") if isinstance(m.get("api"), int) else 1
            out[f"{kind}:{pid}"] = {"kind": kind, "id": pid,
                                    "name": m.get("name") if isinstance(m.get("name"), (str, dict)) else pid,
                                    "about": m.get("about") if isinstance(m.get("about"), (str, dict)) else "",
                                    "version": str(m["version"]), "api": api if kind == "plugin" else perms.API_VERSION,
                                    "perms": [p for p in m.get("perms") or [] if p in perms.PERMS] if kind == "plugin" else [],
                                    "os": [o for o in m.get("os") or [] if o in ("windows", "linux")] if kind == "plugin" else [],
                                    "code": bool(m.get("code")) if kind == "mod" else False,
                                    "changes": clean_changes(m.get("changes")), "files": files}
    return out


# ---------- центр обновлений на деке ----------
class Center:
    def __init__(self, deck, root=ROOT):
        self.deck = deck
        self.root = Path(root)
        self.mods, self.sha, self.checked, self.error = {}, "", 0.0, ""     # mods: ключ «вид:id» → запись индекса
        self.busy = {}                 # ключ → install | rollback
        self.lock = asyncio.Lock()
        self.told = set()              # о каких обновлениях уже сказали (ключ@версия)

    @property
    def host(self):
        return self.deck.plugins

    def folder(self, kind):
        return Path(self.host.folder) if kind == "plugin" else Path(self.deck.mods.folder)

    def token(self):
        try:
            return updmod.user_state(self.root).get("token") or None
        except Exception:
            return None

    @staticmethod
    def split(key):
        kind, _, pid = str(key).partition(":")
        return (kind, pid) if kind in KINDS and ID_RE.fullmatch(pid) else (None, None)

    # ---------- что стоит ----------
    def installed(self, key):
        """Версия стоящего: "" — стоит без версии, None — не стоит."""
        kind, pid = self.split(key)
        if kind == "plugin":
            it = self.host.items.get(pid)
            return None if it is None else str(getattr(it, "version", "") or "")
        if kind == "mod":
            m = self.deck.mods.items.get(pid)
            return None if m is None else m.version
        return None

    def backups(self, key):
        kind, pid = self.split(key)
        d = self.folder(kind) / "_backups" / pid if kind else None
        out = []
        if d is not None and d.is_dir():
            for b in d.iterdir():
                m = updmod.read_json(b / "meta.json")
                if b.is_dir() and STAMP_RE.fullmatch(b.name) and isinstance(m, dict):
                    out.append({"name": b.name, "version": str(m.get("version") or ""), "when": m.get("when") or 0})
        return sorted(out, key=lambda x: x["name"], reverse=True)

    def updates(self):
        """Стоящие, у которых в каталоге версия новее (и её можно поставить)."""
        return [k for k, m in self.mods.items()
                if self.installed(k) is not None and newer(m["version"], self.installed(k)) and not self.why(m)]

    def why(self, m):
        if m["kind"] != "plugin":
            return ""
        if m["api"] > perms.API_VERSION:
            return "api_new"
        if m["api"] < perms.API_MIN:
            return "api_old"
        if m["os"] and OS_NAME not in m["os"]:
            return "os"
        return ""

    def msg(self):
        lang = self.host.lang()
        items = []
        keys = set(self.mods)
        for kind in KINDS:
            b = self.folder(kind) / "_backups"
            if b.is_dir():
                keys |= {f"{kind}:{p.name}" for p in b.iterdir() if p.is_dir() and ID_RE.fullmatch(p.name)}
        for key in sorted(keys):
            kind, pid = self.split(key)
            m, have = self.mods.get(key), self.installed(key)
            it = {"id": key, "pid": pid, "kind": kind, "installed": have, "backups": self.backups(key), "busy": self.busy.get(key, "")}
            if m:
                state = "new" if have is None else "update" if newer(m["version"], have) else \
                    "newer" if newer(have, m["version"]) else "same"
                it.update(name=text(m["name"], lang) or pid, about=text(m["about"], lang), version=m["version"],
                          state=state, why=self.why(m), os=m["os"], api=m["api"], code=m["code"],
                          perms=[{"k": k, "label": perms.label(k, lang), "risk": perms.PERMS[k][4]} for k in m["perms"]],
                          changes=[{"v": v, "text": uk if lang == "uk" else ru} for v, ru, uk in m["changes"]
                                   if have is None or newer(v, have)][:8],
                          size=sum(f["size"] for f in m["files"]))
            else:
                if not it["backups"]:
                    continue
                it.update(name=pid, about="", version="", state="local", why="")
            items.append(it)
        items.sort(key=lambda x: (x["state"] != "update", x["installed"] is None, x["name"].lower()))
        return {"t": "catalog", "sha": self.sha[:7], "checked": self.checked, "error": self.error, "os": OS_NAME,
                "items": items, "updates": len(self.updates())}

    async def push(self):
        await self.deck.broadcast(self.msg())

    # ---------- проверка ----------
    async def check(self, quiet=False):
        """Индекс из main. quiet — фоновая проверка: о новых версиях — в центр уведомлений."""
        loop = asyncio.get_running_loop()
        token = self.token()

        def fetch():
            sha = str(updmod.api(f"/commits/{updmod.BRANCH}", token).get("sha", ""))
            if not updmod.SHA.fullmatch(sha):
                raise updmod.UpdateError("bad", "commit")
            return sha, json.loads(updmod.raw(INDEX, sha, token, 4 * 1024 * 1024).decode("utf-8"))
        try:
            sha, data = await loop.run_in_executor(None, fetch)
            self.mods, self.sha, self.error = clean_index(data), sha, ""
        except updmod.UpdateError as e:
            self.error = "private" if e.code == "notfound" and not token else e.code
            log.warning("Каталог плагинов: %s", e)
        except (ValueError, OSError) as e:
            self.error = "bad"
            log.warning("Каталог плагинов: %s", e)
        self.checked = time.time()
        await self.push()
        new = [k for k in self.updates() if f"{k}@{self.mods[k]['version']}" not in self.told]
        if new:
            self.told |= {f"{k}@{self.mods[k]['version']}" for k in new}
            lang = self.host.lang()
            names = ", ".join(f"{text(self.mods[k]['name'], lang)} {self.mods[k]['version']}" for k in new)
            await self.deck.event("update", self.deck.tr("Обновления в каталоге: ", "Оновлення в каталозі: ") + names,
                                  level="update", notify=quiet, link="settings")

    async def loop(self):
        await asyncio.sleep(60)
        while True:
            try:
                await self.check(quiet=True)
            except Exception:
                log.exception("Каталог плагинов")
            await asyncio.sleep(CHECK_EVERY)

    # ---------- копии ----------
    def _current(self, kind, pid):
        f = self.folder(kind)
        if kind == "mod":
            return f / pid if (f / pid / "mod.json").is_file() else None
        if (f / pid / "plugin.py").is_file():
            return f / pid
        return f / f"{pid}.py" if (f / f"{pid}.py").is_file() else None

    def _saved_cfg(self, kind, pid):
        c = self.deck.store.cfg
        if kind == "mod":
            return {"mod": (c.get("mods") or {}).get(pid)}
        return {"pluginCfg": (c.get("pluginCfg") or {}).get(pid), "pluginPerms": (c.get("pluginPerms") or {}).get(pid),
                "on": (c.get("plugins") or {}).get(pid)}

    def _restore_cfg(self, kind, pid, saved):
        c = self.deck.store.cfg
        pairs = (("mod", "mods"),) if kind == "mod" else (("pluginCfg", "pluginCfg"), ("pluginPerms", "pluginPerms"), ("on", "plugins"))
        for key, dkey in pairs:
            v = saved.get(key)
            if v is None:
                c.setdefault(dkey, {}).pop(pid, None)
            else:
                c.setdefault(dkey, {})[pid] = v
        self.deck.store.save()

    def _backup(self, kind, pid, reason):
        """Текущий (файлы + его настройки) → <папка>/_backups/<id>/<время>/. Нет — None."""
        src = self._current(kind, pid)
        if src is None:
            return None
        base = self.folder(kind) / "_backups" / pid
        name = time.strftime("%Y%m%d-%H%M%S")
        n = 1
        while (base / name).exists():
            n += 1
            name = time.strftime("%Y%m%d-%H%M%S") + f"-{n}"
        b = base / name
        (b / "files").mkdir(parents=True)
        if src.is_dir():
            shutil.copytree(src, b / "files" / pid, ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copyfile(src, b / "files" / src.name)
        updmod.write_json(b / "cfg.json", self._saved_cfg(kind, pid), private=True)
        ver = mod_meta(src / "mod.json").get("version") if kind == "mod" else \
            meta(src / "plugin.py" if src.is_dir() else src).get("VERSION")
        updmod.write_json(b / "meta.json", {"version": str(ver or ""), "when": int(time.time()), "reason": reason,
                                            "kind": "dir" if src.is_dir() else "file"})
        for old in sorted((p for p in base.iterdir() if p.is_dir()), key=lambda p: p.name)[:-KEEP]:
            shutil.rmtree(old, ignore_errors=True)
        return b

    def _drop_current(self, kind, pid):
        shutil.rmtree(self.folder(kind) / pid, ignore_errors=True)
        if kind == "plugin":
            (self.folder(kind) / f"{pid}.py").unlink(missing_ok=True)

    async def _stop(self, kind, pid):
        if kind != "plugin":
            return
        it = self.host.items.get(pid)
        if it is not None:
            await self.host.unload(pid)
            self.host.forget(it)

    async def _rescan(self, kind):
        if kind == "plugin":
            await self.host.scan()
        else:
            await self.deck.mods_scan()

    # ---------- поставить / обновить ----------
    async def install(self, key):
        """"" — готово; иначе причина: busy / none / api_new / api_old / os / net / token / private / rate / bad / io."""
        m = self.mods.get(key)
        if m is None:
            return "none"
        if self.why(m):
            return self.why(m)
        if self.lock.locked():
            return "busy"
        async with self.lock:
            self.busy[key] = "install"
            await self.push()
            try:
                return await self._install(key, m)
            finally:
                self.busy.pop(key, None)
                await self.push()

    async def _install(self, key, m):
        loop = asyncio.get_running_loop()
        kind, pid = m["kind"], m["id"]
        token, sha = self.token(), self.sha
        folder = self.folder(kind)
        stage = folder / "_staging" / pid
        src = KINDS[kind][0]

        def download():
            shutil.rmtree(stage, ignore_errors=True)
            stage.mkdir(parents=True)
            for f in m["files"]:
                data = updmod.raw(f"{src}/{pid}/{f['p']}", sha, token, MAX_FILE)
                if len(data) != f["size"] or hashlib.sha256(data).hexdigest() != f["sha256"]:
                    raise updmod.UpdateError("bad", f["p"])
                out = stage.joinpath(*PurePosixPath(f["p"]).parts)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(data)
        try:
            await loop.run_in_executor(None, download)
        except updmod.UpdateError as e:
            shutil.rmtree(stage, ignore_errors=True)
            log.warning("Каталог: «%s» не скачался: %s", pid, e)
            return e.code
        old = self.installed(key)
        await self._stop(kind, pid)
        new_files = {f["p"] for f in m["files"]}

        def swap():
            cur = folder / pid
            if (cur / KINDS[kind][1]).is_file():       # свои файлы (история, кэш данных) — в новую версию
                for p in cur.rglob("*"):
                    rel = p.relative_to(cur)
                    if p.is_file() and "__pycache__" not in rel.parts and rel.as_posix() not in new_files \
                            and not (stage / rel).exists():
                        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(p, stage / rel)
            self._backup(kind, pid, "update")
            self._drop_current(kind, pid)
            os.replace(stage, cur)
        try:
            await loop.run_in_executor(None, swap)
        except OSError as e:
            log.warning("Каталог: «%s» не встал: %s", pid, e)
            await self._rescan(kind)
            return "io"
        log.info("Каталог: «%s» %s → %s", pid, old if old else "—", m["version"])
        await self._rescan(kind)
        name = text(m["name"], self.host.lang())
        what = (self.deck.tr("Плагин", "Плагін") if kind == "plugin" else self.deck.tr("Мод", "Мод")) + " "
        await self.deck.event("update", what + (self.deck.tr("обновлён: ", "оновлено: ") if old is not None else
                                                self.deck.tr("установлен: ", "встановлено: ")) + f"{name} {m['version']}")
        return ""

    # ---------- откатить ----------
    async def rollback(self, key, name):
        kind, pid = self.split(key)
        if not (kind and STAMP_RE.fullmatch(str(name))):
            return "none"
        b = self.folder(kind) / "_backups" / pid / name
        info = updmod.read_json(b / "meta.json")
        if not (b.is_dir() and isinstance(info, dict) and (b / "files").is_dir()):
            return "none"
        if self.lock.locked():
            return "busy"
        async with self.lock:
            self.busy[key] = "rollback"
            await self.push()
            try:
                await self._stop(kind, pid)
                saved = updmod.read_json(b / "cfg.json") or {}

                def swap():
                    self._backup(kind, pid, "rollback")      # текущая — тоже в копии: можно вернуться вперёд
                    self._drop_current(kind, pid)
                    for p in (b / "files").iterdir():
                        dst = self.folder(kind) / p.name
                        if p.is_dir():
                            shutil.copytree(p, dst)
                        else:
                            shutil.copyfile(p, dst)
                    shutil.rmtree(b, ignore_errors=True)
                await asyncio.get_running_loop().run_in_executor(None, swap)
                self._restore_cfg(kind, pid, saved)
                log.info("Каталог: «%s» откатан к %s", pid, info.get("version") or name)
                await self._rescan(kind)
                await self.deck.event("update", self.deck.tr("Откатил: ", "Відкотив: ") + f"{pid} {info.get('version') or ''}".strip())
                return ""
            except OSError as e:
                log.warning("Каталог: «%s» не откатился: %s", pid, e)
                await self._rescan(kind)
                return "io"
            finally:
                self.busy.pop(key, None)
                await self.push()


if __name__ == "__main__":
    if sys.argv[1:2] == ["index"]:
        repo = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT
        (repo / INDEX).write_text(index_text(repo), encoding="utf-8", newline="\n")
        ix = build_index(repo)
        print("catalog/index.json — плагины:", ", ".join(ix["plugins"]), "· моды:", ", ".join(ix["mods"]) or "—")
    else:
        print("python server/catalog.py index [папка репозитория] — собрать catalog/index.json")
