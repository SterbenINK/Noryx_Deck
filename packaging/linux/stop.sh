#!/usr/bin/env bash
# Noryx — остановить (то же, что значок в трее → «Выход»)
# язык сообщений — как в настройках дека (data/config.json), до первого запуска — как в системе
UK=0
if [ -f "$(dirname "$(readlink -f "$0")")/data/config.json" ]; then
  grep -q '"lang": *"uk"' "$(dirname "$(readlink -f "$0")")/data/config.json" && UK=1
else
  case "${LANG:-}" in uk*) UK=1 ;; esac
fi
t() { if [ "$UK" = 1 ]; then printf '%s' "$2"; else printf '%s' "$1"; fi; }
cd "$(dirname "$(readlink -f "$0")")" || exit 1
PY=.venv/bin/python
[ -x "$PY" ] || PY=python3
if "$PY" -c "import urllib.request as u;u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8765/quit',timeout=3)" 2>/dev/null; then
  echo " Noryx $(t "остановлен" "зупинено")."
else
  echo " Noryx $(t "не запущен" "не запущено")."
fi
