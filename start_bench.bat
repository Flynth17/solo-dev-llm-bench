@echo off
REM Solo Dev LLM Bench - Windows One-Click Startup
REM This script starts the FastAPI server and opens the dashboard in your browser.

REM Determine the directory where this script is located
set "SCRIPT_DIR=%~dp0"
set "PROJECT_DIR=%SCRIPT_DIR%"

echo ========================================
echo   Solo Dev LLM Bench
echo ========================================
echo.

REM Check if Python is available
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python is not installed or not in PATH.
    echo.
    echo Please install Python 3.10+ from https://www.python.org/downloads/
    echo Make sure to check "Add Python to PATH" during installation.
    echo.
    pause
    exit /b 1
)

echo Python found. Checking dependencies...
echo.

REM Check if required packages are installed
python -c "import fastapi" >nul 2>&1
if errorlevel 1 (
    echo ERROR: Required packages are not installed.
    echo.
    echo Please run: pip install -r "%PROJECT_DIR%requirements.txt"
    echo.
    pause
    exit /b 1
)

echo Dependencies OK.
echo.

REM Create data directory if it does not exist
if not exist "%PROJECT_DIR%data" mkdir "%PROJECT_DIR%data"

REM Start the server using a Python wrapper to ensure correct path setup
echo Starting Solo Dev LLM Bench on http://127.0.0.1:8000
echo.
echo Press Ctrl+C to stop the server.
echo.

cd /d "%PROJECT_DIR%"
python "%PROJECT_DIR%\src\server_launcher.py"

REM This line is only reached if the server is stopped
echo.
echo Server stopped.
pause
