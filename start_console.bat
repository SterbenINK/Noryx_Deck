@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Noryx server (console)
if not exist .venv\Scripts\python.exe (
  echo Run install.bat first.
  pause
  exit /b 1
)
set PYTHONUTF8=1
.venv\Scripts\python -u server\deck.py
echo.
echo Server stopped. Log: logs\server.log
pause
