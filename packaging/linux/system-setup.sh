#!/usr/bin/env bash
# Noryx — один раз, с правами root (sudo ./system-setup.sh). Всё по желанию, дек работает и без этого:
#   1. порты для телефона в брандмауэре: 8765/TCP (дек) и 8766/UDP (приложение ищет ПК);
#   2. доступ к /dev/uinput — виртуальная клавиатура для кнопок-хоткеев (правило udev, как у Steam);
#   3. группа dialout — доступ к Arduino для лампы.
set -u
# язык сообщений — как в настройках дека (data/config.json), до первого запуска — как в системе
UK=0
if [ -f "$(dirname "$(readlink -f "$0")")/data/config.json" ]; then
  grep -q '"lang": *"uk"' "$(dirname "$(readlink -f "$0")")/data/config.json" && UK=1
else
  case "${LANG:-}" in uk*) UK=1 ;; esac
fi
t() { if [ "$UK" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
if [ "$(id -u)" -ne 0 ]; then
  exec sudo -- "$0" "$@"
fi
USER_NAME="${SUDO_USER:-}"
[ -z "$USER_NAME" ] && [ -n "${PKEXEC_UID:-}" ] && USER_NAME="$(id -nu "$PKEXEC_UID")"

echo " [1/3] $(t "Брандмауэр" "Брандмауер")"
if command -v firewall-cmd >/dev/null 2>&1 && firewall-cmd --state >/dev/null 2>&1; then
  firewall-cmd --permanent --add-port=8765/tcp --add-port=8766/udp >/dev/null && firewall-cmd --reload >/dev/null \
    && echo "     firewalld: $(t "порты 8765/tcp и 8766/udp открыты" "порти 8765/tcp і 8766/udp відкрито")" || echo "     firewalld: $(t "не получилось открыть порты" "не вдалося відкрити порти")"
elif command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 8765/tcp >/dev/null && ufw allow 8766/udp >/dev/null && echo "     ufw: $(t "порты 8765/tcp и 8766/udp открыты" "порти 8765/tcp і 8766/udp відкрито")"
else
  echo "     $(t "брандмауэр не включён — ничего делать не нужно" "брандмауер не ввімкнено — нічого робити не треба")"
fi

echo " [2/3] $(t "Виртуальная клавиатура для хоткеев" "Віртуальна клавіатура для хоткеїв") (/dev/uinput)"
cat > /etc/udev/rules.d/60-noryx-uinput.rules <<'EOF'
# Noryx: кнопки-хоткеи — виртуальная клавиатура для пользователя за компьютером (как у Steam Input)
KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"
EOF
echo uinput > /etc/modules-load.d/noryx-uinput.conf
modprobe uinput 2>/dev/null
udevadm control --reload-rules 2>/dev/null
udevadm trigger --action=change --sysname-match=uinput 2>/dev/null
echo "     $(t "правило udev добавлено" "правило udev додано")"

echo " [3/3] $(t "Доступ к Arduino (лампа)" "Доступ до Arduino (лампа)")"
GROUP="dialout"
getent group dialout >/dev/null || GROUP="uucp"
if [ -n "$USER_NAME" ] && getent group "$GROUP" >/dev/null; then
  usermod -aG "$GROUP" "$USER_NAME" && echo "     $USER_NAME $(t "добавлен в группу" "додано до групи") $GROUP"
else
  echo "     $(t "пропущено (не понял, какой пользователь: запусти через sudo из своей сессии)" "пропущено (не зрозумів, який користувач: запусти через sudo зі своєї сесії)")"
fi

echo
echo " $(t "Готово. Выйди из системы и зайди снова — тогда заработает доступ к Arduino." "Готово. Вийди з системи й зайди знову — тоді запрацює доступ до Arduino.")"
