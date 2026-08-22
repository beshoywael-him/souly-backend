@echo off
REM Boot the Souly API on Windows.
REM Double-click this, or run it from cmd. run.sh is the Linux/Mac twin.
REM
REM Binds 0.0.0.0 so the robot tablet can reach it over the MiFi at
REM   http://<this-machine-ip>:8000/student

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtual environment found in .venv
    echo Create one first:  python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

echo.
echo   Souly starting.  Open http://localhost:8000/student
echo   API docs at      http://localhost:8000/docs
echo   Stop with Ctrl+C
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
pause
