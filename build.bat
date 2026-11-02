@echo off
rem Build dist\NetMonitor.exe  (ASCII only, no goto labels: safe for any code page / line ending)
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH. Install Python 3.8+ and tick "Add to PATH".
  pause
  exit /b 1
)

echo [1/3] Installing PyInstaller ...
python -m pip install -q --upgrade pyinstaller certifi
if errorlevel 1 (
  echo [ERROR] pip install pyinstaller failed.
  pause
  exit /b 1
)

echo [2/3] Closing running instance ...
taskkill /f /im NetMonitor.exe >nul 2>nul

echo [3/3] Building ...
python -m PyInstaller --noconfirm --clean StockWatcher.spec
if errorlevel 1 (
  echo [ERROR] Build failed, see messages above.
  pause
  exit /b 1
)

echo.
if exist "dist\NetMonitor.exe" echo [OK] dist\NetMonitor.exe
if not exist "dist\NetMonitor.exe" echo [ERROR] dist\NetMonitor.exe not found.
echo.
pause
