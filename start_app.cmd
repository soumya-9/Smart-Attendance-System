@echo off
setlocal

cd /d "%~dp0"

set "PREFERRED_PYTHON=C:\Users\soumy\AppData\Local\Programs\Python\Python310\python.exe"
set "PYTHON_CMD="
set "PYTHON_ARGS="

if exist "%PREFERRED_PYTHON%" (
    set "PYTHON_CMD=%PREFERRED_PYTHON%"
) else (
    where py >nul 2>nul
    if not errorlevel 1 (
        set "PYTHON_CMD=py"
        set "PYTHON_ARGS=-3.10"
    ) else (
        where python >nul 2>nul
        if not errorlevel 1 (
            set "PYTHON_CMD=python"
        )
    )
)

if not defined PYTHON_CMD (
    echo Python was not found.
    echo Install Python 3.10+ or update start_app.cmd with the correct interpreter path.
    pause
    exit /b 1
)

echo Starting Flask app from:
echo %CD%
echo.

"%PYTHON_CMD%" %PYTHON_ARGS% app.py

if errorlevel 1 (
    echo.
    echo The Flask app exited with an error.
    pause
)
