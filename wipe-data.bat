@echo off
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
echo   IMPORTANT: Close the app first. If the backend is running it
echo   holds the workbook in memory and will re-save deleted data.
echo.
set /p CONFIRM=Type YES to wipe everything:
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo  Cancelled - nothing was deleted.
    pause
    exit /b 1
)

echo.
echo  Wiping...

REM --- main workbook (this is the one that must not be locked) ---
if exist "daikin_stock.xlsx" (
    del /f /q "daikin_stock.xlsx" 2>nul
    if exist "daikin_stock.xlsx" (
        echo.
        echo  [FAILED] daikin_stock.xlsx is locked - the app is still running.
        echo  Close it fully, then run this script again.
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
