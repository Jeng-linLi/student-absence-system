@echo off
chcp 65001 >nul
cd /d "%~dp0leave-system"
set "LEAVE_DEBUG=0"
"C:\Users\Johnny\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe" app.py
pause
