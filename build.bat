@echo off
setlocal
cd /d "%~dp0"

set "PY=py -3"
where py >nul 2>nul || set "PY=python"
%PY% --version >nul 2>nul
if errorlevel 1 (
  echo Python 3.10 or newer is required. Get it from https://www.python.org/downloads/
  echo Tick "Add python.exe to PATH" during install, then run this file again.
  pause
  exit /b 1
)

if not exist .venv (
  echo Creating virtual environment...
  %PY% -m venv .venv || goto :fail
)
call .venv\Scripts\activate.bat

echo Installing build dependencies...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt pyinstaller || goto :fail

echo Building ZeroLink.exe...
pyinstaller --noconfirm --clean zerolink.spec || goto :fail

echo.
echo Done. Your app is in: %~dp0dist\ZeroLink\ZeroLink.exe
echo You can move the whole dist\ZeroLink folder anywhere, e.g. C:\Program Files\ZeroLink
start "" "%~dp0dist\ZeroLink"
pause
exit /b 0

:fail
echo.
echo Build failed. Scroll up for the error.
pause
exit /b 1
