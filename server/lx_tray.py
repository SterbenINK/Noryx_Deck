"""Значок в трее на Linux: StatusNotifierItem + меню DBusMenu (KDE Plasma; GNOME — с расширением
AppIndicator), автозапуск при входе в систему (~/.config/autostart), уведомления рабочего стола.
Интерфейс тот же, что у tray.py для Windows."""
import asyncio
import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

import i18n
import lx_util as U

log = logging.getLogger("tray")

ROOT = Path(__file__).resolve().parent.parent
AUTOSTART = U.config_home() / "autostart" / "noryx.desktop"
MENU_FILE = U.data_home() / "applications" / "noryx.desktop"
ICON_FILE = ROOT / "data" / "noryx.png"
WATCHER = "org.kde.StatusNotifierWatcher"


def _q(s):
    """Аргумент для строки Exec= в .desktop."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$") + '"'


def autorun_command():
    return f"{_q(sys.executable)} {_q(ROOT / 'server' / 'deck.py')} --quiet"


OLD_AUTOSTART = U.config_home() / "autostart" / "claude-deck.desktop"     # прежнее название
OLD_MENU_FILE = U.data_home() / "applications" / "claude-deck.desktop"


def migrate_old_name():
    """Ярлыки под прежним названием → под новым (автозапуск остаётся, если был)."""
    try:
        if OLD_AUTOSTART.exists():
            OLD_AUTOSTART.unlink()
            set_autorun(True)
        if OLD_MENU_FILE.exists():
            OLD_MENU_FILE.unlink()
            menu_entry(create=True)
    except Exception as e:
        log.warning("Ярлыки под прежним названием: %s", e)


def is_autorun():
    return AUTOSTART.exists() or OLD_AUTOSTART.exists()


def comment():
    """Описание дека в меню приложений и автозапуске — на языке дека (без Comment[..]: те берутся по языку
    системы, а не по настройке дека)."""
    return i18n.tr("Стрим-дек на телефоне — сервер на ПК", "Стрім-дек на телефоні — сервер на ПК")


def menu_entry(create=False):
    """«Noryx» в меню приложений: start.sh создаёт его при первом запуске, дек переписывает при запуске
    и смене языка (если пользователь его не удалял)."""
    migrate_old_name()
    text = ("[Desktop Entry]\nType=Application\nName=Noryx\n"
            f"Comment={comment()}\nGenericName={i18n.tr('Стрим-дек', 'Стрім-дек')}\n"
            f"Exec={_q(ROOT / 'start.sh')}\nIcon={ICON_FILE}\nTerminal=false\nCategories=Utility;\n"
            "Keywords=deck;stream;phone;\n")
    try:
        if AUTOSTART.exists() and f"Comment={comment()}\n" not in AUTOSTART.read_text(encoding="utf-8", errors="replace"):
            set_autorun(True)
        if (not create and not MENU_FILE.exists()) or (MENU_FILE.exists() and MENU_FILE.read_text(encoding="utf-8") == text):
            return
        write_icon()
        MENU_FILE.parent.mkdir(parents=True, exist_ok=True)
        MENU_FILE.write_text(text, encoding="utf-8")
        log.info("Ярлык в меню приложений: %s", MENU_FILE)
    except Exception as e:
        log.warning("Ярлык в меню приложений: %s", e)


def set_autorun(on):
    if on:
        AUTOSTART.parent.mkdir(parents=True, exist_ok=True)
        AUTOSTART.write_text("[Desktop Entry]\nType=Application\nName=Noryx\n"
                             f"Comment={comment()}\n"
                             f"Exec={autorun_command()}\nIcon={ICON_FILE}\nTerminal=false\n"
                             "X-GNOME-Autostart-enabled=true\nX-KDE-autostart-after=panel\n", encoding="utf-8")
    else:
        for f in (AUTOSTART, OLD_AUTOSTART):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
    log.info("Автозапуск при входе в систему: %s", i18n.tr("включён", "увімкнено") if on else i18n.tr("выключен", "вимкнено"))


def spawn_restart():
    """Новая копия сервера; она подождёт, пока старая освободит порт."""
    subprocess.Popen([sys.executable, str(ROOT / "server" / "deck.py"), "--restart"], cwd=str(ROOT),
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def _image(size):
    import logo
    return logo.image(size)


def _pixmaps():
    """IconPixmap для StatusNotifierItem: ARGB32, байты в сетевом порядке."""
    out = []
    for size in (22, 32, 48, 64):
        rgba = _image(size).tobytes()
        argb = bytearray(len(rgba))
        argb[0::4], argb[1::4], argb[2::4], argb[3::4] = rgba[3::4], rgba[0::4], rgba[1::4], rgba[2::4]
        out.append([size, size, bytes(argb)])
    return out


def write_icon():
    try:
        ICON_FILE.parent.mkdir(parents=True, exist_ok=True)
        _image(256).save(ICON_FILE)                 # каждый раз: сменился логотип — сменился и значок ярлыка
    except Exception:
        log.exception("иконка")


class Tray:
    def __init__(self, url_local, url_phone, on_quit, on_restart, greet=True):
        self.url_local, self.url_phone = url_local, url_phone
        self.on_quit, self.on_restart = on_quit, on_restart
        self.greet = greet
        self.loop = None
        self.done = None
        self.menu = None
        self.sni = None
        self.uk = lambda: False     # язык меню: дек подставляет «украинский ли сейчас»
        self.upd_label = lambda: None   # «Обновить дек» / «Обновить дек: …» (есть новая версия) / «Обновление…»
        self.on_update = None
        self.on_update_file = None
        self.bus = None
        self._ready = threading.Event()
        self._ok = False

    def tr(self, ru, uk):
        try:
            return uk if self.uk() else ru
        except Exception:
            return ru

    def lang_changed(self):
        """Язык сменили в настройках дека — панель перечитает меню и подсказку."""
        def emit():
            if self.menu:
                self.menu.changed()
            if self.sni:
                self.sni.NewToolTip()
        if self.loop:
            self.loop.call_soon_threadsafe(emit)

    def refresh(self):
        """Состояние обновлений поменялось — панель перечитает меню."""
        self.lang_changed()

    def notify(self, text):
        """Уведомление рабочего стола (обновление вышло / встало / не встало)."""
        if self.loop and self.bus:
            asyncio.run_coroutine_threadsafe(self._notify(self.bus, text), self.loop)

    # ---------- действия меню ----------
    def open_deck(self):
        U.open_path(self.url_local)

    def _action(self, item):
        try:
            if item == 1:
                self.open_deck()
            elif item == 4:
                set_autorun(not is_autorun())
                if self.menu:
                    self.menu.changed()
            elif item == 5:
                U.open_path(ROOT / "logs" / "server.log")
            elif item == 6:
                U.open_path(ROOT)
            elif item == 8:
                log.info("Перезапуск из трея")
                self.on_restart()
            elif item == 9:
                log.info("Выход из трея")
                self.on_quit()
            elif item == 10 and self.on_update:
                log.info("Обновление из трея")
                self.on_update()
            elif item == 11 and self.on_update_file:
                log.info("Обновление из файла (трей)")
                self.on_update_file()
        except Exception:
            log.exception("меню трея")

    def items(self):
        try:
            upd = self.upd_label()
        except Exception:
            log.exception("пункт обновления в меню трея")      # меню остаётся, просто без этого пункта
            upd = None
        return [
            (1, {"label": self.tr("Открыть дек", "Відкрити дек")}),
            (2, {"label": self.tr("С телефона: ", "З телефона: ") + self.url_phone, "enabled": False}),
            (3, {"type": "separator"}),
            *([(10, {"label": upd}), (11, {"label": self.tr("Обновить из файла…", "Оновити з файлу…")}),
               (12, {"type": "separator"})] if upd else []),
            (4, {"label": self.tr("Запускать при входе в систему", "Запускати під час входу в систему"),
                 "toggle-type": "checkmark", "toggle-state": 1 if is_autorun() else 0}),
            (5, {"label": self.tr("Открыть лог", "Відкрити лог")}),
            (6, {"label": self.tr("Открыть папку дека", "Відкрити папку дека")}),
            (7, {"type": "separator"}),
            (8, {"label": self.tr("Перезапустить", "Перезапустити")}),
            (9, {"label": self.tr("Выход", "Вихід")}),
        ]

    # ---------- жизнь значка ----------
    def start(self):
        write_icon()
        threading.Thread(target=lambda: asyncio.run(self._main()), name="tray", daemon=True).start()
        self._ready.wait(5)
        return self._ok

    def stop(self):
        if self.loop and self.done:
            self.loop.call_soon_threadsafe(self.done.set)

    async def _main(self):
        self.loop = asyncio.get_running_loop()
        self.done = asyncio.Event()
        try:
            from dbus_fast import Message, MessageType
            from dbus_fast.aio import MessageBus
            bus = await MessageBus().connect()
        except Exception as e:
            log.warning("Значок в трее недоступен: нет сеансовой шины D-Bus (%s)", e)
            self._ready.set()
            return
        sni_cls, menu_cls = _interfaces()
        self.bus = bus
        name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.menu = menu_cls(self)
        self.sni = sni_cls(self)
        bus.export("/StatusNotifierItem", self.sni)
        bus.export("/MenuBar", self.menu)
        await bus.request_name(name)

        async def register():
            reply = await bus.call(Message(destination=WATCHER, path="/StatusNotifierWatcher", interface=WATCHER,
                                           member="RegisterStatusNotifierItem", signature="s", body=[name]))
            if reply.message_type == MessageType.ERROR:
                raise RuntimeError(reply.error_name)

        try:
            await asyncio.wait_for(register(), 3)
            self._ok = True
        except Exception as e:
            log.warning("Значок в трее недоступен (%s): в GNOME нужно расширение AppIndicator. "
                        "Остановить сервер: ./stop.sh", e)
        # панель перезапустилась (или появилась позже) — регистрируемся заново
        await bus.call(Message(destination="org.freedesktop.DBus", path="/org/freedesktop/DBus",
                               interface="org.freedesktop.DBus", member="AddMatch", signature="s",
                               body=[f"type='signal',interface='org.freedesktop.DBus',member='NameOwnerChanged',"
                                     f"arg0='{WATCHER}'"]))

        def on_msg(msg):
            if msg.member == "NameOwnerChanged" and msg.body and msg.body[0] == WATCHER and msg.body[2]:
                asyncio.ensure_future(self._reregister(register))

        bus.add_message_handler(on_msg)
        self._ready.set()
        if self._ok and self.greet:
            await self._notify(bus, self.tr("Сервер работает", "Сервер працює"))
        await self.done.wait()
        bus.disconnect()

    async def _reregister(self, register):
        await asyncio.sleep(0.5)
        try:
            await register()
            log.info("Значок в трее: панель перезапустилась — зарегистрировался заново")
        except Exception as e:
            log.info("Значок в трее: %s", e)

    async def _notify(self, bus, text):
        from dbus_fast import Message
        try:
            await asyncio.wait_for(bus.call(Message(
                destination="org.freedesktop.Notifications", path="/org/freedesktop/Notifications",
                interface="org.freedesktop.Notifications", member="Notify", signature="susssasa{sv}i",
                body=["Noryx", 0, str(ICON_FILE), "Noryx", text, [], {}, 6000])), 3)
        except Exception as e:
            log.info("Уведомление: %s", e)


def _interfaces():
    """Классы D-Bus-интерфейсов (импорт dbus_fast — только когда он точно установлен)."""
    from dbus_fast import Variant
    from dbus_fast.service import PropertyAccess, ServiceInterface, dbus_property, method, signal

    types = {"label": "s", "enabled": "b", "visible": "b", "type": "s", "toggle-type": "s", "toggle-state": "i",
             "children-display": "s"}

    def props(d):
        return {k: Variant(types[k], v) for k, v in d.items()}

    class SNI(ServiceInterface):
        def __init__(self, tray):
            super().__init__("org.kde.StatusNotifierItem")
            self.tray = tray
            self.pix = _pixmaps()

        @dbus_property(access=PropertyAccess.READ)
        def Category(self) -> "s":
            return "ApplicationStatus"

        @dbus_property(access=PropertyAccess.READ)
        def Id(self) -> "s":
            return "noryx"

        @dbus_property(access=PropertyAccess.READ)
        def Title(self) -> "s":
            return "Noryx"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "Active"

        @dbus_property(access=PropertyAccess.READ)
        def WindowId(self) -> "i":
            return 0

        @dbus_property(access=PropertyAccess.READ)
        def IconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def IconPixmap(self) -> "a(iiay)":
            return self.pix

        @dbus_property(access=PropertyAccess.READ)
        def OverlayIconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def AttentionIconName(self) -> "s":
            return ""

        @dbus_property(access=PropertyAccess.READ)
        def ToolTip(self) -> "(sa(iiay)ss)":
            return ["", [], "Noryx", self.tray.tr("С телефона: ", "З телефона: ") + self.tray.url_phone]

        @dbus_property(access=PropertyAccess.READ)
        def ItemIsMenu(self) -> "b":
            return False

        @dbus_property(access=PropertyAccess.READ)
        def Menu(self) -> "o":
            return "/MenuBar"

        @method()
        def Activate(self, x: "i", y: "i"):
            self.tray.open_deck()

        @method()
        def SecondaryActivate(self, x: "i", y: "i"):
            self.tray.open_deck()

        @method()
        def ContextMenu(self, x: "i", y: "i"):
            pass

        @method()
        def Scroll(self, delta: "i", orientation: "s"):
            pass

        @signal()
        def NewIcon(self):
            pass

        @signal()
        def NewToolTip(self):
            pass

        @signal()
        def NewStatus(self) -> "s":
            return "Active"

    class Menu(ServiceInterface):
        def __init__(self, tray):
            super().__init__("com.canonical.dbusmenu")
            self.tray = tray
            self.revision = 1

        def changed(self):
            self.revision += 1
            self.LayoutUpdated()

        def _layout(self):
            kids = [Variant("(ia{sv}av)", [i, props(p), []]) for i, p in self.tray.items()]
            return [0, props({"children-display": "submenu"}), kids]

        @dbus_property(access=PropertyAccess.READ)
        def Version(self) -> "u":
            return 3

        @dbus_property(access=PropertyAccess.READ)
        def TextDirection(self) -> "s":
            return "ltr"

        @dbus_property(access=PropertyAccess.READ)
        def Status(self) -> "s":
            return "normal"

        @dbus_property(access=PropertyAccess.READ)
        def IconThemePath(self) -> "as":
            return []

        @method()
        def GetLayout(self, parent_id: "i", depth: "i", names: "as") -> "u(ia{sv}av)":
            return [self.revision, self._layout()]

        @method()
        def GetGroupProperties(self, ids: "ai", names: "as") -> "a(ia{sv})":
            return [[i, props(p)] for i, p in self.tray.items() if not ids or i in ids]

        @method()
        def GetProperty(self, item_id: "i", name: "s") -> "v":
            p = dict(self.tray.items()).get(item_id, {})
            return props({name: p[name]})[name] if name in p else Variant("s", "")

        @method()
        def Event(self, item_id: "i", event_id: "s", data: "v", timestamp: "u"):
            if event_id == "clicked":
                self.tray._action(item_id)

        @method()
        def EventGroup(self, events: "a(isvu)") -> "ai":
            for item_id, event_id, _data, _ts in events:
                if event_id == "clicked":
                    self.tray._action(item_id)
            return []

        @method()
        def AboutToShow(self, item_id: "i") -> "b":
            return False

        @method()
        def AboutToShowGroup(self, ids: "ai") -> "aiai":
            return [[], []]

        @signal()
        def LayoutUpdated(self) -> "ui":
            return [self.revision, 0]

        @signal()
        def ItemsPropertiesUpdated(self) -> "a(ia{sv})a(ias)":
            return [[], []]

    return SNI, Menu
