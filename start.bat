@echo off
chcp 65001 >nul
title 株価ヒートマップ
cd /d "%~dp0"
echo.
echo ブラウザで http://127.0.0.1:8765 を開きます。
echo 終了するときはこの窓を閉じてください。
echo.
python -u server.py %*
pause
