@echo off
setlocal EnableExtensions
cd /d "%~dp0"

echo === BOT-AI local setup ===
echo.
echo Configuration will be saved to .env in this folder.
echo .env is ignored by Git and must NOT be uploaded to GitHub.
echo.

set /p "BOT_TOKEN=Telegram BOT_TOKEN: "
if "%BOT_TOKEN%"=="" goto :empty

set /p "OPENAI_API_KEY=OpenAI API key: "
if "%OPENAI_API_KEY%"=="" goto :empty

set /p "OPENAI_BASE_URL=OpenAI base URL (press Enter for default): "
set /p "OPENAI_MODEL=OpenAI model (press Enter for gpt-6-astra): "
if "%OPENAI_MODEL%"=="" set "OPENAI_MODEL=gpt-6-astra"

> .env (
    echo BOT_TOKEN=%BOT_TOKEN%
    echo LOG_LEVEL=INFO
    echo APP_ENV=development
    echo OPENAI_API_KEY=%OPENAI_API_KEY%
    echo OPENAI_BASE_URL=%OPENAI_BASE_URL%
    echo OPENAI_MODEL=%OPENAI_MODEL%
    echo DATABASE_PATH=data/bot.db
)

if not exist .env goto :write_error

echo.
echo .env created successfully.
echo Now run:
echo   python -m app.main
pause
exit /b 0

:empty
echo.
echo ERROR: BOT_TOKEN and OPENAI_API_KEY are required.
pause
exit /b 1

:write_error
echo.
echo ERROR: Could not create .env
pause
exit /b 1
