@echo off
rem Noryx - start. The first start also downloads what it needs (see README.txt).
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
title Noryx
if not exist "server\deck.py" goto notunpacked
if not exist logs mkdir logs
set "PY=%~dp0python\python.exe"
set "PYW=%~dp0python\pythonw.exe"
set "FIRST="
if not exist "%PYW%" goto setup
rem libraries are installed and match requirements.txt - just start
if not exist "python\deck-ready.txt" goto setup
fc /b "requirements.txt" "python\deck-ready.txt" >nul 2>&1
if not errorlevel 1 goto run

:setup
set "FIRST=1"
rem an update over a running deck: stop it first, its libraries are about to be replaced
if exist "%PY%" call :stopdeck
call "%~dp0server\setup.bat"
if errorlevel 1 goto failed

:run
start "" "%PYW%" "%~dp0server\deck.py"
echo  Starting Noryx...
for /l %%i in (1,1,60) do (
  "%PY%" -c "import urllib.request as u;u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8765/',timeout=2)" >nul 2>&1
  if not errorlevel 1 goto started
  ping -n 2 127.0.0.1 >nul
)
echo.
echo  Noryx did not start. Details: logs\server.log
echo.
pause
exit /b 1

:started
if not defined FIRST exit /b 0
echo.
echo  ======================================================================
echo   Noryx is running. Its icon is in the tray, next to the clock:
echo   right click it - open the deck, start with Windows, exit.
echo  ======================================================================
echo.
echo  Phone (Android, same Wi-Fi as this PC):
echo    1. Send Noryx.apk from this folder to the phone and install it.
echo    2. Open the app - it finds this PC by itself.
echo  Or open this address in the phone browser:
"%PY%" -c "import socket as S;s=S.socket(2,2);s.connect(('10.255.255.255',1));print('       http://'+s.getsockname()[0]+':8765')" 2>nul
echo.
echo  The phone cannot connect? Run Firewall.bat. More in README.txt
echo.
pause
exit /b 0

:failed
echo.
echo  Setup did not finish. Check the internet connection and run Start.bat again.
echo  Details: logs\install.log
echo.
pause
exit /b 1

:notunpacked
echo.
echo  Unpack the whole archive first (right click the zip - Extract All),
echo  then run Start.bat from the unpacked folder.
echo.
pause
exit /b 1

:stopdeck
"%PY%" -c "import urllib.request as u;u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8765/quit',timeout=2)" >nul 2>&1
if not errorlevel 1 ping -n 4 127.0.0.1 >nul
exit /b 0
