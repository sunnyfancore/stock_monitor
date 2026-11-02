@echo off
rem Self test: syntax, imports, parsing, market hours, live quote/search APIs. Does not start the UI.
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
where python >nul 2>nul
if errorlevel 1 (
  echo [ERROR] python not found in PATH.
  pause
  exit /b 1
)
python selftest.py
echo.
pause
