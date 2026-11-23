"""Медиа: Spotify, YouTube Music (или любой плеер) через системный медиаконтроль Windows (SMTC).

Какой плеер показывать в доке — настройка player: spotify / ytmusic / auto («что играет»).
YouTube Music узнаём по имени приложения (приложение-PWA, th-ch, ytmdesktop), а если он открыт
просто во вкладке браузера — берём сессию браузера.

Работает в отдельном потоке со своим циклом событий, чтобы WinRT не конфликтовал с COM.
Если пакеты winrt не встали — остаются медиаклавиши (без названия трека, shuffle и повтора).

Обложка: при смене трека Spotify сначала меняет название и лишь через 1–2 с — картинку,
поэтому первые 8 с после смены трека перечитываем обложку каждую секунду, дальше — раз в 5 с.
Версия обложки — хэш картинки: одинаковая обложка (треки одного альбома) не мигает,
новая подменяется сразу, как только плеер её отдал.
"""
import asyncio
import hashlib
import logging
import threading
import time

import i18n
import keys

log = logging.getLogger("media")

# наш порядок повтора: 0 выкл → 1 плейлист → 2 трек
# SMTC: 0 None, 1 Track, 2 List
OURS_TO_SMTC = {0: 0, 1: 2, 2: 1}
SMTC_TO_OURS = {0: 0, 2: 1, 1: 2}

ART_FAST_WINDOW = 8.0     # сколько секунд после смены трека перечитываем обложку часто
ART_FAST_EVERY = 0.8      # (цикл опроса — раз в секунду, небольшой запас на дрожание)
ART_SLOW_EVERY = 5.0       # дальше — на случай, если плеер прислал картинку совсем поздно
ART_NONE_AFTER = 2.0      # если у трека так и нет картинки — убрать обложку прошлого

YTM_KEYS = ("youtube", "ytmd", "ytmusic", "th-ch", "cinhimbnkkaeohfgghhklpknlkffjgod")
BROWSERS = ("chrome", "msedge", "firefox", "opera", "brave", "yandex", "vivaldi", "arc")


def kind_of(app):
    """Что за плеер по AppUserModelID сессии: spotify / ytmusic / browser / other."""
    a = (app or "").lower()
    if "spotify" in a:
        return "spotify"
    if any(k in a for k in YTM_KEYS):
        return "ytmusic"
    if any(b in a for b in BROWSERS):
        return "browser"
    return "other"


def is_playing(s):
    try:
        return int(s.get_playback_info().playback_status) == 4
    except Exception:
        return False


class Media:
    def __init__(self, on_update, player=lambda: "spotify"):
        self.on_update = on_update  # вызывается из потока медиа: on_update(dict)
        self.player = player        # () → "spotify" / "ytmusic" / "auto" — из настроек дека
        self.watched = lambda: True  # есть ли кому показывать (дек подставляет: подключён ли хоть один телефон)
        self._last_app = None       # AUMID выбранной сессии — для «что играет» и для лога
        self.loop = None
        self.queue = None
        self.state = {"available": False, "smtc": False, "title": "", "artist": "", "app": "",
                      "playing": False, "shuffle": None, "repeat": None, "art": None, "player": "spotify"}
        self.art = None          # (bytes, content_type, хэш) обложки текущего трека
        self._art_key = None
        self._art_since = 0.0    # когда сменился трек
        self._art_next = 0.0     # когда перечитать обложку
        self._art_err_key = None # чтобы не засорять лог одной и той же ошибкой
        threading.Thread(target=self._thread, name="media", daemon=True).start()

    def command(self, cmd):
        if self.loop and self.queue:
            self.loop.call_soon_threadsafe(self.queue.put_nowait, cmd)

    def _thread(self):
        asyncio.run(self._main())

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.queue = asyncio.Queue()
        try:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Mgr
            mgr = await Mgr.request_async()
            self.state.update(available=True, smtc=True)
            log.info("SMTC подключён")
        except Exception as e:
            log.warning("WinRT недоступен (%s) — только медиаклавиши", e)
            self.state.update(available=True, smtc=False, title="Медиаклавиши", artist="")
            self._push()
            while True:
                cmd = await self.queue.get()
                self._fallback(cmd)
        while True:
            try:
                # телефонов нет — плеер в доке никто не видит: раз в 5 с вместо раза в секунду (команды — сразу)
                cmd = await asyncio.wait_for(self.queue.get(), timeout=1.0 if self.watched() else 5.0)
            except asyncio.TimeoutError:
                cmd = None
            try:
                session = self._pick(mgr)
                if cmd:
                    await self._do(session, cmd)
                    await asyncio.sleep(0.25)
                await self._read(session)
            except Exception:
                log.exception("медиа")
                await asyncio.sleep(2)

    def _want(self):
        try:
            w = self.player()
        except Exception:
            w = "spotify"
        return w if w in ("spotify", "ytmusic", "auto") else "spotify"

    def _pick(self, mgr):
        sessions = list(mgr.get_sessions())
        want = self._want()
        info = [(s, kind_of(s.source_app_user_model_id), is_playing(s), s.source_app_user_model_id or "")
                for s in sessions]
        pick = None
        if want in ("spotify", "ytmusic"):
            cand = [x for x in info if x[1] == want]
            if not cand and want == "ytmusic":
                cand = [x for x in info if x[1] == "browser"]     # YT Music во вкладке браузера
            if cand:
                pick = sorted(cand, key=lambda x: not x[2])[0][0]   # играющий — первым
        else:                                                     # «что играет»
            playing = [x for x in info if x[2]]
            same = [x for x in info if x[3] == self._last_app]
            best = ([x for x in playing if x[3] == self._last_app] or playing or same)
            if best:
                pick = best[0][0]
        if pick is None and sessions:
            pick = mgr.get_current_session()                      # выбранного нет — что угодно, лишь бы рулилось
        app = (pick.source_app_user_model_id or "") if pick is not None else ""
        if app != self._last_app:
            self._last_app = app
            log.info("Плеер в доке: %s", app or i18n.tr("none", "немає"))
        return pick

    async def _read(self, s):
        if s is None:
            self.art = None
            self._art_key = None
            want = self._want()
            new = dict(self.state, title="", artist="", app="", playing=False, shuffle=None, repeat=None, art=None,
                       player=want if want != "auto" else "other")
        else:
            props = await s.try_get_media_properties_async()
            info = s.get_playback_info()
            now = time.monotonic()
            key = (props.title or "", props.artist or "", props.album_title or "")
            if key != self._art_key:
                # новый трек: картинку Spotify обновит чуть позже — несколько секунд проверяем часто
                self._art_key = key
                self._art_since = now
                self._art_next = now
            if now >= self._art_next:
                age = now - self._art_since
                await self._load_art(props, age, key)
                self._art_next = now + (ART_FAST_EVERY if age < ART_FAST_WINDOW else ART_SLOW_EVERY)
            rep = info.auto_repeat_mode
            shf = info.is_shuffle_active
            kind = kind_of(s.source_app_user_model_id)
            if kind == "browser" and self._want() == "ytmusic":
                kind = "ytmusic"                                  # вкладка с YouTube Music
            new = dict(self.state,
                       player=kind if kind in ("spotify", "ytmusic") else "other",
                       title=props.title or "", artist=props.artist or "",
                       app=(s.source_app_user_model_id or "").split("!")[0],
                       playing=int(info.playback_status) == 4,
                       shuffle=None if shf is None else bool(shf),
                       repeat=None if rep is None else SMTC_TO_OURS.get(int(rep), 0),
                       art=self.art[2] if self.art else None)
        if new != self.state:
            self.state = new
            self._push()

    async def _load_art(self, props, age, key):
        """Читает обложку из SMTC (нужен winrt-Windows.Storage.Streams); размер потока у части
        плееров нулевой, поэтому читаем кусками до конца."""
        title = props.title or "?"
        try:
            ref = props.thumbnail
            if ref is None:
                if self.art and age >= ART_NONE_AFTER:
                    self.art = None                      # у трека нет картинки — не держим чужую
                return
            from winrt.windows.storage.streams import Buffer, InputStreamOptions
            stream = await ref.open_read_async()
            chunks, total, cap = [], 0, 1 << 20
            while total < 16_000_000:
                buf = Buffer(cap)
                res = await stream.read_async(buf, cap, InputStreamOptions.READ_AHEAD)
                got = res if res is not None else buf
                n = int(got.length)
                if not n:
                    break
                chunks.append(self._buf_bytes(got, n))
                total += n
            try:
                stream.close()
            except Exception:
                pass
            data = b"".join(chunks)
            if not data:
                return
            h = hashlib.sha1(data).hexdigest()[:16]
            if self.art and self.art[2] == h:
                return                                   # та же картинка — ничего не меняем
            ctype = "image/png" if data[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
            self.art = (data, ctype, h)
            log.info("Обложка: %d КБ для «%s»", len(data) // 1024, title)
        except Exception:
            if self._art_err_key != key:
                self._art_err_key = key
                log.info("Обложка не загрузилась для «%s»", title, exc_info=True)

    @staticmethod
    def _buf_bytes(ibuf, n):
        try:
            return bytes(memoryview(ibuf))[:n]
        except TypeError:
            from winrt.windows.storage.streams import DataReader
            reader = DataReader.from_buffer(ibuf)
            arr = bytearray(n)
            reader.read_bytes(arr)
            return bytes(arr)

    async def _do(self, s, cmd):
        if s is None:
            self._fallback(cmd)
            return
        if cmd == "play":
            await s.try_toggle_play_pause_async()
        elif cmd == "next":
            await s.try_skip_next_async()
        elif cmd == "prev":
            await s.try_skip_previous_async()
        elif cmd == "shuffle":
            cur = s.get_playback_info().is_shuffle_active
            await s.try_change_shuffle_active_async(not bool(cur))
        elif cmd == "repeat":
            from winrt.windows.media import MediaPlaybackAutoRepeatMode as R
            cur = s.get_playback_info().auto_repeat_mode
            ours = SMTC_TO_OURS.get(int(cur), 0) if cur is not None else 0
            nxt = (ours + 1) % 3
            await s.try_change_auto_repeat_mode_async(R(OURS_TO_SMTC[nxt]))
        log.info("Медиа: %s", cmd)

    def _fallback(self, cmd):
        m = {"play": "media_play", "next": "media_next", "prev": "media_prev"}
        if cmd in m:
            keys.tap(m[cmd])

    def _push(self):
        try:
            self.on_update(dict(self.state))
        except Exception:
            log.exception("push")
