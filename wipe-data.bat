@echo off
setlocal enabledelayedexpansion
title VRE AC Stock - Wipe All Data
cd /d "%~dp0"

echo.
echo  ============================================================
echo   VRE AC Stock  -  WIPE ALL DATA
echo  ============================================================
echo.
echo   This permanently deletes:
echo     - daikin_stock.xlsx   (inventory, returns, activity log)
echo     - backups\            (all backup copies)
echo     - delivery_orders\    (all archived DO photos)
echo     - ui_prefs.json       (saved UI state + staged cart)
echo.
echo   If the app is running it will be CLOSED automatically,
echo   otherwise the in-memory data would re-save after the wipe.
echo.
set /p CONFIRM=Type YES to wipe everything:
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo  Cancelled - nothing was deleted.
    pause
    exit /b 1
)

echo.
set "APPKILLED=0"

REM --- running desktop app? (it records its PID in .vre_app.pid) ---
if exist ".vre_app.pid" (
    set /p APPPID=<.vre_app.pid
    if defined APPPID (
        tasklist /FI "PID eq !APPPID!" 2>nul | findstr /I "python" >nul
        if !errorlevel! equ 0 (
            echo   App is running ^(PID !APPPID!^) - closing it...
            taskkill /PID !APPPID! /F /T >nul 2>&1
            set "APPKILLED=1"
        )
    )
)

REM --- fallback: standalone backend.py owns port 8000 ---
for /f "tokens=5" %%P in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":8000 "') do (
    echo   Backend is listening on port 8000 ^(PID %%P^) - closing it...
    taskkill /PID %%P /F >nul 2>&1
    set "APPKILLED=1"
)

if "!APPKILLED!"=="1" (
    echo   App closed. Waiting for file handles to release...
    timeout /t 2 /nobreak >nul
) else (
    echo   App is not running - safe to wipe.
)

echo.
echo  Wiping...

REM --- main workbook ---
if exist "daikin_stock.xlsx" (
    del /f /q "daikin_stock.xlsx" 2>nul
    if exist "daikin_stock.xlsx" (
        echo.
        echo  [FAILED] daikin_stock.xlsx is still locked by another process.
        echo  Close whatever has it open ^(Excel, the app^) and run again.
        pause
        exit /b 1
    )
    echo   - deleted daikin_stock.xlsx
) else (
    echo   - daikin_stock.xlsx already gone
)

REM --- backups folder contents ---
if exist "backups" (
    del /f /q "backups\*.*" 2>nul
    echo   - emptied backups\
)

REM --- archived delivery-order photos ---
if exist "delivery_orders" (
    del /f /q "delivery_orders\*.*" 2>nul
    echo   - emptied delivery_orders\
)

REM --- saved UI state / staged cart ---
if exist "ui_prefs.json" (
    del /f /q "ui_prefs.json"
    echo   - deleted ui_prefs.json
)

REM --- stale app pid file ---
if exist ".vre_app.pid" (
    del /f /q ".vre_app.pid"
    echo   - deleted .vre_app.pid
)

echo.
echo  ============================================================
echo   Done - app is back to a clean slate.
echo   Reopen it and you are ready for a fresh test run.
echo  ============================================================
echo.
pause
