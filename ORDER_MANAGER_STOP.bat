@echo off
setlocal EnableExtensions
cd /d "%~dp0"

title Order Manager Stop

echo.
echo ========================================
echo  Order Manager Stop
echo ========================================
echo.

echo Stopping server...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-server.ps1"
if errorlevel 1 (
  echo.
  echo Failed to stop the server.
  echo.
  pause
  exit /b 1
)

echo.
echo Server stop command completed.
echo.
pause
