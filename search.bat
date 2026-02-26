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

REM ── Find a working Python interpreter ───────────────────────────────────────
set PYTHON_CMD=
for %%P in (py python3 python) do (
    if not defined PYTHON_CMD (
        %%P --version >nul 2>&1 && set "PYTHON_CMD=%%P"
    )
)

if not defined PYTHON_CMD (
    echo ERROR: No Python interpreter found.
    echo.
    echo Tried: py, python3, python ^- none are available on this machine.
    echo.
    echo Options:
    echo   1. Ask IT to install Python 3.
    echo   2. Use GitHub Actions instead:
    echo      Go to your repo on GitHub ^> Actions ^> Search Marvin ^> Run workflow
    echo.
    pause
    exit /b 1
)

REM ── Run the search ──────────────────────────────────────────────────────────
%PYTHON_CMD% search_marvin.py

echo.
pause
