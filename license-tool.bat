@echo off
REM VRE License Tool — double-click this to issue license keys.
REM Interactive mode: asks for Machine ID, customer, expiry.
cd /d "%~dp0"
python license_tool.py
pause
