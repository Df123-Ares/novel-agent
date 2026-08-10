@echo off
title Novel-Agent
cd /d "%~dp0"

:: Kill existing process on port 7860
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7860" ^| findstr "LISTENING"') do taskkill /PID %%a /F >nul 2>&1

:: Start server in a background window (keeps log visible)
start "Novel-Agent-Server" cmd /c "python webui.py"

:: Wait for port 7860 to start listening (max ~60s)
set /a tries=0
:waitloop
netstat -ano | findstr ":7860" | findstr "LISTENING" >nul
if %errorlevel%==0 goto up
set /a tries+=1
if %tries% geq 60 goto up
timeout /t 1 /nobreak >nul
goto waitloop

:up
start "" http://127.0.0.1:7860
echo Novel-Agent is running on http://127.0.0.1:7860
echo Close the "Novel-Agent-Server" window to stop.
pause