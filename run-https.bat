@echo off
REM Boot Souly over HTTPS on port 8443.
REM
REM This exists for one reason: iOS only hands the microphone to a page served
REM over HTTPS (or localhost). Over plain http://192.168.x.x Safari refuses
REM getUserMedia, so the iPad can do everything except talk to Souly.
REM
REM First time only:
REM   1. run.bat                                   (plain HTTP, port 8000)
REM   2. On the iPad:  http://<laptop-ip>:8000/ca.crt
REM   3. Install the downloaded profile, THEN turn it on under
REM      Settings > General > About > Certificate Trust Settings
REM   4. Stop run.bat and start this instead
REM
REM After the laptop's IP changes, re-run make_cert.py and restart. The iPad
REM does NOT need touching again - the CA it trusts stays the same.

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No virtual environment found in .venv
    pause
    exit /b 1
)

call ".venv\Scripts\activate.bat"

if not exist "certs\souly.crt" (
    echo No certificate found. Making one now...
    python scripts\make_cert.py
    if errorlevel 1 (
        pause
        exit /b 1
    )
)

echo.
echo   Souly starting over HTTPS.
echo   On this laptop:  https://localhost:8443/student
echo   On the iPad:     https://^<laptop-ip^>:8443/student
echo   Stop with Ctrl+C
echo.

python -m uvicorn app.main:app --host 0.0.0.0 --port 8443 --reload ^
    --ssl-keyfile certs\souly.key --ssl-certfile certs\souly.crt
pause
