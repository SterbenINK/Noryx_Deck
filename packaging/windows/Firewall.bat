@echo off
rem Noryx - let the phone reach this PC: TCP 8765 (deck) and UDP 8766 (the app finds the PC).
rem Only devices from your local network are allowed. Asks for administrator rights by itself.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
set "MODE=%~1"
if not defined MODE set "MODE=manual"
fltmc >nul 2>&1
if not errorlevel 1 goto admin
if /i "%~2"=="elevated" goto noadmin
echo  Asking Windows for administrator rights...
set "DECK_SELF=%~f0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$q=[char]34; try { Start-Process -FilePath $env:ComSpec -ArgumentList ('/c '+$q+$q+$env:DECK_SELF+$q+' '+$env:MODE+' elevated'+$q) -Verb RunAs -Wait -ErrorAction Stop } catch { exit 1 }"
if errorlevel 1 goto noadmin
exit /b 0

:noadmin
echo  No administrator rights - the firewall rules were not added.
if /i not "%MODE%"=="auto" pause
exit /b 1

:admin
set "PYW=%~dp0python\pythonw.exe"
set "PY=%~dp0python\python.exe"
echo  Opening Noryx ports in Windows Firewall...
rem old rules for this Python (including "block" ones from a cancelled Windows prompt)
netsh advfirewall firewall delete rule name=all program="%PYW%" >nul 2>&1
netsh advfirewall firewall delete rule name=all program="%PY%" >nul 2>&1
set "ERR="
for %%P in ("%PYW%" "%PY%") do (
  netsh advfirewall firewall add rule name="Noryx" dir=in action=allow program=%%P protocol=TCP localport=8765 remoteip=localsubnet profile=any >nul
  if errorlevel 1 set "ERR=1"
  netsh advfirewall firewall add rule name="Noryx discovery" dir=in action=allow program=%%P protocol=UDP localport=8766 remoteip=localsubnet profile=any >nul
  if errorlevel 1 set "ERR=1"
)
if defined ERR (
  echo  Could not add the firewall rules.
  pause
  exit /b 1
)
if not exist "%~dp0python\" mkdir "%~dp0python"
echo ok>"%~dp0python\firewall-ok.txt"
echo  Done: the phone can reach this PC now (TCP 8765 and UDP 8766, local network only).
if /i "%MODE%"=="auto" (ping -n 3 127.0.0.1 >nul) else (pause)
exit /b 0
