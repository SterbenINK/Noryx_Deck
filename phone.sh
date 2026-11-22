#!/usr/bin/env bash
# Noryx — телефон-киоск: телефон по USB (отладка включена) становится экраном только для дека.
#   ./phone.sh            — настроить (приложение, убрать лишнее, киоск)
#   ./phone.sh off | on   — выключить / включить киоск;  ./phone.sh release — отпустить телефон
#   ./phone.sh restore    — вернуть убранные приложения
set -u
cd "$(dirname "$(readlink -f "$0")")" || exit 1
PY=python3
[ -x .venv/bin/python ] && PY=.venv/bin/python
exec "$PY" server/phone.py "$@"
