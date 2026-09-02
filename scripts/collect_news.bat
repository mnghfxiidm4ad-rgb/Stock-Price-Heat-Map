@echo off
chcp 65001 >nul
title 市場ニュース 日次収集
cd /d "%~dp0.."
if exist ".venv\Scripts\python.exe" (
  set "PY=.venv\Scripts\python.exe"
) else (
  set "PY=python"
)
echo.
echo 直近セッションの市場ニュースを収集します。
echo.
"%PY%" -u collect_market_news.py --market all --daily %*
echo.
pause
