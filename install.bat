@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist logs mkdir logs
set LOG=logs\install.log
echo [%date% %time%] install start > "%LOG%"

set PY=
where py >nul 2>nul && set PY=py -3
if "%PY%"=="" where python >nul 2>nul && set PY=python
if "%PY%"=="" (
  echo Python not found >> "%LOG%"
  echo Python not found. Install Python and tick "Add to PATH".
  pause
  exit /b 1
)
%PY% --version >> "%LOG%" 2>&1

if not exist .venv\Scripts\python.exe (
  echo Creating .venv ...
  %PY% -m venv .venv >> "%LOG%" 2>&1
)
echo Updating pip ...
.venv\Scripts\python -m pip install --upgrade pip >> "%LOG%" 2>&1

for /f "usebackq eol=# delims=" %%p in ("requirements.txt") do (
  echo Installing %%p ...
  echo ----- %%p >> "%LOG%"
  .venv\Scripts\python -m pip install "%%p" >> "%LOG%" 2>&1 || echo FAILED %%p >> "%LOG%"
)

echo [%date% %time%] install done >> "%LOG%"
findstr /b "FAILED" "%LOG%" >nul && (echo Some packages FAILED - see logs\install.log) || (echo All packages installed.)
pause
