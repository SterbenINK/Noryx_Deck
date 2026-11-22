@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
if not exist .venv\Scripts\pythonw.exe (
  echo Run install.bat first.
  pause
  exit /b 1
)
.venv\Scripts\python -c "import pystray" 1>nul 2>nul || (
  echo Installing tray support, one moment...
  .venv\Scripts\python -m pip install pystray >> logs\install.log 2>&1
)
.venv\Scripts\python -c "import numpy, sounddevice, soundfile" 1>nul 2>nul || (
  echo Installing soundpad support, one moment...
  .venv\Scripts\python -m pip install numpy sounddevice soundfile >> logs\install.log 2>&1
)
start "" ".venv\Scripts\pythonw.exe" "server\deck.py"
