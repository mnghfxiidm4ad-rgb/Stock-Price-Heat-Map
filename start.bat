@echo off
chcp 65001 >nul
title StockChronicle.app
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo.
echo Open http://127.0.0.1:8765 in your browser.
echo Close this window to stop the server.
echo.

"%PY%" -c "import numpy, pandas" >nul 2>&1
if errorlevel 1 (
  echo Installing packages from requirements.txt ...
  "%PY%" -m pip install -r requirements.txt
  if errorlevel 1 (
    echo Install failed. Check requirements.txt
    pause
    exit /b 1
  )
)

"%PY%" -u server.py %*
pause
