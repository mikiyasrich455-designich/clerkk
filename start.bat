@echo off
rem Clerk one-click launcher — starts the backend and opens the app.
cd /d "%~dp0"
start "" cmd /c "timeout /t 3 >nul & start http://127.0.0.1:8010"
python -m uvicorn backend:app --host 127.0.0.1 --port 8010
