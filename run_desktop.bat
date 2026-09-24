@echo off
cd /d "%~dp0"

:: Launch frameless desktop app cleanly with no console window
where pythonw >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" pythonw desktop_app.py
) else (
    start "" python desktop_app.py
)
