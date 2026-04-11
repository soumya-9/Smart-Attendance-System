@echo off
setlocal

cd /d "%~dp0"

set "PYTHON_EXE=C:\Users\soumy\AppData\Local\Programs\Python\Python310\python.exe"

if not exist "%PYTHON_EXE%" (
    echo Python was not found at:
    echo %PYTHON_EXE%
    echo.
    echo Update start_app.cmd with the correct Python path and try again.
    pause
    exit /b 1
)

echo Starting Flask app from:
echo %CD%
echo.

"%PYTHON_EXE%" app.py

if errorlevel 1 (
    echo.
    echo The Flask app exited with an error.
    pause
)
