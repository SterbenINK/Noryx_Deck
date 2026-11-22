@echo off
setlocal EnableExtensions DisableDelayedExpansion
rem Noryx - stop (the same as tray icon - Exit)
"%~dp0python\python.exe" -c "import urllib.request as u;u.build_opener(u.ProxyHandler({})).open('http://127.0.0.1:8765/quit',timeout=3);print(' Noryx stopped.')" 2>nul
if errorlevel 1 echo  Noryx is not running.
ping -n 3 127.0.0.1 >nul
