"""Локальный API Discord (RPC) — тот же, что у Elgato Stream Deck и OBS: Discord на этом ПК слушает
именованный канал \\\\.\\pipe\\discord-ipc-N (Windows) или сокет discord-ipc-N (Linux). Только стандартная библиотека.

Кадр: op (int32 LE), длина (int32 LE), JSON. op 0 — рукопожатие {v:1, client_id}, 1 — команда / ответ / событие,
2 — закрыть, 3 / 4 — ping / pong. Чтение — без блокировки (Windows: PeekNamedPipe, Linux: select): канал Windows
синхронный, и висящее чтение не дало бы писать из того же потока.
"""
import json
import os
import socket
import struct
import sys
import time
import uuid

OP_HANDSHAKE, OP_FRAME, OP_CLOSE, OP_PING, OP_PONG = 0, 1, 2, 3, 4


class RPCError(Exception):
    def __init__(self, msg, code=0):
        super().__init__(msg)
        self.code = code


def _linux_dirs():
    base = [os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP")] + ["/tmp", f"/run/user/{os.getuid()}"]
    out = []
    for b in base:
        if b:
            for sub in ("", "app/com.discordapp.Discord", "app/dev.vencord.Vesktop", "snap.discord", ".flatpak/dev.vencord.Vesktop/xdg-run"):
                d = os.path.join(b, sub) if sub else b
                if d not in out:
                    out.append(d)
    return out


class Pipe:
    """Канал до Discord: connect() → send() / recv(timeout)."""

    def __init__(self):
        self.f = self.sock = self.h = None
        self.buf = b""

    def connect(self):
        self.close()
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            from ctypes import wintypes
            for i in range(10):
                try:
                    self.f = open(rf"\\.\pipe\discord-ipc-{i}", "r+b", buffering=0)
                except OSError:
                    continue
                self.h = msvcrt.get_osfhandle(self.f.fileno())
                k = ctypes.WinDLL("kernel32", use_last_error=True)
                k.PeekNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
                k.PeekNamedPipe.restype = wintypes.BOOL
                self.k32, self.DWORD = k, wintypes.DWORD
                return True
            return False
        for d in _linux_dirs():
            for i in range(10):
                path = os.path.join(d, f"discord-ipc-{i}")
                if not os.path.exists(path):
                    continue
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                try:
                    s.connect(path)
                except OSError:
                    s.close()
                    continue
                s.setblocking(False)
                self.sock = s
                return True
        return False

    def send(self, op, obj):
        data = json.dumps(obj).encode("utf-8")
        frame = struct.pack("<II", op, len(data)) + data
        if self.f:
            self.f.write(frame)
            self.f.flush()
        elif self.sock:
            self.sock.setblocking(True)
            try:
                self.sock.sendall(frame)
            finally:
                self.sock.setblocking(False)
        else:
            raise RPCError("нет связи с Discord")

    def _read_some(self, timeout):
        """Дочитать в буфер, что пришло (ждать не дольше timeout). False — канал закрыт."""
        end = time.monotonic() + timeout
        while True:
            if self.f:
                n = self.DWORD()
                if not self.k32.PeekNamedPipe(self.h, None, 0, None, n, None):
                    return False                                   # Discord закрылся
                if n.value:
                    self.buf += self.f.read(n.value)
                    return True
            elif self.sock:
                import select
                r, _, _ = select.select([self.sock], [], [], max(0.0, min(0.05, end - time.monotonic())))
                if r:
                    try:
                        chunk = self.sock.recv(65536)
                    except BlockingIOError:
                        chunk = None
                    if chunk == b"":
                        return False
                    if chunk:
                        self.buf += chunk
                        return True
            else:
                return False
            if time.monotonic() >= end:
                return True
            if self.f:
                time.sleep(0.02)

    def _frame(self):
        if len(self.buf) >= 8:
            op, ln = struct.unpack("<II", self.buf[:8])
            if len(self.buf) >= 8 + ln:
                raw, self.buf = self.buf[8:8 + ln], self.buf[8 + ln:]
                return op, json.loads(raw.decode("utf-8") or "{}")
        return None

    def recv(self, timeout=0.0):
        """(op, dict) или None — за timeout ничего целого не пришло. RPCError — канал закрыт."""
        end = time.monotonic() + timeout
        while True:
            fr = self._frame()
            if fr:
                return fr
            if not self._read_some(max(0.0, end - time.monotonic())):
                raise RPCError("Discord закрыл соединение")
            fr = self._frame()
            if fr:
                return fr
            if time.monotonic() >= end:
                return None

    def close(self):
        for x in (self.f, self.sock):
            try:
                if x:
                    x.close()
            except OSError:
                pass
        self.f = self.sock = self.h = None
        self.buf = b""


class Client:
    """Рукопожатие, команды (ждёт ответ со своим nonce), события — в on_event."""

    def __init__(self, client_id, pipe=None, on_event=None):
        self.cid = str(client_id)
        self.pipe = pipe or Pipe()
        self.on_event = on_event or (lambda evt, data: None)
        self.user = None

    def connect(self, timeout=5):
        if not self.pipe.connect():
            return False
        self.pipe.send(OP_HANDSHAKE, {"v": 1, "client_id": self.cid})
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = self.pipe.recv(0.2)
            if not got:
                continue
            op, msg = got
            if op == OP_CLOSE:
                raise RPCError(msg.get("message") or "Discord отказал в подключении", msg.get("code", 0))
            if msg.get("evt") == "READY":
                self.user = (msg.get("data") or {}).get("user")
                return True
        raise RPCError("Discord не ответил на подключение")

    def poll(self, timeout=0.0):
        """Обработать, что пришло само (события, ping)."""
        got = self.pipe.recv(timeout)
        while got:
            self._other(*got)
            got = self.pipe.recv(0)

    def _other(self, op, msg):
        if op == OP_PING:
            self.pipe.send(OP_PONG, msg)
        elif op == OP_CLOSE:
            raise RPCError(msg.get("message") or "Discord закрыл соединение", msg.get("code", 0))
        elif msg.get("cmd") == "DISPATCH":
            self.on_event(msg.get("evt"), msg.get("data") or {})

    def request(self, cmd, args=None, evt=None, timeout=10):
        nonce = str(uuid.uuid4())
        body = {"cmd": cmd, "args": args or {}, "nonce": nonce}
        if evt:
            body["evt"] = evt
        self.pipe.send(OP_FRAME, body)
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            got = self.pipe.recv(min(0.5, max(0.0, end - time.monotonic())))
            if not got:
                continue
            op, msg = got
            if msg.get("nonce") == nonce:
                if msg.get("evt") == "ERROR":
                    d = msg.get("data") or {}
                    raise RPCError(d.get("message") or "ошибка Discord", d.get("code", 0))
                return msg.get("data") or {}
            self._other(op, msg)
        raise RPCError(f"Discord не ответил на {cmd}")

    def close(self):
        try:
            self.pipe.send(OP_CLOSE, {})
        except Exception:
            pass
        self.pipe.close()
