@echo off
chcp 65001 > nul 2> nul
cd /d "%~dp0"

echo ========================================
echo    JA_Stock Web Console v1.0
echo    Starting server...
echo ========================================
echo.

python --version > nul 2> nul
if errorlevel 1 (
    echo [ERROR] Python not found
    pause
    exit /b 1
)

start "" /b python web_server.py --port 5000
timeout /t 3 /nobreak > nul
start http://localhost:5000

echo Done! http://localhost:5000
pause