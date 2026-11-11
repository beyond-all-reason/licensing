@echo off
:: BAR License Auditor — live-reload dev launcher
:: Restarts the app automatically whenever a .py file in license_auditor/ changes.
:: Usage: dev_reload.bat [path\to\bar\repo]

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

if "%~1"=="" (
    python live_reload.py
) else (
    python live_reload.py "%~1"
)
