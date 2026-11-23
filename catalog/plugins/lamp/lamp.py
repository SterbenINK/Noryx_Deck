"""Лампа на Arduino (скетч ClaudeLamp/ рядом): яркость с дека по USB-Serial. Часть плагина лампы (plugins/lamp).

• Порт ищется сам — плата отвечает «CLAUDE_LAMP …» на «?» — и запоминается в конфиге.
  Новые порты подхватываются на лету: воткнул ардуинку — через пару секунд ползунок ожил.
• Порт держим открытым только пока им пользуются: после 10 с тишины отпускаем,
  чтобы Arduino IDE могла прошить плату.
• DTR при открытии не поднимаем: иначе Nano/Uno перезагружается и лампа моргает.
  Если плата всё равно перезагружается при каждом открытии — держим порт дольше (2 мин).
"""
import logging
import queue
import subprocess
import sys
import threading
import time
from pathlib import Path

import i18n

log = logging.getLogger("lamp")

BAUD = 115200
IDLE_CLOSE = 10.0            # отпустить порт после стольких секунд тишины
IDLE_CLOSE_RESETTY = 120.0   # …если плата перезагружается при каждом открытии порта
PROBE_TIME = 2.6             # сколько ждать ответа (с запасом на загрузчик после перезагрузки)
HOTPLUG_EVERY = 3.0          # как часто смотреть список портов (сами порты при этом не открываются)

# USB-чипы и платы, за которыми может быть ардуинка: Arduino, CH340, FTDI, CP210x, SparkFun, Adafruit, PL2303
KNOWN_VID = {0x2341, 0x2A03, 0x1A86, 0x0403, 0x10C4, 0x1B4F, 0x239A, 0x067B}
HINTS = ("arduino", "ch340", "ch341", "usb-serial", "usb serial", "cp210", "ftdi")
# платы со своим USB (Leonardo, Micro, Pro Micro): молчат без поднятого DTR, а перезагружает их не он
NATIVE_USB = {(0x2341, 0x8036), (0x2341, 0x8037), (0x2341, 0x0036), (0x2341, 0x0037),
              (0x2A03, 0x8036), (0x2A03, 0x8037), (0x1B4F, 0x9205), (0x1B4F, 0x9206)}
NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


PYSERIAL = "pyserial==3.5"        # ставится, только когда лампу включили (в requirements его нет)


def ensure_pyserial():
    """pyserial ставится сам при первом запуске (перезапуск из трея не проходит через start.bat)."""
    try:
        import serial  # noqa: F401
        return True
    except ImportError:
        pass
    py = Path(sys.executable)
    if py.name.lower() == "pythonw.exe":
        py = py.with_name("python.exe")
    log.info("Для лампы нужен pyserial — ставлю…")
    try:
        r = subprocess.run([str(py), "-m", "pip", "install", PYSERIAL], capture_output=True,
                           timeout=300, creationflags=NO_WINDOW)
        if r.returncode:
            log.warning("pip install pyserial не удался:\n%s",
                        (r.stdout + r.stderr).decode("utf-8", "replace")[-1500:])
    except Exception as e:
        log.warning("pip install pyserial: %s", e)
    import importlib
    importlib.invalidate_caches()
    try:
        import serial  # noqa: F401
        log.info("pyserial установлен")
        return True
    except ImportError:
        return False


def parse(line):
    """«CLAUDE_LAMP 600 1» / «CLAUDE_LAMP_BOOT 600 1» → (загрузка?, уровень, вкл) или None."""
    parts = line.split()
    if len(parts) != 3 or parts[0] not in ("CLAUDE_LAMP", "CLAUDE_LAMP_BOOT"):
        return None
    try:
        return parts[0].endswith("_BOOT"), max(0, min(1000, int(parts[1]))), parts[2] == "1"
    except ValueError:
        return None


def looks_like_arduino(p):
    text = f"{p.description or ''} {p.manufacturer or ''}".lower()
    return (p.vid in KNOWN_VID) or any(h in text for h in HINTS)


class Lamp:
    def __init__(self, port_hint, enabled, on_update, on_port):
        self.on_update = on_update    # (dict) — вызывается из потока лампы
        self.on_port = on_port        # (str)  — запомнить найденный порт в конфиге
        self.hint = port_hint or ""
        self.q = queue.Queue()
        self.ser = None
        self.rx = b""
        self.dev = None               # порт, на котором нашлась лампа
        self.last_io = 0.0
        self.idle = IDLE_CLOSE
        self.known = set()
        self.enabled = threading.Event()
        if enabled:
            self.enabled.set()
        self.state = {"status": "search" if enabled else "off", "port": None, "level": 600, "on": False}
        threading.Thread(target=self._run, name="lamp", daemon=True).start()

    # ---------- вызывается из сервера ----------
    def set(self, level):
        self.q.put(("L", max(0, min(1000, int(level)))))

    def toggle(self):
        self.q.put(("T", 0))

    def scan(self):
        self.q.put(("scan", 0))

    def enable(self, on):
        if on and not self.enabled.is_set():
            self.enabled.set()
            self.q.put(("scan", 0))
        elif not on and self.enabled.is_set():
            self.enabled.clear()
            self.q.put(("off", 0))

    # ---------- поток лампы ----------
    def _push(self, **kw):
        new = dict(self.state, **kw)
        if new != self.state:
            self.state = new
            try:
                self.on_update(dict(new))
            except Exception:
                log.exception("lamp push")

    def _run(self):
        self.enabled.wait()
        while not ensure_pyserial():
            self._push(status="nolib")
            while self.q.get()[0] != "scan":   # «Повторить» на деке — ещё попытка установки
                pass
            self._push(status="search")
        self._find(everything=True)
        next_hot = time.monotonic() + HOTPLUG_EVERY
        while True:
            try:
                batch = [self.q.get(timeout=0.5)]
            except queue.Empty:
                batch = []
            while True:
                try:
                    batch.append(self.q.get_nowait())
                except queue.Empty:
                    break
            try:
                if batch:
                    self._do(batch)
                now = time.monotonic()
                if self.ser and now - self.last_io > self.idle:
                    self._close()
                if now >= next_hot and self.enabled.is_set():
                    next_hot = now + HOTPLUG_EVERY
                    self._hotplug()
            except Exception:
                log.exception("Лампа")
                self._close()
                time.sleep(1)

    def _do(self, batch):
        cmds = []
        for c in batch:                       # из серии «L» важна только последняя
            if cmds and c[0] == "L" and cmds[-1][0] == "L":
                cmds[-1] = c
            else:
                cmds.append(c)
        for c, v in cmds:
            if c == "off":
                self._close()
                self._push(status="off")
                continue
            if not self.enabled.is_set():
                continue
            if c == "scan":
                self._close()
                self._find(everything=True)
                continue
            line = f"L {v}" if c == "L" else "T"
            if self._send(line) is False and self._find(everything=True):
                self._send(line)              # плату переткнули / сменился COM-порт

    def _send(self, line):
        """True — отправлено; None — порт занят другой программой; False — лампы нет."""
        if not self.dev:
            return False
        if not self.ser:
            r = self._handshake(self.dev, reopen=True)
            if r in ("busy", "perm"):
                self._push(status=r, port=self.dev)
                return None
            if r is not True:
                return False
        try:
            self.ser.write((line + "\n").encode("ascii"))
            self.last_io = time.monotonic()
            for text in self._lines(0.3):
                st = parse(text)
                if st:
                    self._push(status="ok", port=self.dev, level=st[1], on=st[2])
                    break
            return True
        except Exception as e:
            log.info("Лампа: связь потеряна (%s)", e)
            self._close()
            return False

    def _lines(self, timeout):
        """Строки от платы за timeout секунд."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            chunk = self.ser.read(self.ser.in_waiting or 1)     # ждёт не дольше ser.timeout
            if not chunk:
                continue
            self.rx = (self.rx + chunk)[-512:]
            while b"\n" in self.rx:
                raw, self.rx = self.rx.split(b"\n", 1)
                yield raw.strip().decode("ascii", "replace")

    def _open(self, dev):
        import serial
        from serial.tools import list_ports
        info = next((p for p in list_ports.comports() if p.device == dev), None)
        s = serial.Serial()
        s.port, s.baudrate, s.timeout, s.write_timeout = dev, BAUD, 0.05, 1.0
        # Nano/Uno: DTR не трогаем (он перезагружает плату); Leonardo/Pro Micro без DTR молчат
        s.dtr = bool(info and (info.vid, info.pid) in NATIVE_USB)
        s.rts = False
        s.open()
        return s

    def _handshake(self, dev, reopen=False):
        """Открыть порт и дождаться ответа платы: True / "busy" / "perm" (Linux, нет прав) / False."""
        import serial
        self._close()
        try:
            s = self._open(dev)
        except serial.SerialException as e:
            text = str(e)
            if sys.platform != "win32" and ("Permission denied" in text or "[Errno 13]" in text):
                return "perm"                 # Linux: пользователь не в группе dialout
            if any(w in text for w in ("PermissionError", "Access is denied", "(13,")) or "доступ" in text.lower():
                return "busy"
            log.debug("Порт %s: %s", dev, text)
            return False
        except Exception as e:
            log.debug("Порт %s: %s", dev, e)
            return False
        self.ser, self.rx = s, b""
        try:
            end = time.monotonic() + PROBE_TIME
            ask = 0.0
            while time.monotonic() < end:
                if time.monotonic() >= ask:
                    s.write(b"?\n")
                    ask = time.monotonic() + 0.5
                for text in self._lines(0.1):
                    st = parse(text)
                    if not st:
                        continue
                    boot, level, on = st
                    if boot and reopen and self.idle != IDLE_CLOSE_RESETTY:
                        self.idle = IDLE_CLOSE_RESETTY
                        log.warning("Плата лампы перезагружается при открытии порта — держу порт открытым "
                                    "до %d с после последнего касания (лечится конденсатором 10 мкФ RESET–GND)",
                                    int(IDLE_CLOSE_RESETTY))
                    self.last_io = time.monotonic()
                    self._push(level=level, on=on)
                    return True
        except Exception as e:
            log.debug("Порт %s: %s", dev, e)
        self._close()
        return False

    def _close(self):
        if self.ser:
            try:
                self.ser.close()
            except Exception:
                pass
        self.ser = None

    def _found(self, dev):
        if dev != self.dev:
            log.info("Лампа найдена: %s", dev)
        self.dev = dev
        if dev != self.hint:
            self.hint = dev
            self.on_port(dev)
        self._push(status="ok", port=dev)
        return True

    def _find(self, everything=False):
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        self.known = {p.device for p in ports}
        order = [d for d in dict.fromkeys((self.dev, self.hint)) if d and d in self.known]
        if everything or not order:
            order += [p.device for p in ports if p.device not in order and looks_like_arduino(p)]
        self._push(status="search")
        busy = perm = None
        for dev in order:
            r = self._handshake(dev)
            if r is True:
                return self._found(dev)
            if r == "busy" and not busy and dev in (self.dev, self.hint):
                busy = dev                    # «занят» говорим только про порт, где лампа уже была
            if r == "perm" and not perm:
                perm = dev
        self.dev = None
        if perm and not busy:
            self._push(status="perm", port=perm)
            log.warning("Лампа: нет доступа к %s — пользователь не в группе dialout. Один раз: "
                        "sudo ./system-setup.sh (или sudo usermod -aG dialout $USER), потом перезайди в систему", perm)
            return False
        self._push(status="busy" if busy else "none", port=busy)
        log.info("Лампа не найдена (проверено портов: %d%s)", len(order), i18n.tr(", busy ", ", зайнятий ") + str(busy) if busy else "")
        return False

    def _hotplug(self):
        """Смотрим только список портов: новый порт проверяем, пропавший — значит лампу выдернули."""
        from serial.tools import list_ports
        ports = list(list_ports.comports())
        now = {p.device for p in ports}
        new = [p for p in ports if p.device not in self.known]
        self.known = now
        if self.dev and self.dev not in now:
            log.info("Лампа отключена (%s пропал)", self.dev)
            self._close()
            self.dev = None
            self._push(status="none", port=None)
        if self.state["status"] == "busy" and self.state["port"] in now:
            # порт лампы держит другая программа (монитор порта в Arduino IDE) — ждём, когда отпустит
            r = self._handshake(self.state["port"])
            if r is True:
                self._found(self.state["port"])
            elif r is False:
                self._push(status="none", port=None)
        if self.state["status"] in ("none", "busy", "perm"):
            for p in new:
                if (p.device == self.hint or looks_like_arduino(p)) and self._handshake(p.device) is True:
                    self._found(p.device)
                    break
