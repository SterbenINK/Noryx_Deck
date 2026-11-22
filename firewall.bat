@echo off
chcp 65001 >nul
net session >nul 2>&1
if errorlevel 1 (
  echo Run this file as Administrator: right click - Run as administrator
  pause
  exit /b 1
)
netsh advfirewall firewall delete rule name="Claude Deck" >nul 2>&1
netsh advfirewall firewall delete rule name="Claude Deck discovery" >nul 2>&1
netsh advfirewall firewall delete rule name="Noryx" >nul 2>&1
netsh advfirewall firewall delete rule name="Noryx discovery" >nul 2>&1
netsh advfirewall firewall add rule name="Noryx" dir=in action=allow protocol=TCP localport=8765 profile=private,public
netsh advfirewall firewall add rule name="Noryx discovery" dir=in action=allow protocol=UDP localport=8766 profile=private,public
echo Ports opened: TCP 8765 (deck), UDP 8766 (app auto-discovery).
pause
