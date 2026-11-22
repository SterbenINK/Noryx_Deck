"""Программы на Linux: меню приложений (.desktop, в т.ч. flatpak и snap), библиотека Steam,
запуск, закрытие, иконки из темы значков, окно выбора файла (kdialog / zenity).

Имя процесса («proc») нужно, чтобы видеть, запущена ли программа, и закрывать её при смене режима:
обычная программа — имя её исполняемого файла («steam», «discord»), flatpak — «flatpak:<id>».
"""
import asyncio
import fcntl
import gzip
import hashlib
import io
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

import psutil

import i18n
import lx_util as U
from store import ROOT

log = logging.getLogger("apps")

ICON_DIR = ROOT / "data" / "icons"
ICON_PX = 192

# эти процессы дек никогда не закрывает: рабочий стол, звук, сам дек, плееры музыки
PROTECTED = {"plasmashell", "kwin_wayland", "kwin_x11", "kwin", "ksmserver", "krunner", "kded6", "kded5",
             "gnome-shell", "dolphin", "nautilus", "systemd", "pipewire", "pipewire-pulse", "wireplumber",
             "xdg-desktop-portal", "python", "python3", "bash", "sh", "konsole",
             "spotify", "youtube-music", "ytmdesktop", "flatpak:com.spotify.client",
             "flatpak:app.ytmdesktop.ytmdesktop", "flatpak:com.github.th_ch.youtube_music"}
SKIP_WORDS = ("uninstall", "удал", "readme", "help", "справк", "website", "license", "лиценз",
              "documentation", "release notes", "changelog")
FIELD_CODES = {"%f", "%F", "%u", "%U", "%d", "%D", "%n", "%N", "%v", "%m"}
WRAPPERS = {"gamemoderun", "prime-run", "mangohud", "optirun", "primusrun", "pvkrun", "switcherooctl", "nice"}
INTERPRETERS = {"python", "python3", "java", "mono", "node", "wine", "wine64", "flatpak-spawn", "perl", "ruby"}
EXEC_FILES = ("*.desktop *.AppImage *.appimage *.sh *.py *.x86_64 *.run")


# ---------- .desktop ----------

def _unescape(v):
    out, i = [], 0
    while i < len(v):
        c = v[i]
        if c == "\\" and i + 1 < len(v):
            n = v[i + 1]
            out.append({"s": " ", "n": "\n", "t": "\t", "r": "\r", "\\": "\\"}.get(n, "\\" + n))
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def read_desktop(path):
    """Ключи группы [Desktop Entry] (с локализованными вариантами вроде Name[uk])."""
    out, group = {}, None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if s.startswith("[") and s.endswith("]"):
                    group = s[1:-1]
                    continue
                if group == "Desktop Entry" and "=" in s:
                    k, v = s.split("=", 1)
                    out.setdefault(k.strip(), v.strip())
    except OSError:
        return None
    return out


def localized(entry, key):
    for tag in U.locale_tags():
        v = entry.get(f"{key}[{tag}]")
        if v:
            return _unescape(v)
    v = entry.get(key)
    return _unescape(v) if v else None


def exec_argv(entry, path=None):
    """Exec=… → список аргументов без %f/%u и прочих кодов."""
    raw = _unescape(entry.get("Exec", ""))
    try:
        toks = shlex.split(raw)
    except ValueError:
        toks = raw.split()
    out = []
    for t in toks:
        if t in FIELD_CODES:
            continue
        if t == "%i":
            if entry.get("Icon"):
                out += ["--icon", entry["Icon"]]
            continue
        if t == "%c":
            out.append(localized(entry, "Name") or "")
            continue
        if t == "%k":
            out.append(str(path or ""))
            continue
        t = re.sub(r"%[fFuUdDnNvmick]", "", t).replace("%%", "%")
        if t:
            out.append(t)
    return out


def proc_from(argv, entry=None):
    """Имя процесса программы по её команде запуска."""
    if entry and entry.get("X-Flatpak"):
        return "flatpak:" + entry["X-Flatpak"].strip()
    toks = list(argv or [])
    while toks:
        b = os.path.basename(toks[0])
        if b == "env":
            toks.pop(0)
            while toks and (toks[0].startswith("-") or ("=" in toks[0] and not toks[0].startswith("/"))):
                toks.pop(0)
            continue
        if b in WRAPPERS:
            toks.pop(0)
            while toks and toks[0].startswith("-"):
                toks.pop(0)
            continue
        if "=" in toks[0] and not toks[0].startswith("/"):       # VAR=value команда
            toks.pop(0)
            continue
        break
    if not toks:
        return None
    b = os.path.basename(toks[0])
    if b == "flatpak" and "run" in toks:
        rest = toks[toks.index("run") + 1:]
        app = next((t for t in rest if not t.startswith("-")), None)
        return f"flatpak:{app}" if app else None
    if b in ("sh", "bash", "dash", "zsh") and "-c" in toks:
        i = toks.index("-c")
        try:
            return proc_from(shlex.split(toks[i + 1])) if i + 1 < len(toks) else None
        except ValueError:
            return None
    if b in INTERPRETERS:
        return None                         # «python»/«java» — по ним не понять, какая это программа
    path = toks[0] if os.path.isabs(toks[0]) else (shutil.which(toks[0]) or toks[0])
    real = os.path.realpath(path) if os.path.exists(path) else path
    name = os.path.basename(real)
    if name in ("snap", "busybox"):
        name = os.path.basename(path)
    return name.lower() or None


def _skip(name):
    low = name.lower()
    return any(w in low for w in SKIP_WORDS)


def _shown(entry):
    if entry.get("Type", "Application") != "Application" or not entry.get("Exec"):
        return False
    if entry.get("NoDisplay", "").lower() == "true" or entry.get("Hidden", "").lower() == "true":
        return False
    cur = U.desktops()
    only = {d.strip().lower() for d in entry.get("OnlyShowIn", "").split(";") if d.strip()}
    if only and not (only & cur):
        return False
    nots = {d.strip().lower() for d in entry.get("NotShowIn", "").split(";") if d.strip()}
    if nots & cur:
        return False
    tryexec = entry.get("TryExec")
    if tryexec and not (os.path.exists(tryexec) if os.path.isabs(tryexec) else shutil.which(tryexec)):
        return False
    return True


def _desktop_files():
    """Файлы меню приложений; у одинаковых id побеждает свой (~/.local) — как в меню рабочего стола."""
    seen = {}
    for base in U.data_dirs():
        root = base / "applications"
        if not root.is_dir():
            continue
        for f in sorted(root.rglob("*.desktop")):
            fid = str(f.relative_to(root)).replace("/", "-")
            seen.setdefault(fid, f)
    return list(seen.values())


def _from_desktop(found):
    for f in _desktop_files():
        e = read_desktop(f)
        if not e or not _shown(e):
            continue
        label = localized(e, "Name") or f.stem
        if _skip(label):
            continue
        names = {label.lower()}
        for k, v in e.items():
            if k == "Name" or k.startswith("Name["):
                names.add(_unescape(v).lower())
        src = "Flatpak" if e.get("X-Flatpak") else ("Snap" if "/snap/" in str(f) else "меню")
        item = {"label": label, "path": str(f), "target": None, "args": "",
                "proc": proc_from(exec_argv(e, f), e), "src": src, "names": sorted(names)}
        found.setdefault(label.lower(), item)


# ---------- Steam ----------

def steam_roots():
    h = U.HOME
    cands = [h / ".local/share/Steam", h / ".steam/steam", h / ".steam/root",
             h / ".var/app/com.valvesoftware.Steam/.local/share/Steam", h / "snap/steam/common/.local/share/Steam"]
    out, seen = [], set()
    for c in cands:
        try:
            r = c.resolve()
        except OSError:
            continue
        if (r / "steamapps").is_dir() and str(r) not in seen:
            seen.add(str(r))
            out.append(r)
    return out


def _from_steam(found):
    for root in steam_roots():
        libs = {root / "steamapps"}
        try:
            txt = (root / "steamapps" / "libraryfolders.vdf").read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(r'"path"\s+"([^"]+)"', txt):
                libs.add(Path(m.group(1).replace("\\\\", "\\")) / "steamapps")
        except OSError:
            pass
        for lib in libs:
            for acf in lib.glob("appmanifest_*.acf"):
                try:
                    t = acf.read_text(encoding="utf-8", errors="replace")
                    appid = re.search(r'"appid"\s+"(\d+)"', t).group(1)
                    name = re.search(r'"name"\s+"([^"]+)"', t).group(1)
                except Exception:
                    continue
                low = name.lower()
                if _skip(name) or low.startswith(("proton", "steam linux runtime", "steamworks", "steam runtime")) \
                        or appid == "228980":
                    continue
                found.setdefault(low, {"label": name, "path": f"steam://rungameid/{appid}", "target": None,
                                       "args": "", "proc": None, "src": "Steam", "appid": appid, "names": [low]})


def _scan_installed():
    found = {}
    for fn in (_from_desktop, _from_steam):
        try:
            fn(found)
        except Exception:
            log.exception("поиск программ: %s", fn.__name__)
    return sorted(found.values(), key=lambda a: a["label"].lower())


_cache = None


async def list_installed(refresh=False):
    global _cache
    if _cache is None or refresh:
        _cache = await asyncio.get_running_loop().run_in_executor(None, _scan_installed)
        log.info("Найдено программ: %d", len(_cache))
    return _cache


def _alive_path(p):
    if not p:
        return False
    if p.startswith(("steam://", "http://", "https://")):
        return True
    return os.path.exists(os.path.expanduser(p))


async def resolve_layouts(layouts):
    """Заполняет path/proc у кнопок, которые ещё не привязаны к программе."""
    installed = await list_installed()
    changed = False
    for items in layouts.values():
        for it in items:
            if it.get("kind") != "app":
                continue
            p = it.get("path")
            if p and not _alive_path(p):
                log.info("Кнопка «%s»: %s больше нет — ищу программу заново", it["label"], p)
                it["path"], it["proc"] = None, None
                changed = True
            if it.get("path"):
                if not it.get("proc"):
                    proc = _proc_for_path(it["path"])
                    if proc:
                        it["proc"] = proc
                        changed = True
                continue
            alts = [a.strip().lower() for a in (it.get("match") or it["label"]).split("|") if a.strip()]
            hit = next((a for alt in alts for a in installed if alt in a.get("names", [a["label"].lower()])), None) or \
                next((a for alt in alts for a in installed if any(alt in n for n in a.get("names", [a["label"].lower()]))), None)
            if hit:
                it["path"], it["proc"] = hit["path"], hit["proc"]
                changed = True
                log.info("Кнопка «%s» → %s", it["label"], hit["path"])
            else:
                log.warning("Кнопка «%s»: программа не найдена — добавь её через «+» → «Выбрать программу на ПК»",
                            it["label"])
    return changed


def _proc_for_path(p):
    p = os.path.expanduser(p)
    if p.endswith(".desktop"):
        e = read_desktop(p)
        return proc_from(exec_argv(e, p), e) if e else None
    if os.path.isfile(p):
        return os.path.basename(p).lower()
    return None


# ---------- запущенные программы ----------

def _flatpak_running():
    """Запущенные flatpak-приложения: живой экземпляр держит блокировку на своём файле .ref."""
    out = set()
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / ".flatpak"
    try:
        dirs = list(base.iterdir())
    except OSError:
        return out
    for d in dirs:
        app = None
        try:
            group = None
            for line in (d / "info").read_text(errors="replace").splitlines():
                s = line.strip()
                if s.startswith("["):
                    group = s
                elif group == "[Application]" and s.startswith("name="):
                    app = s[5:].strip()
                    break
        except OSError:
            continue
        if not app:
            continue
        alive = False
        try:
            fd = os.open(d / ".ref", os.O_RDWR)
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)     # получилось — экземпляр уже завершился
                fcntl.lockf(fd, fcntl.LOCK_UN)
            except OSError:
                alive = True
            finally:
                os.close(fd)
        except OSError:
            try:
                pid = json.loads((d / "bwrapinfo.json").read_text()).get("child-pid")
                alive = bool(pid) and psutil.pid_exists(int(pid))
            except Exception:
                alive = False
        if alive:
            out.add("flatpak:" + app)
    return out


def running_procs():
    names = set()
    for p in psutil.process_iter():
        try:
            n = p.name()
        except Exception:
            continue
        if n:
            names.add(n.lower())
    names |= _flatpak_running()
    return names


# ---------- запуск ----------

def launch(item):
    path = item.get("path")
    if not path:
        raise FileNotFoundError("путь не задан")
    log.info("Запуск: %s (%s)", item["label"], path)
    if path.startswith("steam://"):
        if shutil.which("steam"):
            U.spawn(["steam", path])
        elif shutil.which("flatpak") and any("com.valvesoftware.Steam" in str(r) for r in steam_roots()):
            U.spawn(["flatpak", "run", "com.valvesoftware.Steam", path])
        else:
            U.open_path(path)
        return
    if "://" in path:
        U.open_path(path)
        return
    p = os.path.expanduser(path)
    if p.endswith(".desktop"):
        e = read_desktop(p)
        if not e:
            raise FileNotFoundError(p)
        argv = exec_argv(e, p)
        if not argv:
            raise ValueError("в .desktop нет команды запуска")
        if e.get("Terminal", "").lower() == "true":
            argv = U.terminal_argv(argv)
        cwd = os.path.expanduser(e["Path"]) if e.get("Path") and os.path.isdir(os.path.expanduser(e["Path"])) else None
        U.spawn(argv, cwd=cwd)
        return
    if os.path.isdir(p):
        U.open_path(p)
        return
    if not os.path.exists(p):
        raise FileNotFoundError(p)
    if os.access(p, os.X_OK):
        U.spawn([p], cwd=os.path.dirname(p))
    elif p.endswith(".sh"):
        U.spawn(["bash", p], cwd=os.path.dirname(p))
    elif p.lower().endswith(".appimage"):
        os.chmod(p, os.stat(p).st_mode | 0o100)               # скачанный AppImage без права на запуск
        U.spawn([p], cwd=os.path.dirname(p))
    else:
        U.open_path(p)


def _matching(proc):
    me = os.getuid()
    out = []
    for p in psutil.process_iter():
        try:
            if p.name().lower() == proc and p.uids().real == me:
                out.append(p)
        except Exception:
            continue
    return out


def _flatpak_pids(app):
    pids = []
    base = Path(os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}") / ".flatpak"
    for d in base.glob("*"):
        try:
            if f"name={app}" in (d / "info").read_text(errors="replace"):
                pid = json.loads((d / "bwrapinfo.json").read_text()).get("child-pid")
                if pid:
                    pids.append(int(pid))
        except Exception:
            continue
    return pids


async def close(proc, target=None):
    """Закрыть программу: сначала вежливо (SIGTERM), через 4 с — принудительно."""
    if not proc or proc in PROTECTED or proc not in running_procs():
        return
    log.info("Закрываю: %s", proc)
    if proc == "steam" and shutil.which("steam"):
        U.spawn(["steam", "-shutdown"])
        return
    if proc.startswith("flatpak:"):
        app = proc.split(":", 1)[1]
        for pid in _flatpak_pids(app):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        for _ in range(8):
            await asyncio.sleep(0.5)
            if proc not in running_procs():
                return
        subprocess.run(["flatpak", "kill", app], capture_output=True)
        return
    procs = _matching(proc)
    for p in procs:
        try:
            p.terminate()
        except Exception:
            pass
    for _ in range(8):
        await asyncio.sleep(0.5)
        if not any(p.is_running() for p in procs):
            return
    for p in procs:
        try:
            if p.is_running():
                p.kill()
        except Exception:
            pass


# ---------- окно уже запущенной программы: развернуть и вывести наверх (KDE) ----------
# На Wayland программа не может двигать чужие окна — это умеет только сам KWin. Даём ему разовый скрипт:
# найти окно (по PID или по .desktop), развернуть, сделать активным и ответить деку вызовом D-Bus.
# Скрипт — для KWin 5 и 6: в Plasma 6 activeWindow / windowList, в Plasma 5 — activeClient / clientList.

KWIN_SHOW = """(function () {
    var own = __OWN__, kids = __KIDS__, desk = __DESK__, toggle = __TOGGLE__;
    var list = workspace.stackingOrder || (workspace.windowList ? workspace.windowList() : workspace.clientList());
    function find(pids, byDesk) {
        for (var i = list.length - 1; i >= 0; i--) {          // сверху вниз: последнее, с чем работали
            var w = list[i];
            if (!w || !w.normalWindow || w.skipTaskbar) continue;
            if (pids.indexOf(w.pid) >= 0 || (byDesk && desk && w.desktopFileName == desk)) return w;
        }
        return null;
    }
    var hit = find(own, true) || find(kids, false);
    var active = workspace.activeWindow !== undefined ? workspace.activeWindow : workspace.activeClient;
    var hidden = false;
    if (hit && toggle && active && !active.minimized && (active === hit || own.indexOf(active.pid) >= 0 || kids.indexOf(active.pid) >= 0)) {
        active.minimized = true;          // повторное нажатие: окно и так наверху — свернуть
        hidden = true;
    } else if (hit) {
        hit.minimized = false;
        if (workspace.activeWindow !== undefined) workspace.activeWindow = hit; else workspace.activeClient = hit;
    }
    callDBus(__SVC__, "/ClaudeDeck", "local.claudedeck.Windows", "Found", hit ? (hidden ? "-" : "+") + String(hit.caption || "?") : "");
})();
"""
KWIN_IFACE = "local.claudedeck.Windows"
_kwin_seq = iter(range(1, 1 << 30))


def kwin_script(js, wait=2.0):
    """Разовый скрипт KWin. В тексте __SVC__ — адрес дека на шине: скрипт отвечает вызовом
    callDBus(__SVC__, "/ClaudeDeck", "local.claudedeck.Windows", "Found", "строка").
    Возвращает эту строку; KWin нет (не KDE) или он не ответил — исключение."""
    name = f"claudedeck_{os.getpid()}_{next(_kwin_seq)}"

    async def go():
        from dbus_fast import Message, MessageType
        from dbus_fast.aio import MessageBus
        bus = await MessageBus().connect()
        answer = asyncio.get_running_loop().create_future()

        def on_call(msg):
            if msg.message_type == MessageType.METHOD_CALL and msg.interface == KWIN_IFACE and msg.member == "Found":
                if not answer.done():
                    answer.set_result(str(msg.body[0]) if msg.body else "")
                return Message.new_method_return(msg)
            return None
        bus.add_message_handler(on_call)

        async def kwin(path, iface, member, sig="", body=()):
            r = await asyncio.wait_for(bus.call(Message(destination="org.kde.KWin", path=path, interface=iface,
                                                        member=member, signature=sig, body=list(body))), wait)
            if r.message_type == MessageType.ERROR:
                raise RuntimeError(f"{r.error_name}: {r.body[0] if r.body else ''}")
            return r.body

        fd, js_path = tempfile.mkstemp(prefix="claudedeck-", suffix=".js", dir=os.environ.get("XDG_RUNTIME_DIR") or None)
        loaded = False
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(js.replace("__SVC__", json.dumps(bus.unique_name)))
            sid = (await kwin("/Scripting", "org.kde.kwin.Scripting", "loadScript", "ss", [js_path, name]))[0]
            if sid < 0:
                raise RuntimeError(f"KWin не загрузил скрипт ({sid})")
            loaded = True
            for at in (f"/Scripting/Script{sid}", f"/{sid}"):     # Plasma 6 / Plasma 5
                try:
                    await kwin(at, "org.kde.kwin.Script", "run")
                    break
                except RuntimeError:
                    continue
            else:
                await kwin("/Scripting", "org.kde.kwin.Scripting", "start")
            return await asyncio.wait_for(answer, wait)
        finally:
            if loaded:
                try:
                    await kwin("/Scripting", "org.kde.kwin.Scripting", "unloadScript", "s", [name])
                except Exception:
                    pass
            bus.disconnect()
            try:
                os.unlink(js_path)
            except OSError:
                pass
    return asyncio.run(go())


def _window_pids(proc):
    """PID программы и, отдельно, её потомков: окно Steam, например, у дочернего steamwebhelper.
    У flatpak своё дерево процессов в песочнице — оно всё своё."""
    if proc.startswith("flatpak:"):
        roots = []
        for pid in _flatpak_pids(proc.split(":", 1)[1]):
            try:
                roots.append(psutil.Process(pid))
            except Exception:
                continue
    else:
        roots = _matching(proc)
    own, kids = set(), set()
    for p in roots:
        try:
            own.add(p.pid)
            (own if proc.startswith("flatpak:") else kids).update(c.pid for c in p.children(recursive=True))
        except Exception:
            continue
    me = os.getpid()
    return sorted(own - {me}), sorted(kids - own - {me})


def show(item, toggle=False):
    """Уже запущенная программа: развернуть её окно из свёрнутого и вывести наверх → "shown".
    toggle (повторное нажатие плитки): окно и так активное — свернуть → "hidden".
    Окна нет (свёрнута в трей или ещё не успела открыть окно) — False: тогда дек запускает её ещё раз,
    и программа сама показывает окно. Не KDE — всегда False (как раньше: просто запуск)."""
    proc = item.get("proc") or ""          # как в running_procs(): обычные — строчными, flatpak — как есть
    if not proc:
        return False
    path = item.get("path") or ""
    if proc.startswith("flatpak:"):
        desk = proc.split(":", 1)[1]
    elif path.endswith(".desktop"):
        desk = os.path.basename(path)[:-len(".desktop")]
    else:
        desk = ""
    own, kids = _window_pids(proc)
    if not own and not kids and not desk:
        return False
    js = (KWIN_SHOW.replace("__OWN__", json.dumps(own)).replace("__KIDS__", json.dumps(kids))
          .replace("__DESK__", json.dumps(desk)).replace("__TOGGLE__", "true" if toggle else "false"))
    try:
        caption = kwin_script(js)
    except Exception as e:
        log.debug("KWin: %s", e)
        return False
    if not caption:
        return False
    if caption.startswith("-"):
        log.info("Сворачиваю окно: %s", item.get("label") or proc)
        return "hidden"
    log.info("Показываю окно: %s", item.get("label") or proc)
    return "shown"


# ---------- раскладки окон (KDE): запомнить, где стоят окна программ, и расставить так же ----------

KWIN_GRAB = """(function () {
    var list = workspace.stackingOrder || (workspace.windowList ? workspace.windowList() : workspace.clientList());
    var out = [];
    for (var i = list.length - 1; i >= 0; i--) {
        var w = list[i];
        if (!w || !w.normalWindow || w.skipTaskbar) continue;
        var g = w.frameGeometry || w.geometry;
        out.push({pid: w.pid, title: String(w.caption), desk: String(w.desktopFileName || ""), x: g.x, y: g.y, w: g.width, h: g.height});
    }
    callDBus(__SVC__, "/ClaudeDeck", "local.claudedeck.Windows", "Found", JSON.stringify(out));
})();
"""
KWIN_PLACE = """(function () {
    var own = __OWN__, desk = __DESK__, r = __RECT__;
    var list = workspace.stackingOrder || (workspace.windowList ? workspace.windowList() : workspace.clientList());
    var hit = null;
    for (var i = list.length - 1; i >= 0 && !hit; i--) {
        var w = list[i];
        if (w && w.normalWindow && !w.skipTaskbar && (own.indexOf(w.pid) >= 0 || (desk && w.desktopFileName == desk))) hit = w;
    }
    if (hit) {
        hit.minimized = false;
        if (hit.setMaximize) hit.setMaximize(false, false);
        hit.frameGeometry = {x: r[0], y: r[1], width: r[2], height: r[3]};
    }
    callDBus(__SVC__, "/ClaudeDeck", "local.claudedeck.Windows", "Found", hit ? "1" : "");
})();
"""
SKIP_GRAB = {"plasmashell", "python3", "python", "konsole", "krunner", "systemsettings"}


def grab_windows():
    """Окна сейчас: [{proc, title, x, y, w, h, max}] — по одному на программу (верхнее). Только KDE."""
    try:
        wins = json.loads(kwin_script(KWIN_GRAB) or "[]")
    except Exception as e:
        log.debug("KWin: %s", e)
        return []
    out, seen = [], set()
    for w in wins:
        try:
            proc = psutil.Process(int(w["pid"])).name().lower()
        except Exception:
            proc = ""
        if not proc or proc in seen or proc in SKIP_GRAB or int(w["pid"]) == os.getpid():
            continue
        seen.add(proc)
        out.append({"proc": proc, "title": w.get("title", "")[:80], "x": int(w["x"]), "y": int(w["y"]),
                    "w": int(w["w"]), "h": int(w["h"]), "max": False})
    return out


def place_window(proc, x, y, w, h, maximized):
    if not proc:
        return False
    own, kids = _window_pids(proc)
    pids = own + kids
    if not pids:
        return False
    js = KWIN_PLACE.replace("__OWN__", json.dumps(pids)).replace("__DESK__", json.dumps("")) \
        .replace("__RECT__", json.dumps([int(x), int(y), int(w), int(h)]))
    try:
        return bool(kwin_script(js))
    except Exception as e:
        log.debug("KWin: %s", e)
        return False


# ---------- окно выбора файла ----------

def ask_files(title, filters, multi=False, start=None):
    """filters: [("Звуки", "*.mp3;*.wav"), ("Все файлы", "*.*")] → список путей (пустой — отмена).
    start — папка, с которой открыть окно (по умолчанию домашняя)."""
    pats = [(name, pat.replace(";", " ").replace("*.*", "*")) for name, pat in filters]
    start = str(start or U.HOME)
    if shutil.which("kdialog"):
        flt = "\n".join(f"{pat}|{name}" for name, pat in pats)
        argv = ["kdialog", "--title", title, "--getopenfilename"]
        if multi:
            argv += ["--multiple", "--separate-output"]
        r = subprocess.run(argv + [start, flt], capture_output=True, text=True, timeout=900)
        return [ln for ln in r.stdout.splitlines() if ln.strip()]
    if shutil.which("zenity"):
        argv = ["zenity", "--file-selection", f"--title={title}", f"--filename={start}/"]
        argv += [f"--file-filter={name} | {pat}" for name, pat in pats]
        if multi:
            argv += ["--multiple", "--separator=\n"]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=900)
        return [ln for ln in r.stdout.splitlines() if ln.strip()]
    try:
        import tkinter
        from tkinter import filedialog
    except ImportError:
        raise RuntimeError("нет окна выбора файла: поставь kdialog или zenity") from None
    root = tkinter.Tk()
    root.withdraw()
    try:
        types = [(name, pat) for name, pat in pats]
        if multi:
            return list(filedialog.askopenfilenames(parent=root, title=title, filetypes=types, initialdir=start) or ())
        f = filedialog.askopenfilename(parent=root, title=title, filetypes=types, initialdir=start)
        return [f] if f else []
    finally:
        root.destroy()


def pick_exe():
    """Окно выбора программы на ПК (блокирует — вызывать в отдельном потоке)."""
    files = ask_files(i18n.tr("Noryx — выбери программу", "Noryx — обери програму"),
                      [(i18n.tr("Программы", "Програми"), EXEC_FILES), (i18n.tr("Все файлы", "Усі файли"), "*")])
    f = files[0] if files else ""
    if not f:
        return None
    if f.endswith(".desktop"):
        e = read_desktop(f) or {}
        return {"label": localized(e, "Name") or Path(f).stem, "path": f, "target": None, "args": "",
                "proc": proc_from(exec_argv(e, f), e)}
    label = re.sub(r"[-_.](x86_64|amd64|linux)$", "", Path(f).stem, flags=re.I) or Path(f).stem
    return {"label": label, "path": f, "target": f, "args": "", "proc": os.path.basename(f).lower()}


# ---------- иконки ----------

_index = None
_index_lock = threading.Lock()


def _ini_value(path, group, key):
    try:
        cur = None
        for line in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
            s = line.strip()
            if s.startswith("["):
                cur = s[1:-1]
            elif cur == group and s.startswith(key + "="):
                return s.split("=", 1)[1].strip()
    except OSError:
        pass
    return None


def _icon_bases():
    return [U.data_home() / "icons", U.HOME / ".icons"] + [d / "icons" for d in U.data_dirs()]


def _theme_chain():
    theme = _ini_value(U.config_home() / "kdeglobals", "Icons", "Theme") or \
        _ini_value(U.config_home() / "gtk-3.0" / "settings.ini", "Settings", "gtk-icon-theme-name")
    chain = [theme] if theme else []
    i = 0
    while i < len(chain) and i < 12:
        for base in _icon_bases():
            inh = _ini_value(base / chain[i] / "index.theme", "Icon Theme", "Inherits")
            if inh:
                for t in inh.split(","):
                    t = t.strip()
                    if t and t not in chain:
                        chain.append(t)
                break
        i += 1
    for t in ("breeze", "Adwaita", "hicolor"):
        if t not in chain:
            chain.append(t)
    if "hicolor" in chain:                  # hicolor — общий запасной вариант, всегда последним
        chain.remove("hicolor")
        chain.append("hicolor")
    return chain


def _size_of(parts):
    for part in reversed(parts):
        if part == "scalable":
            return 512
        m = re.match(r"^(\d+)(x\d+)?(@\d+x)?$", part)
        if m:
            return int(m.group(1))
    return 32


def _build_index():
    idx = {}
    for rank, theme in enumerate(_theme_chain()):
        for base in _icon_bases():
            root = base / theme
            if not root.is_dir():
                continue
            for dirpath, _dirs, files in os.walk(root):
                parts = Path(dirpath).relative_to(root).parts
                size = _size_of(parts)
                for fn in files:
                    stem, ext = os.path.splitext(fn)
                    ext = ext.lower()
                    if ext not in (".png", ".svg", ".svgz", ".xpm"):
                        continue
                    vector = ext in (".svg", ".svgz")
                    score = (rank, 0 if vector else 1, -size)
                    best = idx.get(stem)
                    if best is None or score < best[0]:
                        idx[stem] = (score, os.path.join(dirpath, fn))
    for pix in [d / "pixmaps" for d in U.data_dirs()]:
        if pix.is_dir():
            for f in pix.iterdir():
                stem, ext = os.path.splitext(f.name)
                if ext.lower() in (".png", ".svg", ".xpm") and stem not in idx:
                    idx[stem] = ((99, 1, 0), str(f))
    log.info("Иконки: тема %s, значков %d", _theme_chain()[0], len(idx))
    return idx


def _theme_icon(name):
    global _index
    if not name:
        return None
    if os.path.isabs(name):
        return name if os.path.isfile(name) else None
    with _index_lock:
        if _index is None:
            _index = _build_index()
    stem = re.sub(r"\.(png|svg|svgz|xpm)$", "", name, flags=re.I)
    hit = _index.get(stem)
    return hit[1] if hit else None


def _steam_icon(appid):
    # значок, что Steam кладёт в тему (hicolor, до 256 px), — чётче, чем 32 px из кэша Steam
    big = _theme_icon(f"steam_icon_{appid}")
    if big:
        return big
    for root in steam_roots():
        cache = root / "appcache" / "librarycache"
        for pat in (f"{appid}_icon.jpg", f"{appid}/*.jpg"):
            hits = sorted(cache.glob(pat), key=lambda p: p.stat().st_size)
            if hits:
                return str(hits[0])
    return _theme_icon(f"steam_icon_{appid}")


def _icon_source(path):
    if path.startswith("steam://rungameid/"):
        return _steam_icon(path.rsplit("/", 1)[-1])
    p = os.path.expanduser(path)
    if p.endswith(".desktop"):
        e = read_desktop(p) or {}
        return _theme_icon(e.get("Icon"))
    if os.path.isdir(p):
        return _theme_icon("system-file-manager") or _theme_icon("folder")
    if os.path.isfile(p):
        stem = os.path.splitext(os.path.basename(p))[0].lower()
        return _theme_icon(stem)
    return None


def _raster_png(src):
    from PIL import Image
    im = Image.open(src).convert("RGBA")
    bbox = im.getbbox()
    if bbox and (bbox[2] - bbox[0]) > 8:
        im = im.crop(bbox)
    side = max(im.width, im.height)
    if im.width != im.height:
        sq = Image.new("RGBA", (side, side), (0, 0, 0, 0))
        sq.paste(im, ((side - im.width) // 2, (side - im.height) // 2))
        im = sq
    if im.width != ICON_PX:
        im = im.resize((ICON_PX, ICON_PX), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


_no_icon = set()
_svg = {}


def _icon_data(path):
    src = _icon_source(path)
    if not src or not os.path.isfile(src):
        return None
    low = src.lower()
    if low.endswith(".svg"):
        return Path(src).read_bytes(), "image/svg+xml"
    if low.endswith(".svgz"):
        return gzip.decompress(Path(src).read_bytes()), "image/svg+xml"
    return _raster_png(src), "image/png"


async def icon_png(path):
    """(байты, тип) значка программы: PNG 192 px или SVG как есть — браузер нарисует сам."""
    if path in _no_icon:
        return None
    if path in _svg:
        return _svg[path]
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]
    cached = ICON_DIR / f"{key}@{ICON_PX}{'v2' if path.startswith('steam://') else ''}.png"   # см. _steam_icon
    if cached.exists():
        return cached.read_bytes(), "image/png"
    try:
        res = await asyncio.get_running_loop().run_in_executor(None, _icon_data, path)
    except Exception as e:
        log.info("Иконка не нашлась для %s: %s", path, e)
        res = None
    if not res:
        _no_icon.add(path)
        return None
    if res[1] == "image/png":
        cached.write_bytes(res[0])
    else:
        _svg[path] = res
    return res
