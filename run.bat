@echo off
cd /d "%~dp0"
set "PY=py -3"
where py >nul 2>nul || set "PY=python"
if not exist .venv (
  %PY% -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
start "" pythonw main.py
