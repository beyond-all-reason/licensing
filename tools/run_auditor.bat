@echo off
:: BAR License Auditor launcher
:: Usage: run_auditor.bat [path\to\bar\repo]
::   If path is omitted, opens a directory-picker on startup.

cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo Python not found. Install Python 3.10+ and add it to PATH.
    pause
    exit /b 1
)

pip show PyQt6 >nul 2>&1
if errorlevel 1 (
    echo Installing PyQt6...
    pip install -r "%~dp0license_auditor\requirements.txt"
)

python -m license_auditor %1
