@echo off
title AC Stock Tracker (Desktop App)
cd /d "%~dp0"
echo Starting AC Stock Tracker in Standalone Desktop Window mode...
python backend.py --desktop
pause
