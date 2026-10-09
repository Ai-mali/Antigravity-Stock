@echo off
REM VRE License Tool — double-click to open the license generator window.
REM Console mode instead:  python license_tool.py menu
cd /d "%~dp0"
where pythonw >nul 2>nul
if %ERRORLEVEL% equ 0 (
    start "" pythonw license_tool.py
) else (
    python license_tool.py
    pause
)
