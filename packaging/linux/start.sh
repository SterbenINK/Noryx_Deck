#!/usr/bin/env bash
# Noryx — запуск. Первый раз сам ставит библиотеки в папку .venv рядом (интернет нужен один раз).
# Ничего не ставит в систему и не просит root. Остановить: значок в трее → «Выход» или ./stop.sh
set -u
# язык сообщений — как в настройках дека (data/config.json), до первого запуска — как в системе
UK=0
if [ -f "$(dirname "$(readlink -f "$0")")/data/config.json" ]; then
  grep -q '"lang": *"uk"' "$(dirname "$(readlink -f "$0")")/data/config.json" && UK=1
else
  case "${LANG:-}" in uk*) UK=1 ;; esac
fi
t() { if [ "$UK" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
cd "$(dirname "$(readlink -f "$0")")" || exit 1
DIR="$PWD"
VPY="$DIR/.venv/bin/python"
URL="http://127.0.0.1:8765"
mkdir -p logs
LOG="$DIR/logs/install.log"

need_setup() {
  [ -x "$VPY" ] || return 0
  [ -f .venv/deck-ready.txt ] || return 0
  [ "$(cat requirements-linux.txt)" = "$(cat .venv/deck-ready.txt)" ] || return 0
  return 1
}

# запустили двойным кликом (без терминала), а нужна установка — откроемся в терминале, чтобы было видно
if [ ! -t 1 ] && need_setup; then
  for t in konsole gnome-terminal kgx xfce4-terminal mate-terminal tilix alacritty kitty foot xterm; do
    command -v "$t" >/dev/null 2>&1 || continue
    case "$t" in
      gnome-terminal|kgx|tilix) exec "$t" -- "$0" "$@" ;;
      xfce4-terminal|mate-terminal) exec "$t" -x "$0" "$@" ;;
      kitty|foot) exec "$t" "$0" "$@" ;;
      *) exec "$t" -e "$0" "$@" ;;
    esac
  done
fi

say() { printf ' %s\n' "$*"; }
ask_close() { [ -t 0 ] && { echo; read -r -p " $(t "Нажми Enter, чтобы закрыть окно" "Натисни Enter, щоб закрити вікно") " _; }; }
deck_up() { "$VPY" -c "import urllib.request as u;u.build_opener(u.ProxyHandler({})).open('$URL/',timeout=2)" >/dev/null 2>&1; }

setup() {
  echo
  say "$(t "Noryx — подготовка (первый запуск или обновление)." "Noryx — підготовка (перший запуск або оновлення).")"
  say "$(t "Библиотеки ставятся только в папку .venv рядом: ~60 МБ, 1–2 минуты. В систему ничего не ставится." "Бібліотеки ставляться лише в теку .venv поруч: ~60 МБ, 1–2 хвилини. У систему нічого не ставиться.")"
  echo
  echo "===== $(date '+%F %T') setup start" >>"$LOG"
  local py
  py="$(command -v python3 || true)"
  if [ -z "$py" ]; then
    say "$(t "Не найден python3." "Не знайдено python3.") Fedora / Nobara: sudo dnf install python3"
    return 1
  fi
  if ! "$py" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    say "$(t "Нужен Python 3.10 или новее, сейчас:" "Потрібен Python 3.10 або новіший, зараз:") $("$py" -V 2>&1)"
    return 1
  fi
  "$py" -V >>"$LOG" 2>&1
  # обновление поверх работающего дека: сначала остановить, его библиотеки сейчас заменятся
  if [ -x "$VPY" ] && "$VPY" -c "import urllib.request as u;u.build_opener(u.ProxyHandler({})).open('$URL/quit',timeout=2)" >/dev/null 2>&1; then
    sleep 2
  fi
  if [ ! -x "$VPY" ]; then
    say "[1/2] $(t "Окружение Python" "Середовище Python") (.venv)"
    if ! "$py" -m venv .venv >>"$LOG" 2>&1; then
      rm -rf .venv
      say "$(t "Не создалось окружение Python." "Не створилося середовище Python.") Fedora / Nobara: sudo dnf install python3-pip ;"
      say "Debian / Ubuntu: sudo apt install python3-venv"
      return 1
    fi
  fi
  say "[2/2] $(t "Библиотеки" "Бібліотеки")"
  if ! "$VPY" -m pip install --disable-pip-version-check --only-binary=:all: --log "$LOG" -r requirements-linux.txt; then
    say "$(t "Проверенные версии не встали под этот Python" "Перевірені версії не встали під цей Python") ($("$VPY" -V 2>&1)) — $(t "ставлю свежие." "ставлю свіжі.")"
    "$VPY" -m pip install --disable-pip-version-check --only-binary=:all: --log "$LOG" \
      aiohttp psutil Pillow numpy soundfile dbus-fast pulsectl || return 1
  fi
  cp requirements-linux.txt .venv/deck-ready.txt
  echo "===== $(date '+%F %T') setup done" >>"$LOG"
}

menu_entry() {
  # «Noryx» в меню приложений (только для этого пользователя)
  "$VPY" - "$DIR" <<'PY' >/dev/null 2>&1
import sys
from pathlib import Path
d = Path(sys.argv[1])
sys.path.insert(0, str(d / "server"))
import i18n, lx_tray
i18n.set_lang(i18n.config_lang(d))
lx_tray.menu_entry(create=True)
PY
}

hints() {
  # подсказки после первой установки: брандмауэр, хоткеи, звук
  local note=0
  if command -v firewall-cmd >/dev/null 2>&1 && timeout 5 firewall-cmd --state >/dev/null 2>&1; then
    local ports
    if ports="$(timeout 5 firewall-cmd --list-ports 2>/dev/null)"; then
      if ! "$VPY" - "$ports" <<'PY'; then
import sys
def ok(port, proto):
    for item in sys.argv[1].split():
        rng, _, pr = item.partition("/")
        lo, _, hi = rng.partition("-")
        if pr == proto and lo.isdigit() and int(lo) <= port <= int(hi or lo):
            return True
    return False
sys.exit(0 if ok(8765, "tcp") and ok(8766, "udp") else 1)
PY
        say "• $(t "Брандмауэр может не пустить телефон — один раз:" "Брандмауер може не пустити телефон — один раз:") sudo ./system-setup.sh"
        note=1
      fi
    fi
  fi
  if [ ! -w /dev/uinput ]; then
    say "• $(t "Кнопки-хоткеи: нет доступа к виртуальной клавиатуре — один раз:" "Кнопки-хоткеї: немає доступу до віртуальної клавіатури — один раз:") sudo ./system-setup.sh"
    note=1
  fi
  if command -v ldconfig >/dev/null 2>&1 && ! ldconfig -p 2>/dev/null | grep -q 'libpulse-simple.so.0'; then
    say "• $(t "Микшер и саундпад: нет libpulse" "Мікшер і саундпад: немає libpulse") — Fedora / Nobara: sudo dnf install pulseaudio-libs"
    note=1
  fi
  [ "$note" = 1 ] && say "  ($(t "подробности — в README.txt" "подробиці — у README_UA.txt"))"
}

FIRST=0
if need_setup; then
  FIRST=1
  if ! setup; then
    echo "===== $(date '+%F %T') setup FAILED" >>"$LOG"
    echo
    say "$(t "Установка не прошла. Проверь интернет и запусти ./start.sh ещё раз. Подробности:" "Встановлення не пройшло. Перевір інтернет і запусти ./start.sh ще раз. Подробиці:") logs/install.log"
    ask_close
    exit 1
  fi
fi

# сервер — отдельно от терминала: закроешь окно, а дек продолжит работать
if command -v setsid >/dev/null 2>&1; then
  setsid -f "$VPY" "$DIR/server/deck.py" </dev/null >/dev/null 2>&1
else
  nohup "$VPY" "$DIR/server/deck.py" </dev/null >/dev/null 2>&1 &
fi
[ "$FIRST" = 1 ] && say "$(t "Запускаю" "Запускаю") Noryx…"
up=0
for _ in $(seq 1 60); do
  if deck_up; then up=1; break; fi
  sleep 1
done
if [ "$up" != 1 ]; then
  say "Noryx $(t "не запустился. Подробности:" "не запустився. Подробиці:") logs/server.log"
  ask_close
  exit 1
fi
[ "$FIRST" = 1 ] || exit 0

menu_entry
IP="$("$VPY" -c "import socket as S;s=S.socket(2,2);s.connect(('10.255.255.255',1));print(s.getsockname()[0])" 2>/dev/null)"
echo
say "======================================================================"
say " Noryx $(t "работает. Значок — в трее у часов: правый клик — меню" "працює. Значок — у треї біля годинника: правий клік — меню")"
say " $(t "(открыть дек, запускать при входе в систему, лог, обновление, выход)." "(відкрити дек, запускати під час входу в систему, лог, оновлення, вихід).")"
say " $(t "В меню приложений появился «Noryx» — так его и запускай." "У меню застосунків з'явився «Noryx» — так його й запускай.")"
say "======================================================================"
echo
say "$(t "Телефон (Android, тот же Wi-Fi):" "Телефон (Android, той самий Wi-Fi):")"
say "  1. $(t "Скинь на телефон Noryx.apk из этой папки и установи." "Скинь на телефон Noryx.apk з цієї теки й встанови.")"
say "  2. $(t "Открой приложение — оно само найдёт этот ПК." "Відкрий застосунок — він сам знайде цей ПК.")"
say "$(t "Или открой в браузере телефона:" "Або відкрий у браузері телефона:")  http://${IP:-<IP>}:8765"
echo
hints
ask_close
exit 0
