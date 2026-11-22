"""Обновление дека — с GitHub или из файла, без git и без Claude.

1. Проверка (дек: через 10 с после запуска, потом раз в 6 ч, кнопкой в настройках или в трее). Какой коммит
   сейчас в ветке main и как он соотносится с версией этой установки (server/version.json) — сравнивает сам
   GitHub (compare). Есть новее — всплывающее сообщение на телефоне и у часов. Ставится только то, что ушло
   вперёд от нашей версии: сборка из рабочей ветки, которой ещё нет в main, назад не откатывается.
2. Подготовка (дек, в потоке). Архив исходников этого коммита → то же, что лежит в этой установке:
   переносная Windows — make_zip.py, Linux — make_linux.py, рабочая папка — файлы репозитория.
   Или архив, выбранный на ПК («Обновить из файла»): готовая сборка (Noryx.zip / Noryx-linux.tar.gz)
   или исходники (zip репозитория с GitHub). Результат — update/next/ и план update/plan.json.
3. Помощник — этот же файл отдельным процессом: python update.py apply <pid дека>. Дек запускает его и выходит.
   Помощник ждёт, пока дек закроется, ставит новые библиотеки (pip), откладывает заменяемые файлы в update/prev/
   (кнопка «Вернуть прошлую версию»), раскладывает новые и запускает дек.

Работоспособность новой версии при установке не проверяется: её проверяют до того, как она попадёт в main
(GitHub Actions — .github/workflows/check.yml). Только стандартная библиотека: помощник работает, пока дек
остановлен, и не держит открытыми библиотеки, которые меняет pip. Токен GitHub (если репозиторий приватный)
и пропущенные версии — data/update.json.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import traceback
import urllib.error
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

log = logging.getLogger("update")

ROOT = Path(__file__).resolve().parent.parent
REPO = os.environ.get("DECK_UPDATE_REPO", "SterbenINK/Noryx_Deck")
BRANCH = "main"
API = os.environ.get("DECK_UPDATE_API", "https://api.github.com").rstrip("/")   # проверки подставляют поддельный GitHub
PORT = 8765
IS_WIN = sys.platform == "win32"
NO_WINDOW = 0x08000000                    # CREATE_NO_WINDOW: pip и проверки — без мигающей консоли
DETACHED = 0x00000008 | 0x00000200        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
DEVNULL = subprocess.DEVNULL
SHA = re.compile(r"[0-9a-f]{40}")
MAX_ZIP = 64 * 1024 * 1024
# этого обновление не касается никогда: настройки, звуки, логи, библиотеки, ключ подписи APK
PROTECTED = {"data", "logs", "sounds", ".venv", "venv", "python", "update", ".git", "dist", "__pycache__"}
SECRET = (".jks", ".keystore", ".idsig")
# рабочая папка (как репозиторий) получает всё, кроме проверок и служебных файлов git
DEV_SKIP = {"tests", ".github", ".gitattributes", ".gitignore"}
# библиотеки установки и файл, по которому лаунчер (Start.bat / start.sh) помнит, что они уже стоят
REQ = {"windows": ("requirements.txt", "python/deck-ready.txt"),
       "linux": ("requirements-linux.txt", ".venv/deck-ready.txt"),
       "dev": ("requirements.txt", None) if IS_WIN else ("packaging/linux/requirements-linux.txt", None)}


class UpdateError(Exception):
    """code: net | private | token | rate | http | notfound | bad | build | test | helper | layout"""

    def __init__(self, code, detail=""):
        super().__init__(code + (f": {detail}" if detail else ""))
        self.code, self.detail = code, str(detail)[:300]


# ---------- мелочи ----------
def read_json(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(p, data, private=False):
    """private — файл только для хозяина (Linux: права 600; в Windows папку и так видит лишь её пользователь)."""
    p = Path(p)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    text = json.dumps(data, ensure_ascii=False, indent=1)
    if private:
        _rm(tmp)                                   # права задаются при создании: прежний файл их не унаследует
        with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="utf-8") as f:
            f.write(text)
    else:
        tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, p)


def _rm(p):
    p = Path(p)
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)
    else:
        try:
            p.unlink()
        except FileNotFoundError:
            pass


def _uk(root):
    """Язык дека из data/config.json (помощник работает без дека, а лог обновления — тоже на его языке)."""
    try:
        return read_json(Path(root) / "data" / "config.json", {})["cfg"]["lang"] == "uk"
    except Exception:
        return False


def note(root, ru, uk=None):
    """Строка в logs/update.log — там вся история обновлений, в том числе то, что делал помощник."""
    text = uk if uk is not None and _uk(root) else ru
    p = Path(root) / "logs" / "update.log"
    try:
        p.parent.mkdir(exist_ok=True)
        if p.exists() and p.stat().st_size > 1_000_000:
            os.replace(p, p.with_name("update.log.1"))
        with open(p, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + str(text).rstrip() + "\n")
    except OSError:
        pass


def _last_error(out):
    """Самая содержательная строка вывода: последняя «…Error: …», иначе просто последняя."""
    lines = [s.strip() for s in str(out).splitlines() if s.strip()]
    errs = [s for s in lines if re.match(r"^[\w.]*(Error|Exception)\b", s) or s.startswith("ERROR")]
    return ((errs or lines or ["?"])[-1])[:240]


def _py(name):
    exe = Path(sys.executable)
    w = exe.with_name(name)
    return str(w if IS_WIN and w.exists() else exe)


def console_python():
    """python.exe рядом с текущим (у pythonw нет вывода) — для pip, сборки и проверки."""
    return _py("python.exe")


def gui_python():
    """pythonw.exe — чтобы дек и помощник работали без окна консоли."""
    return _py("pythonw.exe")


def _run(cmd, cwd, timeout, env=None):
    r = subprocess.run(cmd, cwd=str(cwd), env=env, stdin=DEVNULL, capture_output=True, timeout=timeout,
                       creationflags=NO_WINDOW if IS_WIN else 0)
    return r.returncode, (r.stdout + r.stderr).decode("utf-8", "replace")


def _detached(cmd, cwd):
    kw = {"cwd": str(cwd), "stdin": DEVNULL, "stdout": DEVNULL, "stderr": DEVNULL, "close_fds": True}
    if IS_WIN:
        kw["creationflags"] = DETACHED
    else:
        kw["start_new_session"] = True
    return subprocess.Popen(cmd, **kw)


# ---------- что за установка и какая версия ----------
def layout(root=ROOT):
    """dev — рабочая папка (как репозиторий: install.bat), windows — переносная (Start.bat), linux — архив
    для Linux (start.sh), git — клон (обновляется через git pull, сами не трогаем), None — непонятно что."""
    root = Path(root)
    if (root / ".git").exists():
        return "git"
    if (root / "install.bat").exists():
        return "dev"
    if (root / "start.sh").exists() and (root / "requirements-linux.txt").exists():
        return "linux"
    if (root / "server" / "setup.bat").exists():
        return "windows"
    return None


def local_version(root=ROOT):
    v = read_json(Path(root) / "server" / "version.json")
    return v if isinstance(v, dict) and SHA.fullmatch(str(v.get("sha", ""))) else None


def build_version(deck):
    """Что записать в server/version.json при сборке архива: DECK_VERSION (так собирает обновление)
    или данные git, если архив собирают из клона."""
    env = os.environ.get("DECK_VERSION")
    if env:
        try:
            v = json.loads(env)
        except ValueError:
            return None
        return v if isinstance(v, dict) and SHA.fullmatch(str(v.get("sha", ""))) else None

    def git(*a):
        return subprocess.run(["git", "-C", str(deck), *a], capture_output=True, text=True, encoding="utf-8",
                              timeout=15, check=True).stdout.strip()
    try:
        sha, date, msg = (git("log", "-1", "--format=%H%n%cI%n%s").split("\n") + ["", ""])[:3]
        if not SHA.fullmatch(sha):
            return None
        return {"sha": sha, "date": date, "msg": msg[:120], "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
                "dirty": bool(git("status", "--porcelain", "--untracked-files=no"))}
    except Exception:
        return None


def downloads_dir():
    """Папка «Загрузки» (туда браузер кладёт скачанный архив), иначе — домашняя."""
    home = Path.home()
    if not IS_WIN:
        try:
            out = subprocess.run(["xdg-user-dir", "DOWNLOAD"], capture_output=True, text=True, timeout=5).stdout.strip()
            if out and Path(out).is_dir() and Path(out) != home:
                return out
        except Exception:
            pass
    d = home / "Downloads"
    return str(d if d.is_dir() else home)


def user_state(root=ROOT):
    p = Path(root) / "data" / "update.json"
    if os.name != "nt":
        try:                                       # в файле токен GitHub: записан прежней версией — закрываем от других
            if p.stat().st_mode & 0o077:
                os.chmod(p, 0o600)
        except OSError:
            pass                                   # файла ещё нет или он не наш — права поправит следующая запись
    s = read_json(p, {})
    return s if isinstance(s, dict) else {}


def save_user_state(root=ROOT, **kw):
    s = user_state(root)
    s.update(kw)
    write_json(Path(root) / "data" / "update.json", s, private=True)


def skip(root, sha, on=True):
    """Версия, которую не ставить самим (не встала или её откатили). Кнопкой «Обновить» — можно."""
    lst = [x for x in user_state(root).get("skip") or [] if x != sha]
    if on:
        lst.append(sha)
    save_user_state(root, skip=lst[-20:])


# ---------- GitHub ----------
def _open(url, token=None, timeout=20, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": "Noryx",
                                               "X-GitHub-Api-Version": "2022-11-28"})
    if token:
        req.add_unredirected_header("Authorization", f"Bearer {token}")   # в редирект на codeload токен не уходит
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        if e.code == 429 or (e.code == 403 and e.headers.get("X-RateLimit-Remaining") == "0"):
            raise UpdateError("rate", e.code) from None
        code = {401: "token", 404: "notfound"}.get(e.code) or ("token" if e.code == 403 and token else "http")
        raise UpdateError(code, e.code) from None
    except (urllib.error.URLError, OSError) as e:
        raise UpdateError("net", getattr(e, "reason", e)) from None


def api(path, token=None):
    with _open(f"{API}/repos/{REPO}{path}", token) as r:
        return json.loads(r.read(8_000_000).decode("utf-8"))


def raw(path, ref, token=None, limit=MAX_ZIP):
    """Файл репозитория как есть (каталог: catalog/index.json, файлы плагинов и модов) на коммите ref."""
    from urllib.parse import quote
    with _open(f"{API}/repos/{REPO}/contents/{quote(path)}?ref={quote(ref)}", token, timeout=30,
               accept="application/vnd.github.raw") as r:
        data = r.read(limit + 1)
    if len(data) > limit:
        raise UpdateError("bad", f"{path}: больше {limit} байт")
    return data


def title(message):
    """Заголовок коммита; у слияния pull request — название PR (вторая строка), а не «Merge pull request #…»."""
    lines = [s.strip() for s in str(message or "").splitlines() if s.strip()]
    if len(lines) > 1 and re.match(r"Merge (pull request|branch|remote-tracking)", lines[0]):
        return lines[1][:120]
    return (lines[0] if lines else "")[:120]


def _commit(c):
    cm = c.get("commit") or {}
    return {"sha": str(c.get("sha", "")), "date": str((cm.get("committer") or {}).get("date", "")),
            "msg": title(cm.get("message"))}


def check(token=None, local=None):
    """→ {"state": latest | ahead | behind | diverged | unknown, "remote": {sha, date, msg},
    "news": заголовки новых коммитов (свежие сверху), "count": сколько их}."""
    try:
        remote = _commit(api(f"/commits/{BRANCH}", token))
    except UpdateError as e:
        if e.code == "notfound":          # приватный репозиторий без токена (или токен без доступа к нему)
            raise UpdateError("token" if token else "private") from None
        raise
    if not SHA.fullmatch(remote["sha"]):
        raise UpdateError("bad", "commit")
    res = {"state": "unknown", "remote": remote, "news": [], "count": 0}
    if not local:
        return res
    if local["sha"] == remote["sha"]:
        res["state"] = "latest"
        return res
    try:
        cmp = api(f"/compare/{local['sha']}...{remote['sha']}", token)
    except UpdateError as e:
        if e.code == "notfound":          # нашей версии на GitHub нет (собрана из незапушенного) — «неизвестно»
            return res
        raise
    res["state"] = {"ahead": "ahead", "behind": "behind", "identical": "latest",
                    "diverged": "diverged"}.get(cmp.get("status"), "unknown")
    if res["state"] == "ahead":
        res["count"] = int(cmp.get("ahead_by") or 0)
        msgs = [(c.get("commit") or {}).get("message") or "" for c in cmp.get("commits") or []]
        res["news"] = [title(m) for m in reversed(msgs) if m and not m.startswith("Merge ")][:8]
        res["changes"] = remote_changes(remote["sha"], token)
    return res


def remote_changes(sha, token=None):
    """Пункты лога изменений новой версии, которых нет в установленной (server/changelog.py с GitHub).
    Нет файла / сети / разбора — пусто: обновлению это не мешает."""
    try:
        import changelog
        return changelog.newer(changelog.parse(raw("server/changelog.py", sha, token, 512 * 1024).decode("utf-8")))
    except Exception as e:
        log.info("Лог изменений новой версии не получен: %s", e)
        return []


def download(sha, token, dest):
    with _open(f"{API}/repos/{REPO}/zipball/{sha}", token, timeout=60) as r, open(dest, "wb") as f:
        size = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_ZIP:
                raise UpdateError("bad", "архив больше 64 МБ")
            f.write(chunk)


# ---------- посылка: новая версия в том виде, в каком она лежит в этой установке ----------
def safe_rel(rel):
    """Путь внутри установки, который обновлению можно трогать."""
    p = PurePosixPath(rel)
    return (bool(p.parts) and not p.is_absolute() and ".." not in p.parts and p.parts[0] not in PROTECTED
            and "__pycache__" not in p.parts and not rel.endswith(SECRET) and ":" not in rel and "\\" not in rel)


def _out(base, rel):
    p = PurePosixPath(rel)
    if not rel or p.is_absolute() or ".." in p.parts or ":" in rel or "\\" in rel:
        raise UpdateError("bad", rel)
    out = Path(base).joinpath(*p.parts)
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


def unpack_src(zpath, dest):
    """zipball GitHub: сверху одна папка «владелец-репо-sha» — её содержимое в dest."""
    with zipfile.ZipFile(zpath) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len({i.filename.split("/", 1)[0] for i in infos}) != 1:
            raise UpdateError("bad", "архив")
        for i in infos:
            rel = i.filename.split("/", 1)[1] if "/" in i.filename else ""
            mode = i.external_attr >> 16
            if not rel or (mode & 0o170000) == 0o120000:       # символические ссылки дек не использует
                continue
            out = _out(dest, rel)
            with z.open(i) as s, open(out, "wb") as f:
                shutil.copyfileobj(s, f)
            if mode & 0o111 and not IS_WIN:
                os.chmod(out, 0o755)


def build_payload(src, lay, version, nxt, root=ROOT):
    """src — исходники из архива GitHub; nxt — файлы установки этого вида, как после распаковки сборки."""
    root, upd = Path(root), Path(nxt).parent
    env = dict(os.environ, DECK_VERSION=json.dumps(version, ensure_ascii=False), PYTHONDONTWRITEBYTECODE="1",
               PYTHONUTF8="1")

    def make(kind, out):
        script = src / "packaging" / kind / ("make_zip.py" if kind == "windows" else "make_linux.py")
        rc, txt = _run([console_python(), str(script), str(out)], src, 300, env)
        tail = "" if rc == 0 else "\n" + txt[-2000:]
        note(root, f"сборка ({kind}): код {rc}{tail}", f"збирання ({kind}): код {rc}{tail}")
        if rc != 0 or not Path(out).is_file():
            raise UpdateError("build", _last_error(txt))

    if lay == "windows":
        make("windows", upd / "build.zip")
        with zipfile.ZipFile(upd / "build.zip") as z:
            for i in z.infolist():
                if not i.is_dir():
                    _out(nxt, i.filename).write_bytes(z.read(i))
    elif lay == "linux":
        make("linux", upd / "build.tar.gz")
        with tarfile.open(upd / "build.tar.gz") as t:
            for m in t.getmembers():
                if m.isfile() and "/" in m.name:
                    out = _out(nxt, m.name.split("/", 1)[1])
                    out.write_bytes(t.extractfile(m).read())
                    if not IS_WIN:
                        os.chmod(out, (m.mode & 0o777) or 0o644)
    elif lay == "dev":
        for p in sorted(src.rglob("*")):
            rel = p.relative_to(src).as_posix()
            if p.is_file() and rel.split("/", 1)[0] not in DEV_SKIP:
                _copy(p, _out(nxt, rel))
        for name, old, kind in (("Noryx.zip", "ClaudeDeck.zip", "windows"),
                                ("Noryx-linux.tar.gz", "ClaudeDeck-linux.tar.gz", "linux")):
            if (root / name).is_file() or (root / old).is_file():   # архивы для друзей лежат в рабочей папке — соберём свежие
                make(kind, Path(nxt) / name)
    else:
        raise UpdateError("layout", lay)
    write_json(Path(nxt) / "server" / "version.json", version)


def manifest(nxt):
    nxt = Path(nxt)
    return sorted(r for r in (p.relative_to(nxt).as_posix() for p in nxt.rglob("*") if p.is_file()) if safe_rel(r))


def _req_lines(p):
    try:
        lines = Path(p).read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return None
    return [s.split("#", 1)[0].strip() for s in lines if s.split("#", 1)[0].strip()]


def req_plan(lay, nxt, root=ROOT):
    """Меняются ли библиотеки: новый список против того, что уже стоит (метка лаунчера или файл установки)."""
    path, stamp = REQ[lay]
    new = _req_lines(Path(nxt) / path)
    if new is None:
        return {"path": path, "stamp": stamp, "changed": False}
    st = Path(root) / stamp if stamp else None
    have = _req_lines(st) if st and st.exists() else _req_lines(Path(root) / path)
    return {"path": path, "stamp": stamp, "changed": have != new}


def prepare(info, token, lay, root=ROOT, progress=None):
    """Скачать и собрать новую версию в update/next/, записать план для помощника. Ничего в установке не меняет."""
    progress = progress or (lambda stage: None)
    root, remote = Path(root), info["remote"]
    upd = root / "update"
    for name in ("src", "next", "src.zip", "build.zip", "build.tar.gz", "plan.json"):
        _rm(upd / name)
    upd.mkdir(parents=True, exist_ok=True)
    note(root, f"обновление до {remote['sha'][:7]} «{remote['msg']}» ({lay}): скачиваю",
         f"оновлення до {remote['sha'][:7]} «{remote['msg']}» ({lay}): завантажую")
    progress("download")
    download(remote["sha"], token, upd / "src.zip")
    unpack_src(upd / "src.zip", upd / "src")
    src = upd / "src"
    miss = [n for n in ("server/deck.py", "server/update.py", "client/index.html") if not (src / n).is_file()]
    if miss:
        raise UpdateError("bad", ", ".join(miss))
    progress("build")
    version = {"sha": remote["sha"], "date": remote["date"], "msg": remote["msg"], "branch": BRANCH}
    build_payload(src, lay, version, upd / "next", root)
    return write_plan(root, lay, version)


def write_plan(root, lay, version):
    """План для помощника: что разложить (update/next/) и меняются ли библиотеки."""
    root = Path(root)
    upd, nxt = root / "update", root / "update" / "next"
    files = manifest(nxt)
    req = req_plan(lay, nxt, root)
    plan = {"version": version, "layout": lay, "files": files, "req": req,
            "python": console_python(), "pythonw": gui_python(), "old": local_version(root), "time": time.time()}
    write_json(upd / "plan.json", plan)
    note(root, f"посылка готова: файлов {len(files)}, библиотеки {'новые' if req['changed'] else 'те же'}",
         f"посилка готова: файлів {len(files)}, бібліотеки {'нові' if req['changed'] else 'ті самі'}")
    return plan


# ---------- обновление из файла ----------
MAX_UNPACKED = 256 * 1024 * 1024
ARCHIVE_EXTS = (".zip", ".tar.gz", ".tgz")


def _open_archive(arc):
    """→ (открытый архив, [(путь, размер, права, читалка)] его файлов, комментарий zip). Ссылки и папки — мимо."""
    name = str(arc).lower()
    if name.endswith(".zip"):
        z = zipfile.ZipFile(arc)
        files = [(i.filename, i.file_size, i.external_attr >> 16, (lambda i=i: z.read(i)))
                 for i in z.infolist() if not i.is_dir() and ((i.external_attr >> 16) & 0o170000) != 0o120000]
        return z, files, z.comment.decode("ascii", "replace").strip()
    if name.endswith((".tar.gz", ".tgz")):
        t = tarfile.open(arc, "r:gz")
        return t, [(m.name, m.size, m.mode, (lambda m=m: t.extractfile(m).read())) for m in t.getmembers() if m.isfile()], ""
    raise UpdateError("file", "zip / tar.gz")


def unpack_file(arc, dest):
    """Архив → dest. Одна общая папка сверху (Noryx/, Code_deck-main/) снимается.
    → (что это: source | windows | linux, комментарий zip — у архивов GitHub там коммит)."""
    try:
        handle, files, comment = _open_archive(arc)
    except UpdateError:
        raise
    except Exception as e:
        raise UpdateError("file", f"{type(e).__name__}: {e}") from None
    with handle:
        if not files:
            raise UpdateError("file", "пустой архив")
        if sum(f[1] for f in files) > MAX_UNPACKED:
            raise UpdateError("file", "больше 256 МБ")
        names = [f[0][2:] if f[0].startswith("./") else f[0] for f in files]
        tops = {n.split("/", 1)[0] for n in names}
        strip = len(tops) == 1 and all("/" in n for n in names)
        for (_, _, mode, read), n in zip(files, names):
            rel = n.split("/", 1)[1] if strip else n
            if not rel:
                continue
            out = _out(dest, rel)
            out.write_bytes(read())
            if not IS_WIN:
                os.chmod(out, 0o755 if mode & 0o111 else 0o644)
    dest = Path(dest)
    if not (dest / "server" / "deck.py").is_file() or not (dest / "client" / "index.html").is_file():
        raise UpdateError("file", "внутри нет дека")
    if (dest / "packaging").is_dir():
        kind = "source"
    elif (dest / "start.sh").is_file() and (dest / "requirements-linux.txt").is_file():
        kind = "linux"
    elif (dest / "server" / "setup.bat").is_file():
        kind = "windows"
    else:
        raise UpdateError("file", "непонятная сборка")
    return kind, comment


def prepare_file(arc, lay, root=ROOT, progress=None):
    """Архив, выбранный на ПК, → update/next/ и план, как у обновления с GitHub.
    Готовая сборка годится только для своей ОС; исходники собираются под эту установку."""
    progress = progress or (lambda stage: None)
    root, arc = Path(root), Path(arc)
    upd = root / "update"
    for name in ("src", "next", "src.zip", "build.zip", "build.tar.gz", "plan.json"):
        _rm(upd / name)
    upd.mkdir(parents=True, exist_ok=True)
    note(root, f"обновление из файла {arc}", f"оновлення з файлу {arc}")
    progress("build")
    kind, comment = unpack_file(arc, upd / "src")
    src, nxt = upd / "src", upd / "next"
    old = read_json(src / "server" / "version.json")
    version = old if isinstance(old, dict) and SHA.fullmatch(str(old.get("sha", ""))) else None
    if kind == "source":
        if not version:          # zip исходников с GitHub: в комментарии архива — коммит
            version = {"sha": comment if SHA.fullmatch(comment) else "", "date": "", "msg": arc.name, "branch": ""}
        build_payload(src, lay, version, nxt, root)
    elif kind != lay:
        raise UpdateError("kind", {"windows": "Windows", "linux": "Linux"}[kind])
    else:
        os.replace(src, nxt)
        version = version or {"sha": "", "date": "", "msg": arc.name, "branch": ""}
        write_json(nxt / "server" / "version.json", version)
    version = dict(version, file=arc.name)
    return write_plan(root, lay, version)


def helper_running(root=ROOT):
    """Помощник прошлого обновления ещё работает (ждёт, пока новый дек поднимется, прибирает за собой).
    Пока он не закончил, новое обновление не начинаем: он чистит update/ и мог бы забрать чужую посылку."""
    try:
        pid = int((Path(root) / "update" / "helper.pid").read_text().strip())
    except (OSError, ValueError):
        return False
    return pid != os.getpid() and pid_alive(pid)


def spawn_helper(mode, root=ROOT, wait=15):
    """Запустить помощника и дождаться, что он ожил: только после этого дек выходит."""
    upd = Path(root) / "update"
    upd.mkdir(parents=True, exist_ok=True)
    if helper_running(root):
        return False
    flag = upd / "helper.pid"
    _rm(flag)
    _detached([gui_python(), "-I", str(Path(root) / "server" / "update.py"), mode, str(os.getpid())], root)
    end = time.monotonic() + wait
    while time.monotonic() < end:
        if flag.exists():
            return True
        time.sleep(0.1)
    return False


# ---------- помощник: работает, пока дек остановлен ----------
def pid_alive(pid):
    if not pid:
        return False
    if IS_WIN:
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000 | 0x1000, False, int(pid))     # SYNCHRONIZE | QUERY_LIMITED_INFORMATION
        if not h:
            return False
        try:
            return k.WaitForSingleObject(h, 0) == 0x102            # WAIT_TIMEOUT — ещё работает
        finally:
            k.CloseHandle(h)
    try:
        st = Path(f"/proc/{pid}/stat").read_text()
        if st.rsplit(")", 1)[-1].split()[0] == "Z":                # зомби — процесс уже закончился
            return False
    except (OSError, IndexError):
        pass
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _is_deck(pid):
    """Этот pid — всё ещё процесс питона (дек), а не случайный, получивший тот же номер."""
    try:
        if IS_WIN:
            import ctypes
            from ctypes import wintypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x1000, False, int(pid))                 # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            try:
                buf, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
                if not k.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
                    return False
                return Path(buf.value).name.lower() in ("python.exe", "pythonw.exe")
            finally:
                k.CloseHandle(h)
        return b"deck.py" in Path(f"/proc/{pid}/cmdline").read_bytes()
    except Exception:
        return False


def kill_pid(pid):
    """Завершить зависший при выходе дек (он уже всё сохранил и закрыл порт)."""
    try:
        if IS_WIN:
            import ctypes
            k = ctypes.windll.kernel32
            h = k.OpenProcess(0x0001, False, int(pid))                 # PROCESS_TERMINATE
            if h:
                try:
                    k.TerminateProcess(h, 0)
                finally:
                    k.CloseHandle(h)
        else:
            import signal
            os.kill(pid, signal.SIGKILL)
    except Exception:
        pass


def port_free():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if not IS_WIN:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        s.bind(("0.0.0.0", PORT))
        return True
    except OSError:
        return False
    finally:
        s.close()


def deck_alive():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{PORT}/", timeout=2) as r:
            head = r.read(4096)
            return b"Noryx" in head or b"Claude Deck" in head    # прежнее название — у ещё не обновлённых
    except Exception:
        return False


def _wait(cond, seconds):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.3)
    return cond()


def start_deck(root, exe=None):
    return _detached([exe or gui_python(), str(Path(root) / "server" / "deck.py"), "--restart"], root)


def pip_install(root, py, req_file):
    rc, out = _run([py, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location",
                    "--only-binary=:all:", "-r", str(req_file)], root, 900)
    note(root, f"pip ({Path(req_file).name}): код {rc}\n{out[-3000:]}", f"pip ({Path(req_file).name}): код {rc}\n{out[-3000:]}")
    return rc == 0, _last_error(out)


def result(root, ok, kind, error=None, detail="", version=None, old=None, final=True):
    """Итог для дека: он покажет его на телефоне после запуска. Удачный пишется ещё до запуска нового дека
    (final=False) — тот прочтёт его при старте; не поднимется — помощник перепишет итог на неудачу."""
    write_json(Path(root) / "update" / "result.json",
               {"ok": ok, "kind": kind, "error": error, "detail": str(detail)[:300], "version": version, "old": old,
                "time": time.time(), "shown": False})
    if final:
        why = f"({kind}{', ' + error if error else ''}) {detail}".rstrip()
        note(root, f"итог: {'готово' if ok else 'НЕ ВЫШЛО'} {why}", f"підсумок: {'готово' if ok else 'НЕ ВИЙШЛО'} {why}")


class Backup:
    """Прошлая версия заменённых файлов: update/prev/files/… и опись update/prev/backup.json.
    Прежний откат хранится в prev.keep/, пока новое обновление не встанет."""

    def __init__(self, root, version, req):
        self.root, upd = Path(root), Path(root) / "update"
        self.prev, self.keep = upd / "prev", upd / "prev.keep"
        _rm(self.keep)
        if self.prev.exists():
            os.replace(self.prev, self.keep)
        (self.prev / "files").mkdir(parents=True)
        self.data = {"version": version, "replaced": [], "added": [], "removed": [],
                     "manifest": read_json(upd / "installed.json", []), "req": req, "time": time.time()}
        self.save()

    def save(self):
        write_json(self.prev / "backup.json", self.data)

    def stash(self, rel):
        _put(self.root / rel, _out(self.prev / "files", rel))

    def restore(self):
        restore_from(self.root, self.prev, self.data)

    def commit(self):
        _rm(self.keep)

    def abort(self):
        _rm(self.prev)
        if self.keep.exists():
            os.replace(self.keep, self.prev)


def _copy(src, dst):
    """Копия файла. Не shutil.copy2: на Windows он идёт через CopyFile2, а под Wine её нет — процесс падает."""
    with open(src, "rb") as s, open(dst, "wb") as d:
        shutil.copyfileobj(s, d, 1 << 20)
    if not IS_WIN:
        os.chmod(dst, os.stat(src).st_mode & 0o777)


def _put(src, dst):
    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.name + ".upd-tmp")
    _copy(src, tmp)
    os.replace(tmp, dst)


def _same(a, b):
    try:
        return a.stat().st_size == b.stat().st_size and a.read_bytes() == b.read_bytes()
    except OSError:
        return False


def install_files(root, nxt, files, backup):
    """Разложить посылку: менять только то, что отличается; прежнее — в backup; файлы, которых в новой
    версии нет (по описи прошлого обновления), — убрать."""
    root, nxt, new = Path(root), Path(nxt), set(files)
    for rel in files:
        if not safe_rel(rel):
            continue
        src, dst = nxt / rel, root / rel
        if dst.is_file():
            if _same(src, dst):
                continue
            backup.stash(rel)
            backup.data["replaced"].append(rel)
        elif dst.exists():
            raise UpdateError("copy", f"{rel}: на месте файла — папка")
        else:
            backup.data["added"].append(rel)
        backup.save()
        _put(src, dst)
    for rel in backup.data["manifest"] or []:
        if rel.startswith(KEEP_PLUGINS):          # плагин ушёл из сборки — остаётся у пользователя (свои не трогаем)
            continue
        if rel not in new and safe_rel(rel) and (root / rel).is_file():
            backup.stash(rel)
            backup.data["removed"].append(rel)
            backup.save()
            (root / rel).unlink()


# плагины, которые раньше шли в сборке (лампа до переезда в каталог), — теперь свои у пользователя: файлы, которые прошлый
# помощник убрал как «нет в новой версии», возвращаем из update/prev — один раз (потом их могли удалить нарочно)
KEEP_PLUGINS = "plugins/"


def keep_plugins(root=ROOT):
    """После обновления: вернуть из update/prev/files файлы plugins/…, которые обновление убрало. Вернёт список."""
    root = Path(root)
    prev = root / "update" / "prev"
    data = read_json(prev / "backup.json")
    if not data or data.get("pluginsKept"):
        return []
    back = []
    for rel in data.get("removed") or []:
        if not (safe_rel(rel) and rel.startswith(KEEP_PLUGINS)) or (root / rel).exists():
            continue
        src = _out(prev / "files", rel)
        if src.is_file():
            _put(src, root / rel)
            back.append(rel)
    data["pluginsKept"] = True
    write_json(prev / "backup.json", data)
    if back:
        log.info("Обновление: плагины больше не в сборке — оставил у себя: %s", ", ".join(back))
    return back


def restore_from(root, prev, data):
    root = Path(root)
    for rel in reversed(data.get("added") or []):
        if safe_rel(rel):
            _rm(root / rel)
    for rel in (data.get("replaced") or []) + (data.get("removed") or []):
        f = Path(prev) / "files" / rel
        if safe_rel(rel) and f.is_file():
            _put(f, root / rel)


def _libs_back(root, py, req):
    """Вернуть прежние библиотеки (обновление их меняло, но не встало)."""
    if not req or not req.get("changed") or req.get("old") is None:
        return
    old = Path(root) / "update" / "old-requirements.txt"
    old.write_text(req["old"], encoding="utf-8")
    pip_install(root, py, old)
    if req.get("stamp"):
        st = Path(root) / req["stamp"]
        if req.get("old_stamp") is None:
            _rm(st)
        else:
            st.write_text(req["old_stamp"], encoding="utf-8")
    _rm(old)


def _text(p):
    try:
        return Path(p).read_text(encoding="utf-8-sig")
    except OSError:
        return None


def apply_plan(root):
    root = Path(root)
    upd = root / "update"
    plan, nxt = read_json(upd / "plan.json"), upd / "next"
    if not plan or not nxt.is_dir():
        result(root, False, "update", "noplan")
        start_deck(root)
        return False
    ver, old_ver = plan["version"], plan.get("old")
    py, pyw = plan.get("python") or console_python(), plan.get("pythonw") or gui_python()
    req = dict(plan["req"], old=_text(root / plan["req"]["path"]),
               old_stamp=_text(root / plan["req"]["stamp"]) if plan["req"].get("stamp") else None)

    def give_up(error, detail=""):
        _libs_back(root, py, req)
        skip(root, ver["sha"])
        result(root, False, "update", error, detail, ver, old_ver)
        start_deck(root, pyw)
        return False

    if req["changed"]:
        note(root, "ставлю новые библиотеки", "ставлю нові бібліотеки")
        ok, err = pip_install(root, py, nxt / req["path"])
        if not ok:
            return give_up("pip", err)
    backup = Backup(root, old_ver, req)
    try:
        install_files(root, nxt, plan["files"], backup)
    except Exception as e:
        tb = traceback.format_exc()
        note(root, "файлы не заменились:\n" + tb, "файли не замінилися:\n" + tb)
        backup.restore()
        backup.abort()
        return give_up("copy", e)
    if req.get("stamp") and req["changed"]:
        _put(nxt / req["path"], root / req["stamp"])
    write_json(upd / "installed.json", plan["files"])
    n = backup.data
    note(root, f"файлы на месте: заменено {len(n['replaced'])}, новых {len(n['added'])}, убрано {len(n['removed'])}"
               " — запускаю дек",
         f"файли на місці: замінено {len(n['replaced'])}, нових {len(n['added'])}, прибрано {len(n['removed'])}"
         " — запускаю дек")
    # новую версию не проверяем: её проверили до main (CI); прошлая лежит в update/prev — кнопка «Вернуть»
    backup.commit()
    result(root, True, "update", version=ver, old=old_ver)
    for name in ("src", "next", "src.zip", "build.zip", "build.tar.gz", "plan.json"):
        _rm(upd / name)
    start_deck(root, pyw)
    return True


def rollback(root):
    """«Вернуть прошлую версию»: файлы из update/prev/, прежние библиотеки, запуск."""
    root = Path(root)
    prev = root / "update" / "prev"
    data = read_json(prev / "backup.json")
    if not data:
        result(root, False, "rollback", "noprev")
        start_deck(root)
        return False
    cur = local_version(root)
    restore_from(root, prev, data)
    write_json(root / "update" / "installed.json", data.get("manifest") or [])
    _libs_back(root, console_python(), data.get("req"))
    if cur:
        skip(root, cur["sha"])
    _rm(prev)
    result(root, True, "rollback", version=data.get("version"), old=cur)
    start_deck(root)
    return True


def helper_main(mode, pid, root=ROOT):
    root = Path(root)
    upd = root / "update"
    upd.mkdir(parents=True, exist_ok=True)
    if helper_running(root):               # второй помощник сразу за первым — не мешаем ему
        note(root, f"помощник ({mode}): уже работает другой — выхожу", f"помічник ({mode}): уже працює інший — виходжу")
        return 1
    (upd / "helper.pid").write_text(str(os.getpid()))
    try:
        note(root, f"помощник ({mode}): жду, пока дек закроется", f"помічник ({mode}): чекаю, поки дек закриється")
        if not _wait(lambda: not pid_alive(pid), 20) and port_free() and _is_deck(pid):
            # дек уже всё сохранил и закрыл порт, а процесс не завершается (так бывает под Wine) — добиваем
            note(root, "дек закрыл порт, но процесс не завершился — завершаю его",
                 "дек закрив порт, але процес не завершився — завершую його")
            kill_pid(pid)
        if not _wait(lambda: not pid_alive(pid), 20) or not _wait(port_free, 40):
            note(root, "дек не закрылся — отменяю", "дек не закрився — скасовую")
            result(root, False, mode, "busy")
            return 1
        time.sleep(0.5)
        ok = apply_plan(root) if mode == "apply" else rollback(root)
        return 0 if ok else 1
    except Exception:
        tb = traceback.format_exc()
        note(root, "помощник упал:\n" + tb, "помічник упав:\n" + tb)
        if not deck_alive():          # что бы ни случилось — дек должен работать
            try:
                start_deck(root)
            except Exception as e:      # дек остался выключенным — пусть в истории будет видно почему
                note(root, f"дек не запустился после сбоя помощника: {type(e).__name__}: {e}",
                     f"дек не запустився після збою помічника: {type(e).__name__}: {e}")
        return 1
    finally:
        _rm(upd / "helper.pid")


# ---------- в работающем деке ----------
class Controller:
    """Проверка по расписанию, состояние для телефона и трея, запуск помощника.
    deck: tr, toast, broadcast, send, store.cfg, idle(сек), stop_for(действие), tray."""
    FIRST = float(os.environ.get("DECK_UPDATE_FIRST", 10))    # первая проверка после запуска (сеть успела подняться)
    EVERY = 6 * 3600
    RETRY = 30 * 60
    TICK = float(os.environ.get("DECK_UPDATE_TICK", 300))
    IDLE = float(os.environ.get("DECK_UPDATE_IDLE", 30 * 60))  # «никто не пользуется»: столько без нажатий

    def __init__(self, deck, root=ROOT):
        self.deck, self.root = deck, Path(root)
        self.lay = layout(self.root)
        self.local = local_version(self.root)
        self.info = None
        self.error = None               # (код, подробности) последней неудачи
        self.busy = None                # check | pick | download | build | restart
        self.checked = None
        self.told = set()               # о каких версиях уже сказали в этом запуске
        self.failed = set()             # какие в этом запуске не собрались — сами больше не пробуем
        self.last = read_json(self.root / "update" / "result.json")
        self.announce = None            # (текст, до какого момента показывать подключившимся)

    def can(self):
        return self.lay in ("dev", "windows", "linux")

    def token(self):
        return user_state(self.root).get("token") or None

    def skipped(self, sha):
        return sha in (user_state(self.root).get("skip") or [])

    # ---------- тексты ----------
    def error_text(self):
        if not self.error:
            return None
        code, detail = self.error
        tr = self.deck.tr
        text = {
            "net": tr("Нет связи с GitHub", "Немає зв’язку з GitHub"),
            "private": tr("Репозиторий приватный — нужен токен GitHub", "Репозиторій приватний — потрібен токен GitHub"),
            "token": tr("Токен не подходит или не даёт доступа к репозиторию",
                        "Токен не підходить або не дає доступу до репозиторію"),
            "rate": tr("GitHub ограничил число запросов — попробую позже",
                       "GitHub обмежив кількість запитів — спробую пізніше"),
            "bad": tr("Архив с GitHub не похож на дек", "Архів з GitHub не схожий на дек"),
            "build": tr("Новая версия не собралась", "Нова версія не зібралася"),
            "test": tr("Новая версия не запускается", "Нова версія не запускається"),
            "helper": tr("Не запустился помощник обновления", "Не запустився помічник оновлення"),
            "http": tr("GitHub ответил ошибкой", "GitHub відповів помилкою"),
            "file": tr("Архив не подходит", "Архів не підходить"),
            "kind": tr("Это сборка для другой системы", "Це збірка для іншої системи"),
            "pick": tr("Не открылось окно выбора файла", "Не відкрилося вікно вибору файлу"),
        }.get(code, tr("Ошибка", "Помилка"))
        return text + (f": {detail}" if detail and code in ("build", "test", "http", "bad", "net", "file", "kind", "pick")
                       else "")

    def last_text(self):
        r = self.last
        if not r:
            return None
        tr = self.deck.tr
        msg = (r.get("version") or {}).get("msg") or ""
        if r.get("ok"):
            if r.get("kind") == "rollback":
                return tr("Вернул прошлую версию дека", "Повернув попередню версію дека")
            return tr("Дек обновлён: ", "Дек оновлено: ") + f"«{msg}»"
        why = {"pip": tr("не встали библиотеки", "не встановилися бібліотеки"),
               "test": tr("новая версия не запускается", "нова версія не запускається"),
               "start": tr("новая версия не запустилась", "нова версія не запустилася"),
               "copy": tr("не удалось заменить файлы", "не вдалося замінити файли"),
               "busy": tr("дек не закрылся", "дек не закрився"),
               "noprev": tr("нет прошлой версии", "немає попередньої версії")}.get(r.get("error"), r.get("error") or "?")
        if r.get("kind") == "rollback":
            return tr("Откат не вышел: ", "Відкат не вдався: ") + why
        return tr(f"Обновление не встало ({why}) — работает прежняя версия",
                  f"Оновлення не встало ({why}) — працює попередня версія")

    def msg(self):
        i = self.info or {}
        prev = read_json(self.root / "update" / "prev" / "backup.json")
        remote = i.get("remote")
        return {"t": "upd", "can": self.can(), "layout": self.lay, "repo": REPO, "local": self.local, "state": i.get("state"),
                "remote": remote, "news": i.get("news") or [], "count": i.get("count") or 0, "busy": self.busy,
                "changes": i.get("changes") or [],
                "error": self.error_text(), "needToken": bool(self.error and self.error[0] in ("private", "token")),
                "token": bool(self.token()), "hasPrev": bool(prev), "prev": (prev or {}).get("version"),
                "checked": self.checked, "skipped": bool(remote and self.skipped(remote["sha"])),
                "last": self.last_text(), "lastOk": bool(self.last and self.last.get("ok")),
                "lastTime": (self.last or {}).get("time")}

    def tray_label(self):
        """Пункт трея есть всегда: «Обновить дек» проверит GitHub и, если есть новее, поставит."""
        i, tr = self.info or {}, self.deck.tr
        if not self.can():
            return None                 # клон git и непонятные папки дек сам не обновляет — пункта нет
        if self.busy:
            return tr("Обновление…", "Оновлення…")
        if i.get("state") != "ahead":
            return tr("Обновить дек", "Оновити дек")
        m = i["remote"]["msg"]
        return tr("Обновить дек: ", "Оновити дек: ") + (m if len(m) <= 40 else m[:39] + "…")

    def tray_notify(self, text):
        t = getattr(self.deck, "tray", None)
        if t and hasattr(t, "notify"):
            try:
                t.notify(text)
            except Exception:
                log.exception("уведомление трея")

    async def push(self):
        await self.deck.broadcast(self.msg())
        t = getattr(self.deck, "tray", None)
        if t and hasattr(t, "refresh"):
            t.refresh()

    # ---------- жизнь ----------
    def start(self):
        """Из on_startup дека: показать итог прошлого обновления и запустить проверки."""
        r = self.last
        if r and not r.get("shown"):
            text = self.last_text()
            self.announce = (text, time.monotonic() + 180)
            r["shown"] = True
            write_json(self.root / "update" / "result.json", r)
            asyncio.get_running_loop().call_later(4, self.tray_notify, text)
            log.info("Обновление: %s", text)
        asyncio.create_task(self.run())

    async def on_client(self, ws):
        if self.announce and time.monotonic() < self.announce[1] and self.deck.store.cfg.get("toasts", True):
            await self.deck.send(ws, {"t": "toast", "text": self.announce[0]})

    async def run(self):
        await self.push()
        if not self.can():
            return
        await asyncio.sleep(self.FIRST)
        first, due = True, 0.0
        while True:
            if time.monotonic() >= due:
                ok = await self.check()
                due = time.monotonic() + (self.EVERY if ok else self.RETRY)
            if self.want_auto(first):
                await self.apply(auto=True)
            else:
                await self.tell()
            first = False
            await asyncio.sleep(self.TICK)

    async def tell(self):
        """Сказать о новой версии (один раз за запуск), если она не ставится сама прямо сейчас — у часов на ПК.
        Телефон показывает подсказку сам, по состоянию upd (и тот, что подключится позже)."""
        i = self.info or {}
        if self.busy or i.get("state") != "ahead" or i["remote"]["sha"] in self.told:
            return
        self.told.add(i["remote"]["sha"])
        self.tray_notify(self.deck.tr(f"Доступно обновление: «{i['remote']['msg']}». Обновить — в трее или в настройках",
                                      f"Доступне оновлення: «{i['remote']['msg']}». Оновити — у треї або в налаштуваннях"))

    def want_auto(self, first):
        i = self.info or {}
        if self.busy or i.get("state") != "ahead" or self.deck.store.cfg.get("updAuto", True) is False:
            return False
        sha = i["remote"]["sha"]
        if sha in self.failed or self.skipped(sha):
            return False
        return first or self.deck.idle(self.IDLE)     # при запуске — сразу, потом — когда деком не пользуются

    async def check(self, manual=False, notify=False):
        """manual — нажали «Проверить»: сказать итог на телефоне; notify — ещё и у часов (нажали в трее)."""
        if not self.can() or self.busy:
            return False
        self.busy = "check"
        await self.push()
        ok = False
        try:
            self.local = local_version(self.root)
            self.info = await asyncio.get_running_loop().run_in_executor(None, check, self.token(), self.local)
            self.error, self.checked, ok = None, time.time(), True
        except UpdateError as e:
            self.error = (e.code, e.detail)
            log.warning("Обновления: %s", e)
        except Exception as e:
            self.error = ("http", f"{type(e).__name__}: {e}")
            log.exception("Обновления")
        self.busy = None
        await self.push()
        i = self.info or {}
        if manual:
            if ok and i.get("state") == "ahead":
                self.told.add(i["remote"]["sha"])
            tr = self.deck.tr
            text = self.error_text() if not ok else {
                "latest": tr("Это последняя версия", "Це остання версія"),
                "ahead": tr("Есть новая версия", "Є нова версія"),
                "behind": tr("Эта версия новее, чем в main", "Ця версія новіша, ніж у main"),
            }.get(i.get("state"), tr("В main другая версия", "У main інша версія"))
            await self.deck.toast(text)
            if notify:
                self.tray_notify(text)
        return ok

    async def tray_click(self, notify=True):
        """«Обновить дек» в трее (и «Обновить» в настройках): новая версия уже известна — ставим,
        нет — сначала проверяем. notify — итог проверки ещё и у часов (нажали в трее)."""
        if self.busy or not self.can():
            return
        if (self.info or {}).get("state") != "ahead":
            if not await self.check(manual=True, notify=notify) or (self.info or {}).get("state") != "ahead":
                return
        await self.apply()

    async def _stage(self, stage):
        if self.busy:
            self.busy = stage
            await self.push()

    async def apply(self, auto=False):
        i = self.info or {}
        if self.busy or not self.can() or not i.get("remote") or i.get("state") in ("latest", "behind"):
            return
        sha, tr = i["remote"]["sha"], self.deck.tr
        if helper_running(self.root):
            if not auto:
                await self.deck.toast(tr("Прошлое обновление ещё завершается — попробуй через минуту",
                                         "Попереднє оновлення ще завершується — спробуй за хвилину"))
            return
        if not auto:
            self.failed.discard(sha)
            if self.skipped(sha):
                skip(self.root, sha, on=False)
        log.info("Обновление до %s «%s»%s", sha[:7], i["remote"]["msg"], tr(" (само)", " (саме)") if auto else "")
        await self.install("download", prepare, i, self.token(), self.lay, self.root, sha=sha)

    async def pick_file(self):
        """«Обновить из файла…»: окно выбора архива на ПК, потом — как обычное обновление."""
        tr = self.deck.tr
        if self.busy or not self.can():
            if not self.can():
                await self.deck.toast(tr("Эту папку дека обновлять нельзя", "Цю теку дека оновлювати не можна"))
            return
        if helper_running(self.root):
            await self.deck.toast(tr("Прошлое обновление ещё завершается — попробуй через минуту",
                                     "Попереднє оновлення ще завершується — спробуй за хвилину"))
            return
        self.busy, self.error = "pick", None
        await self.push()
        await self.deck.toast(tr("Открыл на ПК окно выбора архива", "Відкрив на ПК вікно вибору архіву"))
        try:
            path = await asyncio.get_running_loop().run_in_executor(None, self.ask_archive)
        except Exception as e:
            log.exception("Выбор архива обновления")
            path, self.error = None, ("pick", str(e))
        self.busy = None
        await self.push()
        if self.error:
            await self.deck.toast(self.error_text())
        if path:
            await self.apply_file(path)

    def ask_archive(self):
        """Окно выбора файла на ПК (то же, что для программ и звуков), начинает с «Загрузок»."""
        import i18n
        mod = __import__("apps" if IS_WIN else "lx_apps")
        tr = i18n.tr
        files = mod.ask_files(tr("Noryx — выбери архив обновления", "Noryx — обери архів оновлення"),
                              [(tr("Архив дека", "Архів дека"), "*.zip;*.tar.gz;*.tgz"), (tr("Все файлы", "Усі файли"), "*.*")],
                              start=downloads_dir())
        return files[0] if files else None

    async def apply_file(self, path):
        tr = self.deck.tr
        if self.busy or not self.can() or not str(path).lower().endswith(ARCHIVE_EXTS):
            if not self.busy:
                await self.deck.toast(tr("Нужен архив дека: .zip или .tar.gz", "Потрібен архів дека: .zip або .tar.gz"))
            return
        log.info("Обновление из файла: %s", path)
        await self.install("build", prepare_file, str(path), self.lay, self.root)

    async def install(self, stage, job, *args, sha=None):
        """Подготовить посылку (job в потоке), запустить помощника и выйти — дальше он."""
        tr, loop = self.deck.tr, asyncio.get_running_loop()

        def progress(st):
            asyncio.run_coroutine_threadsafe(self._stage(st), loop)
        self.busy, self.error = stage, None
        await self.push()
        try:
            await loop.run_in_executor(None, job, *args, progress)
            self.busy = "restart"
            await self.push()
            if not await loop.run_in_executor(None, spawn_helper, "apply", self.root):
                raise UpdateError("helper")
        except Exception as e:
            if not isinstance(e, UpdateError):
                log.exception("Обновление")
                e = UpdateError("build", f"{type(e).__name__}: {e}")
            log.warning("Обновление не готово: %s", e)
            if sha:
                self.failed.add(sha)
            self.busy, self.error = None, (e.code, e.detail)
            await self.push()
            await self.deck.toast(tr("Обновление не готово: ", "Оновлення не готове: ") + self.error_text())
            return
        await self.deck.toast(tr("Ставлю обновление — дек перезапустится", "Ставлю оновлення — дек перезапуститься"))
        await asyncio.sleep(0.3)
        self.deck.stop_for("update")

    async def rollback(self):
        if self.busy or not (self.root / "update" / "prev" / "backup.json").exists():
            return
        if helper_running(self.root):
            await self.deck.toast(self.deck.tr("Прошлое обновление ещё завершается — попробуй через минуту",
                                               "Попереднє оновлення ще завершується — спробуй за хвилину"))
            return
        self.busy = "restart"
        await self.push()
        if not await asyncio.get_running_loop().run_in_executor(None, spawn_helper, "rollback", self.root):
            self.busy, self.error = None, ("helper", "")
            await self.push()
            return
        await self.deck.toast(self.deck.tr("Возвращаю прошлую версию — дек перезапустится",
                                           "Повертаю попередню версію — дек перезапуститься"))
        await asyncio.sleep(0.3)
        self.deck.stop_for("update")

    async def set_token(self, token):
        token, tr = str(token or "").strip(), self.deck.tr
        if token and not re.fullmatch(r"(github_pat_|ghp_)[A-Za-z0-9_]{20,255}", token):
            await self.deck.toast(tr("Это не похоже на токен GitHub (github_pat_… или ghp_…)",
                                     "Це не схоже на токен GitHub (github_pat_… або ghp_…)"))
            return
        save_user_state(self.root, token=token or None)
        log.info("Токен GitHub для обновлений: %s", tr("задан", "задано") if token else tr("убран", "прибрано"))
        await self.deck.toast(tr("Токен сохранён на ПК", "Токен збережено на ПК") if token
                              else tr("Токен убран", "Токен прибрано"))
        await self.check(manual=bool(token))


if __name__ == "__main__":
    _mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if _mode in ("apply", "rollback"):
        sys.exit(helper_main(_mode, int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0))
    print(__doc__)
