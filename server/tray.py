"""Значок в трее: открыть дек, автозапуск с Windows, лог, перезапуск, выход."""
import logging
import os
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import i18n

log = logging.getLogger("tray")

ROOT = Path(__file__).resolve().parent.parent
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_NAME = "Noryx"
OLD_RUN_NAME = "ClaudeDeck"      # прежнее название: такой автозапуск переносим на новое имя
IS_WIN = sys.platform == "win32"
DETACHED = 0x00000008 | 0x00000200   # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP


def pythonw():
    """pythonw.exe рядом с текущим интерпретатором (без консольного окна)."""
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.exists() else exe)


def autorun_command():
    return f'"{pythonw()}" "{ROOT / "server" / "deck.py"}" --quiet'


def is_autorun():
    if not IS_WIN:
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_NAME)
            return True
    except OSError:
        pass
    try:                                  # автозапуск под прежним названием — переносим на новое
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, OLD_RUN_NAME)
        set_autorun(True)
        return True
    except OSError:
        return False


def set_autorun(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, RUN_NAME, 0, winreg.REG_SZ, autorun_command())
        else:
            try:
                winreg.DeleteValue(k, RUN_NAME)
            except OSError:
                pass
        try:
            winreg.DeleteValue(k, OLD_RUN_NAME)
        except OSError:
            pass
    log.info("Автозапуск с Windows: %s", i18n.tr("включён", "увімкнено") if on else i18n.tr("выключен", "вимкнено"))


def spawn_restart():
    """Запускает новую копию сервера; она подождёт, пока старая освободит порт."""
    subprocess.Popen([pythonw(), str(ROOT / "server" / "deck.py"), "--restart"], cwd=str(ROOT),
                     creationflags=DETACHED if IS_WIN else 0, close_fds=True)


def _image():
    import logo
    return logo.image(64)


class Tray:
    def __init__(self, url_local, url_phone, on_quit, on_restart, greet=True):
        self.url_local, self.url_phone = url_local, url_phone
        self.on_quit, self.on_restart = on_quit, on_restart
        self.greet = greet
        self.icon = None
        self.uk = lambda: False     # язык меню: дек подставляет «украинский ли сейчас»
        self.upd_label = lambda: None   # «Обновить дек» / «Обновить дек: …» (есть новая версия) / «Обновление…»
        self.on_update = None
        self.on_update_file = None

    def tr(self, ru, uk):
        return uk if self.uk() else ru

    def lang_changed(self):
        """Язык сменили в настройках дека — перерисовать меню."""
        if self.icon:
            try:
                self.icon.update_menu()
            except Exception:
                log.exception("меню трея")

    def refresh(self):
        """Состояние обновлений поменялось — перерисовать меню."""
        self.lang_changed()

    def notify(self, text):
        """Всплывающее уведомление у часов (обновление вышло / встало / не встало)."""
        if self.icon:
            try:
                self.icon.notify(text, "Noryx")
            except Exception:
                log.exception("уведомление трея")

    def _upd(self):
        try:
            return self.upd_label()
        except Exception:
            log.exception("пункт обновления в меню трея")      # меню остаётся, просто без этого пункта
            return None

    def _update(self):
        if self.on_update:
            log.info("Обновление из трея")
            self.on_update()

    def _update_file(self):
        if self.on_update_file:
            log.info("Обновление из файла (трей)")
            self.on_update_file()

    def start(self):
        if not IS_WIN:
            return False
        try:
            import pystray
        except Exception as e:
            log.warning("Значок в трее недоступен (%s). Остановить сервер: stop.bat", e)
            return False
        P = pystray
        menu = P.Menu(
            # подписи — функциями: pystray перечитывает их при update_menu() после смены языка
            P.MenuItem(lambda item: self.tr("Открыть дек", "Відкрити дек"), lambda icon, item: webbrowser.open(self.url_local), default=True),
            P.MenuItem(lambda item: self.tr("С телефона: ", "З телефона: ") + self.url_phone, lambda icon, item: None, enabled=False),
            P.Menu.SEPARATOR,
            P.MenuItem(lambda item: self._upd() or "", lambda icon, item: self._update(), visible=lambda item: bool(self._upd())),
            P.MenuItem(lambda item: self.tr("Обновить из файла…", "Оновити з файлу…"), lambda icon, item: self._update_file(),
                       visible=lambda item: bool(self._upd())),
            P.Menu.SEPARATOR,
            P.MenuItem(lambda item: self.tr("Запускать вместе с Windows", "Запускати разом з Windows"), lambda icon, item: self._toggle_autorun(),
                       checked=lambda item: is_autorun()),
            P.MenuItem(lambda item: self.tr("Открыть лог", "Відкрити лог"), lambda icon, item: self._open(ROOT / "logs" / "server.log")),
            P.MenuItem(lambda item: self.tr("Открыть папку дека", "Відкрити папку дека"), lambda icon, item: self._open(ROOT)),
            P.Menu.SEPARATOR,
            P.MenuItem(lambda item: self.tr("Перезапустить", "Перезапустити"), lambda icon, item: self._restart()),
            P.MenuItem(lambda item: self.tr("Выход", "Вихід"), lambda icon, item: self._quit()),
        )
        self.icon = P.Icon("Noryx", _image(), f"Noryx\n{self.url_phone}", menu)
        threading.Thread(target=self._run, name="tray", daemon=True).start()
        return True

    def _run(self):
        try:
            import ctypes
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # для ShellExecute (открыть лог/папку/браузер)
        except Exception:
            pass
        def setup(icon):
            icon.visible = True
            if self.greet:
                try:
                    icon.notify(self.tr("Сервер работает", "Сервер працює"), "Noryx")
                except Exception:
                    pass
        try:
            self.icon.run(setup)
        except Exception:
            log.exception("трей")

    @staticmethod
    def _open(path):
        try:
            os.startfile(str(path))
        except Exception:
            log.exception("Не открылось: %s", path)

    def _toggle_autorun(self):
        try:
            set_autorun(not is_autorun())
        except Exception:
            log.exception("автозапуск")

    def _restart(self):
        log.info("Перезапуск из трея")
        self.stop()
        self.on_restart()

    def _quit(self):
        log.info("Выход из трея")
        self.stop()
        self.on_quit()

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
