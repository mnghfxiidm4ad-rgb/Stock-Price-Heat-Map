@echo off
chcp 65001 >nul
title Fetch JP/US price history
cd /d "%~dp0"
setlocal EnableExtensions

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

"%PY%" -c "import yfinance, pandas, requests, pyarrow" >nul 2>&1
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
echo Yahoo Finance から過去の日足を取得します。
echo 日本株 / 米株。保存先: history\  （カレンダー用）
echo 取得済みの日付はスキップし、足りない期間だけ追加します。
echo 全期間: 数時間かかることがあります。この窓は閉じないでください。
echo 1年: 市場あたり 20〜60分 目安
echo.
echo 操作: [P] 一時停止   [R] 再開   [Q] 保存して終了
echo --------------------------------
echo 市場
echo  1  日本株
echo  2  米株
echo  3  日本株と米株
echo  0  終了
echo.
set "MKT="
set /p MKT=市場を選んでください: 
if "%MKT%"=="1" (set "MARKET=jp" & goto PERIOD)
if "%MKT%"=="１" (set "MARKET=jp" & goto PERIOD)
if "%MKT%"=="2" (set "MARKET=us" & goto PERIOD)
if "%MKT%"=="２" (set "MARKET=us" & goto PERIOD)
if "%MKT%"=="3" (set "MARKET=both" & goto PERIOD)
if "%MKT%"=="３" (set "MARKET=both" & goto PERIOD)
goto END

:PERIOD
echo.
echo 期間  （Enter で全期間）
echo  1  全期間（上場来。Yahoo にある限り。銘柄により20年超）
echo  2  1年
echo  3  5年
echo  4  開始日を指定  YYYY-MM-DD
echo  0  終了
echo.
set "PER="
set /p PER=期間を選んでください（Enter=全期間）: 
if "%PER%"=="" goto FULL
if /I "%PER%"=="1" goto FULL
if "%PER%"=="１" goto FULL
if /I "%PER%"=="max" goto FULL
if /I "%PER%"=="全期間" goto FULL
if "%PER%"=="2" (set "RANGE=--period 1y" & goto RUN)
if "%PER%"=="２" (set "RANGE=--period 1y" & goto RUN)
if "%PER%"=="3" (set "RANGE=--period 5y" & goto RUN)
if "%PER%"=="３" (set "RANGE=--period 5y" & goto RUN)
if "%PER%"=="4" goto CUSTOM
if "%PER%"=="４" goto CUSTOM
goto END

:FULL
set "RANGE=--period max"
echo.
echo 全期間（上場来）で取得します。
goto RUN

:CUSTOM
echo.
set "START="
set /p START=開始日 YYYY-MM-DD: 
if "%START%"=="" goto END
set "RANGE=--start %START%"
goto RUN

:RUN
echo.
echo 取得開始  %MARKET%  %RANGE% ...
echo 進捗と日付範囲はこの窓に表示されます。
echo.
"%PY%" -u scripts\update_quotes.py --history --market %MARKET% %RANGE% --batch-size 40 --sleep 2.0
if errorlevel 1 (
  echo 取得に失敗しました。
  goto END
)
echo.
echo history\ に保存しました。
echo カレンダーを更新するには start.bat を開き直してください。

:END
echo.
pause
