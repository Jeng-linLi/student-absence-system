@echo off
chcp 65001 >nul
cd /d "%~dp0leave-system"
set "LEAVE_DEBUG=0"
set "LEAVE_DEMO_QUICK=1"
set "PY=C:\Users\Johnny\.workbuddy-ai\binaries\python\envs\default\Scripts\python.exe"
if not exist "%PY%" set "PY=python"
"%PY%" app.py
pause
