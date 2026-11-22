"""Свои устройства. Дек слушает всю локальную сеть, поэтому новый телефон сначала вводит код, который показывает ПК
(значок у часов, уведомление, уже подключённые телефоны), и получает постоянный ключ: страница хранит его у себя и
кладёт в cookie — так он приходит и с WebSocket, и с картинками. На ПК — только хэши ключей (`data/phones.json`).
Сам ПК (127.0.0.1) кода не вводит. При обновлении со старой версии GRACE секунд пускаем без кода и выдаём ключ:
телефоны, что уже были подключены, остаются своими."""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import urlsplit

import roles
import store

log = logging.getLogger("deck.pair")

COOKIE = "deckkey"
GRACE = 600        # с, сколько после первого запуска новой версии пускаем прежние телефоны без кода
CODE_TTL = 300     # с, сколько живёт код
TRIES = 5          # неверных вводов на один код
LOCK = 30          # с, пауза после TRIES неверных вводов
SHOW_GAP = 20      # с, не чаще показываем код на ПК (чужое устройство в сети не завалит уведомлениями)
MAX = 40           # устройств помним; лишние — самые давние
LOCAL_IPS = ("127.0.0.1", "::1")
LOCAL_HOSTS = ("127.0.0.1", "localhost", "::1")


def host_of(value):
    """Имя из заголовка Host / Origin без порта и скобок IPv6."""
    try:
        return (urlsplit(value if "//" in value else "//" + value).hostname or "").lower()
    except ValueError:
        return ""


def is_local(request):
    """Запрос с самого ПК. Адрес — петля, и Host — тоже: иначе страница из интернета, чьё имя указывает на 127.0.0.1
    (DNS rebinding), сошла бы за свою."""
    return request.remote in LOCAL_IPS and host_of(request.headers.get("Host", "")) in LOCAL_HOSTS


def same_origin(request):
    """WebSocket открывает наша же страница (Origin = Host) или приложение без Origin; чужой сайт в браузере — нет."""
    origin = request.headers.get("Origin")
    if not origin or origin == "null" and is_local(request):
        return True
    o, h = urlsplit(origin), request.headers.get("Host", "")
    return bool(o.netloc) and o.netloc.lower() == h.lower()


def digest(key):
    return hashlib.sha256(str(key).encode("utf-8", "replace")).hexdigest()


class Pairing:
    def __init__(self, fresh=False, now=time.time):
        self.file = store.DATA / "phones.json"
        self.now = now
        self.code = None            # {code, until, left}
        self.locked = 0             # до какого времени не принимаем коды
        self.shown = 0              # когда последний раз показали код на ПК
        self.dirty = 0
        try:
            self.data = json.loads(self.file.read_text(encoding="utf-8"))
            if not isinstance(self.data.get("phones"), dict):
                raise ValueError("phones")
        except FileNotFoundError:
            # файла нет: новая установка — код нужен сразу; обновление со старой версии — прежним телефонам время зайти
            self.data = {"grace": 0 if fresh else self.now() + GRACE, "phones": {}}
            self.save()
            if not fresh:
                log.info("Подключение по коду: %d мин прежние телефоны заходят без кода", GRACE // 60)
        except Exception:
            log.exception("Список своих устройств повреждён — начинаю заново (телефоны введут код ещё раз)")
            self.data = {"grace": 0, "phones": {}}
            self.save()

    # ---------- хранение ----------
    def save(self):
        try:
            tmp = self.file.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)     # ключи — только хозяину (Linux)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.file)
            self.dirty = 0
        except OSError as e:
            log.warning("Список своих устройств не сохранён: %s", e)

    @property
    def phones(self):
        return self.data["phones"]

    def grace(self):
        return self.now() < float(self.data.get("grace") or 0)

    # ---------- ключи ----------
    def check(self, key):
        """Ключ → id устройства или None."""
        if not key or not isinstance(key, str) or len(key) > 200:
            return None
        h = digest(key)
        for pid, p in self.phones.items():
            if hmac.compare_digest(h, p.get("hash", "")):
                return pid
        return None

    def issue(self, name, ip):
        """Новое своё устройство → его ключ (на ПК остаётся только хэш)."""
        key = secrets.token_urlsafe(32)
        pid = secrets.token_hex(4)
        t = int(self.now())
        self.phones[pid] = {"hash": digest(key), "name": str(name or "?")[:80], "ip": str(ip or ""), "added": t, "seen": t}
        while len(self.phones) > MAX:
            del self.phones[min(self.phones, key=lambda k: self.phones[k].get("seen", 0))]
        self.save()
        return pid, key

    def touch(self, pid, ip, name=None):
        p = self.phones.get(pid)
        if not p:
            return
        t = int(self.now())
        changed = p.get("ip") != str(ip or "") or (name and p.get("name") != name)
        p["ip"] = str(ip or "")
        if name:
            p["name"] = str(name)[:80]
        if changed or t - p.get("seen", 0) > 3600:      # «когда видели» — не чаще раза в час на диск
            p["seen"] = t
            self.save()
        else:
            p["seen"] = t

    def forget(self, pid):
        if self.phones.pop(pid, None) is None:
            return False
        self.save()
        return True

    def public(self, online=(), me=None):
        """Список для настроек: без хэшей."""
        return [{"id": pid, "name": p.get("name", "?"), "ip": p.get("ip", ""), "added": p.get("added", 0),
                 "seen": p.get("seen", 0), "on": pid in online, "me": pid == me, "role": self.role(pid)}
                for pid, p in sorted(self.phones.items(), key=lambda kv: -kv[1].get("seen", 0))]

    def role(self, pid):
        """Роль устройства {role, modes, start}; сам ПК и устройства без роли — основной дек."""
        return roles.clean((self.phones.get(pid) or {}).get("role")) if pid and pid != "local" else roles.clean(None)

    def set_role(self, pid, value, exists=None):
        p = self.phones.get(pid)
        if not p:
            return None
        p["role"] = roles.clean(value, exists)
        self.save()
        return p["role"]

    # ---------- код ----------
    def current(self):
        """Действующий код (новый, если прежний истёк или исчерпан)."""
        t = self.now()
        if not self.code or t >= self.code["until"] or self.code["left"] <= 0:
            self.code = {"code": f"{secrets.randbelow(10 ** 6):06d}", "until": t + CODE_TTL, "left": TRIES}
        return self.code["code"]

    def may_show(self):
        """Пора ли показать код на ПК ещё раз (просит неизвестное устройство)."""
        t = self.now()
        if t - self.shown < SHOW_GAP:
            return False
        self.shown = t
        return True

    def enter(self, code):
        """Ввод кода → "ok" | "bad" | "locked". Верный код одноразовый."""
        t = self.now()
        if t < self.locked:
            return "locked"
        c = self.code
        if not c or t >= c["until"] or c["left"] <= 0:
            return "bad"
        text = "".join(ch for ch in str(code or "") if ch.isdigit())[:12]
        if hmac.compare_digest(text, c["code"]):
            self.code = None
            return "ok"
        c["left"] -= 1
        if c["left"] <= 0:
            self.code = None
            self.locked = t + LOCK
            log.warning("Код подключения введён неверно %d раз — пауза %d с", TRIES, LOCK)
        return "bad"
