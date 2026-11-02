@echo off
rem Run from source if Python is installed (no 3rd-party packages needed), else use the exe.
cd /d "%~dp0"
where pythonw >nul 2>nul
if not errorlevel 1 (
  start "" pythonw app.py
  exit /b
)
if exist "dist\NetMonitor.exe" (
  start "" "dist\NetMonitor.exe"
  exit /b
)
echo Python not found and dist\NetMonitor.exe missing. Run build.bat first.
pause
