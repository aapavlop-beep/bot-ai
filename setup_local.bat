@echo off
setlocal
cd /d "%~dp0"

echo === BOT-AI local setup ===
echo.
echo This setup stores configuration in your Windows user environment.
echo Secrets are NOT committed to GitHub.
echo.

set /p "BOT_TOKEN=Telegram BOT_TOKEN: "
if "%BOT_TOKEN%"=="" goto :empty
setx BOT_TOKEN "%BOT_TOKEN%" >nul

set /p "OPENAI_API_KEY=OpenAI API key: "
if "%OPENAI_API_KEY%"=="" goto :empty
setx OPENAI_API_KEY "%OPENAI_API_KEY%" >nul

set /p "OPENAI_BASE_URL=OpenAI base URL (press Enter for default): "
if not "%OPENAI_BASE_URL%"=="" setx OPENAI_BASE_URL "%OPENAI_BASE_URL%" >nul

set /p "OPENAI_MODEL=OpenAI model (press Enter for default): "
if not "%OPENAI_MODEL%"=="" setx OPENAI_MODEL "%OPENAI_MODEL%" >nul

setx LOG_LEVEL "INFO" >nul
setx APP_ENV "development" >nul
setx DATABASE_PATH "data/bot.db" >nul

echo.
echo Configuration saved to Windows user environment.
echo IMPORTANT: close this CMD window and open a new one.
echo Then run:
echo   python -m app.main
pause
exit /b 0

:empty
echo.
echo ERROR: A required value was empty. Nothing else was configured.
pause
exit /b 1
