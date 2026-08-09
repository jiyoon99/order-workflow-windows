@echo off
setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0"

title Order Manager Server

set "PORT=3000"
set "HOST=0.0.0.0"

echo.
echo ========================================
echo  Order Manager Server
echo ========================================
echo.

echo [1/3] Starting server for local network access...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-server.ps1"
if errorlevel 1 (
  echo.
  echo Server failed to start.
  echo Check order-workflow-server.err.log in this folder.
  echo.
  pause
  exit /b 1
)

set "LAN_IP="
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /c:"IPv4 Address"') do (
  if not defined LAN_IP (
    set "LAN_IP=%%A"
    set "LAN_IP=!LAN_IP: =!"
  )
)

echo.
echo [2/3] Server is running.
echo.
echo This computer:
echo   http://127.0.0.1:%PORT%
echo.
echo Other computers on the same network:
if defined LAN_IP (
  echo   http://!LAN_IP!:%PORT%
) else (
  echo   Could not detect LAN IP. Run ipconfig and use the IPv4 address.
)
echo.
echo [3/3] Keep this window for the address. Closing it will not stop the server.
echo To stop the server, run stop-server.bat.
echo.
pause
