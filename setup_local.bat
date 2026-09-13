@echo off
setlocal
cd /d "%~dp0"

echo === BOT-AI local setup ===
echo.
if defined BOT_TOKEN (
    echo BOT_TOKEN already exists in Windows environment.
    echo No .env file is required.
    echo.
    pause
    exit /b 0
)

echo Enter the Telegram bot token. It will be stored in your Windows user environment,
echo NOT in GitHub and NOT in the repository.
echo.
set /p "BOT_TOKEN=BOT_TOKEN: "
if "%BOT_TOKEN%"=="" (
    echo.
    echo ERROR: BOT_TOKEN is empty.
    pause
    exit /b 1
)

setx BOT_TOKEN "%BOT_TOKEN%" >nul
if errorlevel 1 (
    echo.
    echo ERROR: Could not save BOT_TOKEN to Windows environment.
    pause
    exit /b 1
)

echo.
echo BOT_TOKEN saved successfully.
echo IMPORTANT: close this CMD window and open a new one so Windows loads the variable.
echo Then run:
echo   python -m app.main
pause
