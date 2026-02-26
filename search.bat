@echo off
cd /d "%~dp0"

REM ── Load secrets from secrets.env (never commit this file) ──────────────────
if not exist secrets.env (
    echo ERROR: secrets.env not found.
    echo.
    echo Create a file called secrets.env in the same folder as this script with:
    echo   MARVIN_API_TOKEN=your_token_here
    echo   MARVIN_FULL_ACCESS_TOKEN=your_full_access_token_here
    echo   OPENAI_API_KEY=your_key_here
    echo.
    pause
    exit /b 1
)
for /f "usebackq tokens=1,* delims==" %%A in ("secrets.env") do set "%%A=%%B"

REM ── Prompt for query ────────────────────────────────────────────────────────
echo.
set /p SEARCH_QUERY=What do you want to know?
echo.

REM ── Run the search ──────────────────────────────────────────────────────────
python search_marvin.py

echo.
pause
