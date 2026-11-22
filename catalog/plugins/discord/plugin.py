"""Discord — плагин-мод дека (в сборку не входит: папку discord кладут в plugins/ сами).

Микрофон и звук Discord через его локальный API (RPC), как у Elgato Stream Deck: окно Discord не всплывает,
игра не сворачивается, кнопки знают настоящее состояние (выключил в самом Discord — плитка тоже погасла).

Нужно своё приложение Discord (бесплатно, один раз): discord.com/developers/applications → New Application →
OAuth2: Client ID, Client Secret (Reset Secret), Redirects — http://localhost. В настройках плагина — оба поля,
«Подключить», в Discord — «Авторизовать». Токен — в pluginCfg.discord (телефонам не уходит).
Кнопки — на плитке Discord (вместо сочетаний клавиш), в доке и плитки «Микрофон» / «Звук».
"""
import json
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

NAME = {"ru": "Discord (микрофон и звук)", "uk": "Discord (мікрофон і звук)"}
ABOUT = {"ru": "Выключить микрофон и звук Discord, не открывая его окно (через API Discord, как Stream Deck)",
         "uk": "Вимкнути мікрофон і звук Discord, не відкриваючи його вікно (через API Discord, як Stream Deck)"}
API = 2                                  # версия API плагинов дека (perms.API_VERSION)
PERMS = ["network", "native"]
VERSION = "1.0.0"                        # версия плагина в каталоге (catalog/index.json): подними — деки предложат обновиться
CHANGES = [("1.0.0", "Первая версия в каталоге: микрофон и звук Discord через его локальный API",
            "Перша версія в каталозі: мікрофон і звук Discord через його локальний API")]

SCOPES = ["rpc", "rpc.voice.read", "rpc.voice.write"]
TOKEN_URL = "https://discord.com/api/oauth2/token"
REDIRECTS = ("http://localhost", "http://127.0.0.1")
PROCS = ["discord", "vesktop", "webcord", "legcord"]      # плитки этих программ получают кнопки плагина
RETRY = 5                                                  # с, переподключение к Discord


class Plugin:
    def __init__(self, api, make_client=None, http=None):
        self.api = api
        self.make = make_client
        self.http = http or self._http
        self.q = queue.Queue()
        self.run = False
        self.cli = None
        self.state = "init"         # init / nocfg / nodiscord / needauth / auth / ok / err
        self.err = ""
        self.mute = self.deaf = None
        self.user = ""

    # ---------- жизнь ----------
    def start(self):
        self.run = True
        threading.Thread(target=self.worker, name="discord", daemon=True).start()

    def stop(self):
        self.run = False
        self.q.put(("quit",))

    def tr(self, ru, uk):
        return self.api.tr(ru, uk)

    def push(self):
        self.api.call(self.api.update)

    def cfg(self):
        return self.api.cfg

    def set_state(self, st, err=""):
        if (st, err) != (self.state, self.err):
            self.state, self.err = st, err
            self.push()

    # ---------- поток ----------
    def worker(self):
        nxt = 0.0
        while self.run:
            try:
                job = self.q.get(timeout=0.05 if self.cli else 0.5)
            except queue.Empty:
                job = None
            if job and job[0] == "quit":
                break
            try:
                if not self.cli:
                    if job and job[0] in ("auth", "reconnect"):
                        nxt = 0.0
                    if time.monotonic() >= nxt:
                        nxt = time.monotonic() + RETRY
                        self.open(want_auth=bool(job and job[0] == "auth"))
                    continue
                if job:
                    self.do(job)
                self.cli.poll(0)
            except Exception as e:                      # Discord закрылся, токен отозвали и т. п.
                self.api.log.warning("discord: %s", e)
                self.drop()
                self.set_state("err", str(e))
                nxt = time.monotonic() + RETRY
        self.drop()

    def drop(self):
        if self.cli:
            try:
                self.cli.close()
            except Exception:
                pass
        self.cli = None
        self.mute = self.deaf = None

    def open(self, want_auth=False):
        c = self.cfg()
        if not c.get("clientId") or not c.get("secret"):
            return self.set_state("nocfg")
        if self.make:
            cli = self.make(c["clientId"], self.on_event)
        else:
            import discord_rpc
            cli = discord_rpc.Client(c["clientId"], on_event=self.on_event)
        if not cli.connect():
            return self.set_state("nodiscord")
        tok = c.get("token") or {}
        if tok.get("refresh") and tok.get("expires", 0) < time.time() + 3600:
            try:
                tok = self.exchange({"grant_type": "refresh_token", "refresh_token": tok["refresh"]})
            except Exception as e:
                self.api.log.warning("discord: обновить токен — %s", e)
                tok = {}
        if not tok.get("access"):
            if not want_auth:
                cli.close()
                if self.state != "err":             # отказ / ошибка авторизации — пусть видно, пока не нажмут снова
                    self.set_state("needauth")
                return
            self.set_state("auth")
            code = cli.request("AUTHORIZE", {"client_id": c["clientId"], "scopes": SCOPES}, timeout=120).get("code")
            tok = self.exchange({"grant_type": "authorization_code", "code": code})
        try:
            data = cli.request("AUTHENTICATE", {"access_token": tok["access"]})
        except Exception as e:
            # токен забываем, только если Discord сказал, что он плохой (4009 и т. п.). «User logged out» и прочее —
            # Discord только запустился и ещё не вошёл: токен хороший, просто попробуем снова через RETRY
            code = getattr(e, "code", 0)
            if code in (4009, 4006) or "invalid" in str(e).lower() and "token" in str(e).lower():
                c.pop("token", None)
                self.api.call(self.api.save)
            cli.close()
            raise
        u = data.get("user") or {}
        self.user = u.get("global_name") or u.get("username") or ""
        self.cli = cli
        self.voice(cli.request("GET_VOICE_SETTINGS"))
        cli.request("SUBSCRIBE", {}, evt="VOICE_SETTINGS_UPDATE")
        self.set_state("ok")
        self.push()

    def exchange(self, form):
        """Код / refresh-токен → токен (сервер Discord). redirect_uri пробуем, только если без него не вышло."""
        c = self.cfg()
        base = {"client_id": c["clientId"], "client_secret": c["secret"], **form}
        last = None
        for extra in ({},) + tuple({"redirect_uri": r} for r in REDIRECTS):
            try:
                r = self.http(TOKEN_URL, {**base, **extra})
            except Exception as e:
                last = e
                continue
            tok = {"access": r["access_token"], "refresh": r.get("refresh_token", ""),
                   "expires": time.time() + float(r.get("expires_in") or 604800)}
            c["token"] = tok
            self.api.call(self.api.save)
            return tok
        raise last or RuntimeError("нет токена")

    @staticmethod
    def _http(url, form):
        req = urllib.request.Request(url, data=urllib.parse.urlencode(form).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded",
                                              "User-Agent": "Noryx (local stream deck)"})
        try:
            with urllib.request.urlopen(req, timeout=15) as f:
                return json.loads(f.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read()[:200].decode('utf-8', 'replace')}") from None

    def on_event(self, evt, data):
        if evt == "VOICE_SETTINGS_UPDATE":
            self.voice(data)

    def voice(self, d):
        m, df = d.get("mute"), d.get("deaf")
        if (m, df) != (self.mute, self.deaf):
            self.mute, self.deaf = m, df
            self.push()

    def do(self, job):
        if job[0] == "mute" and self.mute is not None:
            self.voice(self.cli.request("SET_VOICE_SETTINGS", {"mute": not self.mute}))
        elif job[0] == "deaf" and self.deaf is not None:
            self.voice(self.cli.request("SET_VOICE_SETTINGS", {"deaf": not self.deaf}))

    # ---------- телефон ----------
    def action(self, aid, value):
        c = self.cfg()
        if aid in ("mute", "deaf"):
            if self.state != "ok":
                self.api.toast(self.status())
                return
            self.q.put((aid,))
        elif aid == "cid":
            c["clientId"] = "".join(ch for ch in str(value or "") if ch.isdigit())[:24]
            c.pop("token", None)
            self.api.save()
            self.q.put(("reconnect",))
        elif aid == "secret":
            c["secret"] = str(value or "").strip()[:80]
            c.pop("token", None)
            self.api.save()
            self.q.put(("reconnect",))
        elif aid == "auth":
            self.q.put(("auth",))
        elif aid == "logout":
            c.pop("token", None)
            self.api.save()
            self.drop()
            self.set_state("needauth")
            self.q.put(("reconnect",))

    def status(self):
        return {
            "init": self.tr("Подключаюсь…", "Підключаюся…"),
            "nocfg": self.tr("Нужны Client ID и Client Secret своего приложения Discord", "Потрібні Client ID і Client Secret свого застосунку Discord"),
            "nodiscord": self.tr("Discord не запущен на ПК", "Discord не запущено на ПК"),
            "needauth": self.tr("Нажми «Подключить» и разреши в Discord", "Натисни «Підключити» і дозволь у Discord"),
            "auth": self.tr("Подтверди в окне Discord («Авторизовать»)", "Підтверди у вікні Discord («Авторизувати»)"),
            "ok": self.tr("Подключено", "Підключено") + (f" — {self.user}" if self.user else ""),
            "err": self.tr("Ошибка", "Помилка") + (f": {self.err}" if self.err else ""),
        }.get(self.state, self.state)

    def ui(self):
        c = self.cfg()
        ok = self.state == "ok"
        mic = {"label": self.tr("Микрофон", "Мікрофон") + (" — " + self.tr("выкл", "вимк") if self.mute else ""),
               "on": bool(self.mute)}
        snd = {"label": self.tr("Звук", "Звук") + (" — " + self.tr("выкл", "вимк") if self.deaf else ""),
               "on": bool(self.deaf)}
        settings = [{"type": "text", "label": self.tr("Состояние", "Стан"), "value": self.status()}]
        if not ok:
            settings.append({"type": "label", "label": self.tr(
                "discord.com/developers/applications → New Application → OAuth2: Client ID, Client Secret, Redirects — http://localhost",
                "discord.com/developers/applications → New Application → OAuth2: Client ID, Client Secret, Redirects — http://localhost")})
        settings += [
            {"type": "input", "id": "cid", "placeholder": "Client ID" + (f" ({c['clientId']})" if c.get("clientId") else ""),
             "button": self.tr("Сохранить", "Зберегти")},
            {"type": "input", "id": "secret", "placeholder": "Client Secret" + (" (" + self.tr("сохранён", "збережено") + ")" if c.get("secret") else ""),
             "button": self.tr("Сохранить", "Зберегти")},
        ]
        if c.get("token"):
            settings.append({"type": "button", "id": "logout", "label": self.tr("Отключить (забыть токен)", "Відключити (забути токен)")})
        elif c.get("clientId") and c.get("secret"):
            settings.append({"type": "button", "id": "auth", "label": self.tr("Подключить", "Підключити"), "style": "dashed", "wide": True})
        dock = [{"type": "row", "items": [
            {"type": "button", "id": "mute", "icon": "micOff" if self.mute else "mic", "disabled": not ok, **mic},
            {"type": "button", "id": "deaf", "icon": "spk", "disabled": not ok, **snd}]}]
        if not ok:
            short = {"nocfg": self.tr("настрой в «Плагинах»", "налаштуй у «Плагінах»"), "nodiscord": self.tr("не запущен", "не запущено"),
                     "needauth": self.tr("нажми «Подключить»", "натисни «Підключити»"), "auth": self.tr("подтверди в Discord", "підтверди в Discord"),
                     "err": self.tr("ошибка — «Плагины»", "помилка — «Плагіни»")}.get(self.state, "…")
            dock.insert(0, {"type": "text", "label": "Discord", "value": short})
        return {
            "dock": dock,
            "settings": settings,
            "tiles": [{"id": "mute", "icon": "micOff" if self.mute else "mic", "label": self.tr("Микрофон", "Мікрофон"),
                       "sub": (self.tr("Выключен", "Вимкнено") if self.mute else self.tr("Включён", "Увімкнено")) if ok else "Discord",
                       "on": bool(self.mute)},
                      {"id": "deaf", "icon": "spk", "label": self.tr("Звук", "Звук"),
                       "sub": (self.tr("Выключен", "Вимкнено") if self.deaf else self.tr("Включён", "Увімкнено")) if ok else "Discord",
                       "on": bool(self.deaf)}],
            # подключён раньше (есть токен) — кнопки на плитке остаются и пока Discord перезапускается: иначе страница
            # вернула бы сочетания клавиш, а они вытаскивают окно Discord наверх
            "ctx": [{"procs": PROCS, "id": "mute", **mic}, {"procs": PROCS, "id": "deaf", **snd}] if ok or c.get("token") else [],
        }
