@echo off
chcp 65001 >nul
title Fetch stock closes

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  echo Creating .venv ...
  python -m venv .venv
  if errorlevel 1 (
    echo Failed to create .venv
    pause
    exit /b 1
  )
  set "PY=.venv\Scripts\python.exe"
)

"%PY%" -c "import yfinance, pandas, requests" >nul 2>&1
if errorlevel 1 (
  echo Installing packages. This can take a few minutes.
  "%PY%" -m pip install -U pip
  "%PY%" -m pip install -r requirements.txt -r requirements-update.txt
  if errorlevel 1 (
    echo Install failed.
    pause
    exit /b 1
  )
)

echo.
echo Fetch closing prices from Yahoo Finance
echo Japan: after 15:00 JST   US: after 16:00 ET
echo This can take 10-30 minutes.
echo --------------------------------
echo  1  Japan (JP)
echo  2  US
echo  3  Both
echo  0  Exit
echo.
set /p CHOICE=Select: 

if "%CHOICE%"=="1" goto JP
if "%CHOICE%"=="2" goto US
if "%CHOICE%"=="3" goto BOTH
goto END

:JP
echo.
echo Fetching Japan closes ...
"%PY%" -u scripts\update_quotes.py --market jp --batch-size 40 --sleep 2.0
goto DONE

:US
echo.
echo Fetching US closes ...
"%PY%" -u scripts\update_quotes.py --market us --batch-size 40 --sleep 2.0
goto DONE

:BOTH
echo.
echo Fetching Japan and US closes ...
"%PY%" -u scripts\update_quotes.py --market both --batch-size 40 --sleep 2.0
goto DONE

:DONE
if errorlevel 1 (
  echo Fetch failed.
  goto END
)
echo.
echo Saved data\jp.json and/or data\us.json
echo Run start.bat to view the heatmap.

:END
echo.
pause
