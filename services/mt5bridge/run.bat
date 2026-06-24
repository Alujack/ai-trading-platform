@echo off
REM Start the MT5 bridge on Windows. Run from services\mt5bridge.
REM First time:  python -m venv venv  &&  venv\Scripts\pip install -r requirements.txt
REM Then copy .env.example to .env and fill it in.

cd /d "%~dp0"
if not exist venv\Scripts\python.exe (
  echo [run.bat] venv not found. Create it first:
  echo     python -m venv venv
  echo     venv\Scripts\pip install -r requirements.txt
  exit /b 1
)
venv\Scripts\python.exe -m uvicorn app:app --host 0.0.0.0 --port 8800
