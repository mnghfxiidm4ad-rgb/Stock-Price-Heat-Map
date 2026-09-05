@echo off
chcp 65001 >nul
title Fetch stock history
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

"%PY%" -c "import yfinance, pandas, pyarrow" >nul 2>&1
if errorlevel 1 (
  echo Installing packages from requirements-update.txt ...
  "%PY%" -m pip install -r requirements-update.txt
  if errorlevel 1 (
    echo Install failed.
    pause
    exit /b 1
  )
)

echo.
echo 全期間の日足を取得します。初回は時間がかかります。
echo 保存先: %%STOCK_DATA_ROOT%% または C:\data\日本株
echo.

"%PY%" -u fetch_history.py %*
echo.
pause
