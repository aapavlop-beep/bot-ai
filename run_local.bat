@echo off
setlocal
cd /d "%~dp0"

echo === BOT-AI local run ===
echo.

if not exist .env (
    echo .env not found. Starting setup...
    echo.
    call setup_local.bat
    if errorlevel 1 exit /b 1
)

echo Checking Python dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo ERROR: Failed to install Python dependencies.
    pause
    exit /b 1
)

echo.
echo Starting bot...
python -m app.main
pause
