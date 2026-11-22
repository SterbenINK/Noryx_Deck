"""Медиа на Linux: Spotify, YouTube Music, браузеры — через MPRIS (D-Bus, как виджет плеера в KDE).

Тот же интерфейс, что у media.Media для Windows: state для дека, art — обложка,
command("play"/"next"/"prev"/"shuffle"/"repeat"). Свой поток со своим циклом событий.
"""
import asyncio
import base64
import hashlib
import logging
import threading
import urllib.parse
import urllib.request

import i18n

log = logging.getLogger("media")

MPRIS = "org.mpris.MediaPlayer2."
PATH = "/org/mpris/MediaPlayer2"
PLAYER = "org.mpris.MediaPlayer2.Player"
PROPS = "org.freedesktop.DBus.Properties"

# наш порядок повтора: 0 выкл → 1 плейлист → 2 трек
LOOP_TO_OURS = {"None": 0, "Playlist": 1, "Track": 2}
OURS_TO_LOOP = {0: "None", 1: "Playlist", 2: "Track"}

YTM_KEYS = ("youtube-music", "youtubemusic", "youtube_music", "ytmd", "ytmusic", "th-ch", "th_ch")
BROWSERS = ("firefox", "chromium", "chrome", "brave", "vivaldi", "opera", "edge", "yandex", "librewolf",
            "zen", "floorp", "plasma-browser-integration")


def kind_of(bus_name, identity="", url=""):
    a = f"{bus_name} {identity}".lower()
    if "spotify" in a:
        return "spotify"
    if any(k in a for k in YTM_KEYS) or "youtube music" in a or "music.youtube.com" in (url or ""):
        return "ytmusic"
    if any(b in a for b in BROWSERS):
        return "browser"
    return "other"


def _plain(v):
    """Variant → обычное значение (рекурсивно)."""
    v = getattr(v, "value", v)
    if isinstance(v, dict):
        return {k: _plain(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def _fetch_art(url):
    """Картинка обложки по mpris:artUrl: файл, http(s) или data: — (байты, тип)."""
    if url.startswith("file://"):
        path = urllib.parse.unquote(urllib.parse.urlparse(url).path)
        with open(path, "rb") as f:
            data = f.read(16_000_000)
    elif url.startswith("data:"):
        data = base64.b64decode(url.split(",", 1)[1])
    elif url.startswith(("http://", "https://")):
        req = urllib.request.Request(url, headers={"User-Agent": "Noryx"})
        with urllib.request.urlopen(req, timeout=6) as r:
            data = r.read(16_000_000)
    else:
        return None
    if not data:
        return None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        ctype = "image/png"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        ctype = "image/webp"
    elif data[:3] == b"GIF":
        ctype = "image/gif"
    else:
        ctype = "image/jpeg"
    return data, ctype


class Media:
    def __init__(self, on_update, player=lambda: "spotify"):
        self.on_update = on_update
        self.player = player
        self.watched = lambda: True  # есть ли кому показывать (дек подставляет: подключён ли хоть один телефон)
        self._last_app = None
        self.loop = None
        self.queue = None
        self.bus = None
        self.state = {"available": False, "smtc": False, "title": "", "artist": "", "app": "",
                      "playing": False, "shuffle": None, "repeat": None, "art": None, "player": "spotify"}
        self.art = None          # (bytes, content_type, хэш)
        self._art_url = None
        self._identity = {}
        threading.Thread(target=self._thread, name="media", daemon=True).start()

    def command(self, cmd):
        if self.loop and self.queue:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, cmd)

    def _thread(self):
        asyncio.run(self._main())

    async def _call(self, dest, iface, member, signature="", body=(), path=PATH):
        from dbus_fast import Message, MessageType
        reply = await asyncio.wait_for(self.bus.call(Message(
            destination=dest, path=path, interface=iface, member=member, signature=signature, body=list(body))), 2.0)
        if reply.message_type == MessageType.ERROR:
            raise RuntimeError(f"{reply.error_name}: {reply.body[0] if reply.body else ''}")
        return reply.body

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        try:
            from dbus_fast.aio import MessageBus
            self.bus = await MessageBus().connect()
            self.state.update(available=True, smtc=True)
            log.info("MPRIS подключён (сеансовая шина D-Bus)")
        except Exception as e:
            log.warning("D-Bus недоступен (%s) — плеер в доке работать не будет", e)
            self.state.update(available=False, smtc=False)
            self._push()
            while True:
                await self.queue.get()
        while True:
            try:
                # телефонов нет — плеер в доке никто не видит: раз в 5 с вместо раза в секунду (команды — сразу)
                cmd = await asyncio.wait_for(self.queue.get(), timeout=1.0 if self.watched() else 5.0)
            except asyncio.TimeoutError:
                cmd = None
            try:
                players = await self._players()
                pick = self._pick(players)
                if cmd:
                    await self._do(pick, cmd)
                    await asyncio.sleep(0.25)
                    players = await self._players()
                    pick = self._pick(players)
                await self._read(pick)
            except Exception:
                log.exception("медиа")
                await asyncio.sleep(2)

    def _want(self):
        try:
            w = self.player()
        except Exception:
            w = "spotify"
        return w if w in ("spotify", "ytmusic", "auto") else "spotify"

    async def _players(self):
        """[{name, kind, playing, props}] — все плееры MPRIS на шине."""
        names = (await self._call("org.freedesktop.DBus", "org.freedesktop.DBus", "ListNames",
                                  path="/org/freedesktop/DBus"))[0]
        out = []
        for n in names:
            if not n.startswith(MPRIS):
                continue
            try:
                props = _plain((await self._call(n, PROPS, "GetAll", "s", [PLAYER]))[0])
            except Exception:
                continue
            if n not in self._identity:
                try:
                    self._identity[n] = _plain((await self._call(n, PROPS, "Get", "ss",
                                                                 ["org.mpris.MediaPlayer2", "Identity"]))[0])
                except Exception:
                    self._identity[n] = ""
            meta = props.get("Metadata") or {}
            out.append({"name": n, "props": props, "meta": meta,
                        "kind": kind_of(n, self._identity[n], meta.get("xesam:url") or ""),
                        "playing": props.get("PlaybackStatus") == "Playing"})
        live = {p["name"] for p in out}
        self._identity = {k: v for k, v in self._identity.items() if k in live}
        return out

    def _pick(self, players):
        want = self._want()
        pick = None
        if want in ("spotify", "ytmusic"):
            cand = [p for p in players if p["kind"] == want]
            if not cand and want == "ytmusic":
                cand = [p for p in players if p["kind"] == "browser"]     # YT Music во вкладке браузера
            if cand:
                pick = sorted(cand, key=lambda p: not p["playing"])[0]
        else:
            playing = [p for p in players if p["playing"]]
            same = [p for p in players if p["name"] == self._last_app]
            best = [p for p in playing if p["name"] == self._last_app] or playing or same
            if best:
                pick = best[0]
        if pick is None and players:
            pick = sorted(players, key=lambda p: not p["playing"])[0]      # что угодно, лишь бы рулилось
        app = pick["name"] if pick else ""
        if app != self._last_app:
            self._last_app = app
            log.info("Плеер в доке: %s", app[len(MPRIS):] if app else i18n.tr("нет", "немає"))
        return pick

    async def _read(self, pick):
        if pick is None:
            self.art, self._art_url = None, None
            want = self._want()
            new = dict(self.state, title="", artist="", app="", playing=False, shuffle=None, repeat=None, art=None,
                       player=want if want != "auto" else "other")
        else:
            meta, props = pick["meta"], pick["props"]
            await self._load_art(meta.get("mpris:artUrl") or "", meta.get("xesam:title") or "")
            artist = meta.get("xesam:artist") or ""
            if isinstance(artist, list):
                artist = ", ".join(a for a in artist if a)
            kind = pick["kind"]
            if kind == "browser" and self._want() == "ytmusic":
                kind = "ytmusic"
            shuffle = props.get("Shuffle")
            loop = props.get("LoopStatus")
            new = dict(self.state, player=kind if kind in ("spotify", "ytmusic") else "other",
                       title=meta.get("xesam:title") or "", artist=artist,
                       app=pick["name"][len(MPRIS):], playing=pick["playing"],
                       shuffle=None if shuffle is None else bool(shuffle),
                       repeat=None if loop is None else LOOP_TO_OURS.get(loop, 0),
                       art=self.art[2] if self.art else None)
        if new != self.state:
            self.state = new
            self._push()

    async def _load_art(self, url, title):
        if url == self._art_url:
            return
        self._art_url = url
        if not url:
            self.art = None
            return
        try:
            res = await asyncio.get_running_loop().run_in_executor(None, _fetch_art, url)
        except Exception as e:
            log.info("Обложка не загрузилась для «%s»: %s", title or "?", e)
            res = None
        if not res:
            self.art = None
            return
        h = hashlib.sha1(res[0]).hexdigest()[:16]
        if not (self.art and self.art[2] == h):
            self.art = (res[0], res[1], h)
            log.info("Обложка: %d КБ для «%s»", len(res[0]) // 1024, title or "?")

    async def _do(self, pick, cmd):
        if pick is None:
            import lx_keys
            keys = {"play": "media_play", "next": "media_next", "prev": "media_prev"}
            if cmd in keys:
                lx_keys.tap(keys[cmd])
            return
        n = pick["name"]
        if cmd == "play":
            await self._call(n, PLAYER, "PlayPause")
        elif cmd == "next":
            await self._call(n, PLAYER, "Next")
        elif cmd == "prev":
            await self._call(n, PLAYER, "Previous")
        elif cmd in ("shuffle", "repeat"):
            from dbus_fast import Variant
            try:
                if cmd == "shuffle":
                    cur = bool(pick["props"].get("Shuffle"))
                    await self._call(n, PROPS, "Set", "ssv", [PLAYER, "Shuffle", Variant("b", not cur)])
                else:
                    ours = LOOP_TO_OURS.get(pick["props"].get("LoopStatus") or "None", 0)
                    await self._call(n, PROPS, "Set", "ssv",
                                     [PLAYER, "LoopStatus", Variant("s", OURS_TO_LOOP[(ours + 1) % 3])])
            except Exception as e:
                log.info("Плеер не дал сменить %s: %s", cmd, e)
        log.info("Медиа: %s", cmd)

    def _push(self):
        try:
            self.on_update(dict(self.state))
        except Exception:
            log.exception("push")
