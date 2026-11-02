@echo off
rem Link this folder to GitHub and push (ASCII only, no goto labels).
rem Remote: https://github.com/sunnyfancore/stock_monitor.git
cd /d "%~dp0"
set REMOTE=https://github.com/sunnyfancore/stock_monitor.git

where git >nul 2>nul
if errorlevel 1 (
  echo [ERROR] git not found. Install "Git for Windows" first: https://git-scm.com/download/win
  pause
  exit /b 1
)

git config user.name >nul 2>nul
if not errorlevel 1 git config user.email >nul 2>nul
if errorlevel 1 (
  echo [ERROR] git user identity not set. Run these two commands once, then run this script again:
  echo   git config --global user.name  "your name"
  echo   git config --global user.email "you@example.com"
  pause
  exit /b 1
)

if not exist ".git" (
  echo [1/5] git init
  git init
  git symbolic-ref HEAD refs/heads/main
)

echo [2/5] remote origin = %REMOTE%
git remote get-url origin >nul 2>nul
if errorlevel 1 (
  git remote add origin %REMOTE%
) else (
  git remote set-url origin %REMOTE%
)

echo [3/5] staging files (see .gitignore)
git add -A
git diff --cached --name-only > "%TEMP%\sw_staged.txt"
findstr /i /c:".pem" /c:"log.txt" /c:"cache.json" /c:"stocks.json" /c:"token" "%TEMP%\sw_staged.txt" >nul
if not errorlevel 1 (
  echo [ERROR] sensitive-looking files are staged, aborting:
  findstr /i /c:".pem" /c:"log.txt" /c:"cache.json" /c:"stocks.json" /c:"token" "%TEMP%\sw_staged.txt"
  git reset -q
  pause
  exit /b 1
)
git status --short

echo [4/5] commit
git diff --cached --quiet
if errorlevel 1 (
  git commit -q -m "stock_monitor: native Win32 client + encrypted relay server"
) else (
  echo nothing new to commit
)

echo [5/5] push
git branch -M main
git fetch origin >nul 2>nul
git rev-parse --verify -q origin/main >nul 2>nul
if not errorlevel 1 (
  rem Remote already has commits, e.g. a README created on GitHub: merge them, local files win on conflict
  git merge origin/main --allow-unrelated-histories -X ours -m "Merge remote main" >nul 2>nul
  if errorlevel 1 git merge --abort >nul 2>nul
)
git push -u origin main
if errorlevel 1 (
  echo.
  echo [ERROR] push failed. If a login window appeared, sign in to GitHub and run this script again.
  pause
  exit /b 1
)

echo.
echo [OK] pushed to %REMOTE%
echo Next: GitHub repo - Settings - Secrets and variables - Actions: add SERVER_HOST / SERVER_USER / SERVER_SSH_KEY
echo Then run the "relay" workflow in the Actions tab. See server\README.md
pause
