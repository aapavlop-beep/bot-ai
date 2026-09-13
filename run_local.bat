@echo off
cd /d "%~dp0"

if not defined BOT_TOKEN (
    echo BOT_TOKEN is not configured in Windows.
    echo Run setup_local.bat first.
    echo.
    pause
    exit /b 1
)

python -m app.main
pause
