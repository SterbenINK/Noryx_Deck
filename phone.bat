@echo off
rem Noryx - phone kiosk: the phone (USB, debugging on) becomes a deck-only screen.
rem   Phone.bat           - set up (install the app, remove extra apps, kiosk)
rem   Phone.bat off | on  - kiosk off / on      Phone.bat release - let the phone go
rem   Phone.bat restore   - bring the removed apps back
rem Details: README.txt (Phone).
setlocal EnableExtensions
cd /d "%~dp0"
chcp 65001 >nul
set "PYTHONIOENCODING=utf-8"
set "PY="
if exist "python\python.exe" set "PY=python\python.exe"
if not defined PY if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"
if not defined PY where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY (
  echo  Python not found: run Start.bat once first.
  pause
  exit /b 1
)
%PY% "server\phone.py" %*
set "RC=%ERRORLEVEL%"
echo.
pause
exit /b %RC%
