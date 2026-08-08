@echo off
title Novel-Agent
cd /d "%~dp0"

:: Kill existing process on port 7860
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7860" ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1

:: Start UI
echo Starting Novel-Agent on http://127.0.0.1:7860 ...
start "" http://127.0.0.1:7860
python app_ui.py
pause