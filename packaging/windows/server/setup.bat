@echo off
rem Noryx - setup, called by Start.bat on the first start.
rem Downloads portable Python and the libraries into the "python" folder next to Start.bat.
rem Nothing is installed into Windows: delete the folder and nothing is left.
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0.."
set "PYVER=3.14.9"
set "PYURL=https://www.python.org/ftp/python/%PYVER%/python-%PYVER%-embed-amd64.zip"
set "PY=%CD%\python\python.exe"
set "LOG=%CD%\logs\install.log"
if not exist logs mkdir logs
>>"%LOG%" echo ===== %date% %time% setup start

if /i "%PROCESSOR_ARCHITECTURE%"=="x86" if not defined PROCESSOR_ARCHITEW6432 (
  echo  Noryx needs 64-bit Windows 10 or 11.
  >>"%LOG%" echo 32-bit Windows is not supported
  goto fail
)

echo.
echo  Noryx - getting ready (first start or after an update).
echo  Python and libraries go into this folder only: about 40 MB, 1-3 minutes.
echo  Nothing is installed into Windows.
echo.

if exist "%PY%" goto pth
echo  [1/4] Python %PYVER%
if not exist python mkdir python
call :download "%PYURL%" "python\python.zip"
if errorlevel 1 goto fail
pushd python
tar.exe -xf python.zip >>"%LOG%" 2>&1
if errorlevel 1 powershell -NoProfile -Command "Expand-Archive -LiteralPath 'python.zip' -DestinationPath '.' -Force" >>"%LOG%" 2>&1
popd
del "python\python.zip" >nul 2>&1
if not exist "%PY%" (
  >>"%LOG%" echo python.exe is missing after unpacking
  goto fail
)

:pth
rem the embeddable Python sees installed libraries only with "import site" switched on in its ._pth file
"%PY%" -c "import pathlib;[p.write_text('\n'.join([l for l in p.read_text().splitlines() if l.strip().lstrip('#').strip()!='import site']+['import site'])+'\n') for p in pathlib.Path('python').glob('python*._pth')]" >>"%LOG%" 2>&1
if errorlevel 1 goto fail

"%PY%" -m pip --version >nul 2>&1
if not errorlevel 1 goto libs
echo  [2/4] pip
rem pip runs straight from its wheel and installs itself (the same trick get-pip.py uses)
set "PIPWHL=python\pip-26.2.2-py3-none-any.whl"
call :download "https://files.pythonhosted.org/packages/3f/06/3e4420302363fbb71be3c7f35ce042f54bfca330e3e66b3a2e3c4cca579f/pip-26.2.2-py3-none-any.whl" "%PIPWHL%"
if errorlevel 1 goto fail
"%PY%" -c "import sys;w=sys.argv[1];sys.path.insert(0,w);from pip._internal.cli.main import main;sys.exit(main(['install','--no-index','--no-warn-script-location','--disable-pip-version-check',w]))" "%PIPWHL%" >>"%LOG%" 2>&1
if errorlevel 1 goto fail
del "%PIPWHL%" >nul 2>&1

:libs
echo  [3/4] Libraries
"%PY%" -m pip install --disable-pip-version-check --no-warn-script-location --only-binary=:all: --log "%LOG%" -r requirements.txt
if errorlevel 1 goto fail
copy /y requirements.txt "python\deck-ready.txt" >nul

if exist "python\firewall-ok.txt" goto done
echo.
echo  [4/4] Windows Firewall: let the phone connect to this PC.
echo        Windows will ask for permission - click "Yes".
call "%CD%\Firewall.bat" auto
if not exist "python\firewall-ok.txt" echo        If the phone cannot connect later, run Firewall.bat.

:done
>>"%LOG%" echo ===== %date% %time% setup done
exit /b 0

:fail
>>"%LOG%" echo ===== %date% %time% setup FAILED
exit /b 1

rem ---- download URL to FILE: curl.exe (built into Windows 10/11), PowerShell if curl fails
:download
curl.exe -L --fail --retry 3 --retry-delay 2 --progress-bar -o "%~2" "%~1"
if not errorlevel 1 exit /b 0
>>"%LOG%" echo curl could not download %~1 - trying PowerShell
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue';[Net.ServicePointManager]::SecurityProtocol=[Net.SecurityProtocolType]::Tls12;Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2'" >>"%LOG%" 2>&1
if errorlevel 1 exit /b 1
exit /b 0
