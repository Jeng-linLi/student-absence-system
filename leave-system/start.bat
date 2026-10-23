@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 啟動線上請假系統...
set "LEAVE_DEBUG=1"
"C:\Users\Johnny\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe" app.py
pause
