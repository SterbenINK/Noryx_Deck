"""История активности и центр уведомлений дека (общие для Windows и Linux).

Одна лента событий (последние MAX, в памяти — до перезапуска дека): режимы, программы с плиток, выход звука,
громкость с дека, скрипты, устройства, телефоны, лампа, обновления. У события:
    id, ts (время), kind (mode / app / audio / device / script / phone / lamp / update), text (на языке дека),
    level (info / warn / error / update), notify (показать в центре уведомлений),
    redo (сообщение, которым телефон может безопасно повторить действие: {"t":"mode",...}), link (куда вести: settings).
Непрочитанные — уведомления с id больше read; на значке считаются только warn / error / update.
"""
import collections
import time

MAX = 200
LOUD = ("warn", "error", "update")


class Events:
    def __init__(self, push):
        """push(msg) — разослать телефонам (корутина)."""
        self.push = push
        self.items = collections.deque(maxlen=MAX)
        self.seq = 0
        self.read = 0

    def add(self, kind, text, level="info", notify=False, redo=None, link=None):
        self.seq += 1
        ev = {"id": self.seq, "ts": time.time(), "kind": kind, "text": text, "level": level, "notify": bool(notify)}
        if redo:
            ev["redo"] = redo
        if link:
            ev["link"] = link
        self.items.append(ev)
        return ev

    def unread(self):
        return sum(1 for e in self.items if e["notify"] and e["id"] > self.read and e["level"] in LOUD)

    def msg(self):
        return {"t": "events", "items": list(self.items), "read": self.read, "unread": self.unread()}

    def mark_read(self, upto=None):
        self.read = max(self.read, int(upto if upto is not None else self.seq))
