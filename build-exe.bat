@echo off
REM ============================================================
REM  Build "VRE AC Stock.exe" — one file, no console, VRE icon.
REM  Icon: exact finalized path D:\AI-Project\Devin\Icon\VRE_Zoomed_Flat.ico
REM  (falls back to the bundled copy next to this script).
REM  Output lands in dist\VRE AC Stock.exe
REM ============================================================
cd /d "%~dp0"

set "ICON=D:\AI-Project\Devin\Icon\VRE_Zoomed_Flat.ico"
if not exist "%ICON%" set "ICON=%~dp0VRE_Zoomed_Flat.ico"
if not exist "%ICON%" (
    echo [ERROR] VRE_Zoomed_Flat.ico not found in D:\AI-Project\Devin\Icon or beside this script.
    pause
    exit /b 1
)
echo Using icon: %ICON%

pyinstaller --noconfirm --clean --onefile --windowed ^
  --name "VRE AC Stock" ^
  --icon "%ICON%" ^
  --add-data "ac-stock-tracker.html;." ^
  --add-data "%ICON%;." ^
  --add-data "VRE.ico;." ^
  --hidden-import backend ^
  --hidden-import stock_store ^
  --hidden-import scanner ^
  --hidden-import excel_exports ^
  --hidden-import comet_layer ^
  --hidden-import boot_debug_kit ^
  --hidden-import uvicorn.logging ^
  --hidden-import uvicorn.loops.auto ^
  --hidden-import uvicorn.loops.asyncio ^
  --hidden-import uvicorn.protocols.http.auto ^
  --hidden-import uvicorn.protocols.http.h11_impl ^
  --hidden-import uvicorn.protocols.websockets.auto ^
  --hidden-import uvicorn.protocols.websockets.wsproto_impl ^
  --hidden-import uvicorn.protocols.websockets.websockets_impl ^
  --hidden-import uvicorn.lifespan.on ^
  --hidden-import uvicorn.lifespan.off ^
  --collect-submodules webview ^
  desktop_app.py

if errorlevel 1 (
    echo [FAILED] Build failed — see output above.
) else (
    echo.
    echo [OK] dist\VRE AC Stock.exe ready.
    echo NOTE: daikin_stock.xlsx / backups / ui_prefs.json stay NEXT TO the exe,
    echo       so keep the exe in this folder (or a folder with your data).
)
pause
