@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
title 市場ニュース収集
cd /d "%~dp0"

if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)

echo.
echo  市場ニュース収集
echo  --------------------------------
echo  1  日次  日本株
echo  2  日次  米株
echo  3  日次  両方
echo  4  テスト（日本株・直近2営業日・LLMなし）
echo  5  バックフィル（開始日と終了日を入力）
echo  0  終了
echo.

set /p CHOICE=番号を選んでください: 

if "%CHOICE%"=="1" goto DAILY_JP
if "%CHOICE%"=="2" goto DAILY_US
if "%CHOICE%"=="3" goto DAILY_ALL
if "%CHOICE%"=="4" goto TEST
if "%CHOICE%"=="5" goto BACKFILL
if "%CHOICE%"=="0" goto END
echo 番号が正しくありません。
goto END

:DAILY_JP
echo.
echo 日本株の日次収集を開始します。
"%PY%" -u collect_market_news.py --market jp --daily
goto END

:DAILY_US
echo.
echo 米株の日次収集を開始します。
"%PY%" -u collect_market_news.py --market us --daily
goto END

:DAILY_ALL
echo.
echo 日米の日次収集を開始します。
"%PY%" -u collect_market_news.py --market all --daily
goto END

:TEST
echo.
echo 日本株の直近2営業日を LLM なしで再生成します。
set "START_DAY="
set "END_DAY="
for /f "usebackq delims=" %%A in (`"%PY%" -c "from datetime import timedelta; from news.calendar import iter_trading_days, session_date_for_daily; e=session_date_for_daily('JP'); d=iter_trading_days('JP', e-timedelta(days=20), e)[-2:]; print(d[0].isoformat()); print(d[-1].isoformat())"`) do (
  if "!START_DAY!"=="" (
    set "START_DAY=%%A"
  ) else (
    set "END_DAY=%%A"
  )
)
if "!END_DAY!"=="" (
  echo 営業日の取得に失敗しました。
  goto END
)
echo 対象: !START_DAY! 〜 !END_DAY!
"%PY%" -u collect_market_news.py --market jp --start !START_DAY! --end !END_DAY! --no-llm --sleep 0.2 --force
goto END

:BACKFILL
echo.
set /p MKT=市場 jp / us / all : 
set /p START_DAY=開始日 YYYY-MM-DD : 
set /p END_DAY=終了日 YYYY-MM-DD : 
if "!MKT!"=="" set "MKT=jp"
if "!START_DAY!"=="" (
  echo 開始日が必要です。
  goto END
)
if "!END_DAY!"=="" set "END_DAY=!START_DAY!"
echo.
echo !MKT! を !START_DAY! 〜 !END_DAY! で収集します。
"%PY%" -u collect_market_news.py --market !MKT! --start !START_DAY! --end !END_DAY!
goto END

:END
echo.
pause
