@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

rem ================================================================
rem  Order Manager - Operations Console
rem  Keep this file ASCII-only so it works regardless of code page.
rem ================================================================

set "APP_NAME=Order Manager"
set "CONTROL_SCRIPT=%~dp0server-control.ps1"
set "ACCESS_LOG=%~dp0order-workflow-server.log"
set "ERROR_LOG=%~dp0order-workflow-server.err.log"
set "WATCHDOG_LOG=%~dp0order-workflow-watchdog.log"
set "SERVICE_URL=http://127.0.0.1:3000"
set "HEALTH_URL=http://127.0.0.1:3000/api/health"

title %APP_NAME% ^| Operations Console
mode con: cols=82 lines=35 >nul 2>&1

if not exist "%CONTROL_SCRIPT%" (
    cls
    echo.
    echo  [FATAL] Required file was not found:
    echo          %CONTROL_SCRIPT%
    echo.
    pause
    exit /b 2
)

rem Administrative rights are required for the scheduled task controls.
fltmc >nul 2>&1
if errorlevel 1 (
    echo Requesting administrator permission...
    powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ^
      "Start-Process -FilePath '%~f0' -WorkingDirectory '%~dp0' -Verb RunAs"
    if errorlevel 1 (
        echo [ERROR] Administrator permission was not granted.
        pause
    )
    exit /b
)

:menu
cls
call :header
echo  CURRENT SERVICE STATUS
echo  ------------------------------------------------------------------------------
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CONTROL_SCRIPT%" status
if errorlevel 1 echo  [WARNING] Status check returned an error.
echo.
echo  SERVICE CONTROL                         MONITORING AND TOOLS
echo  ------------------------------------    --------------------------------------
echo   [1] Start service                       [5] Live access log
echo   [2] Stop service                        [6] Live error log
echo   [3] Restart service                     [7] Live watchdog log
echo   [4] Refresh dashboard                   [8] Open service in browser
echo                                           [9] Open application folder
echo   [0] Exit console                        [D] Run quick diagnostics
echo.
choice /c 123456789D0 /n /m "  Select an action: "

if errorlevel 11 goto close
if errorlevel 10 goto diagnostics
if errorlevel 9 goto open_folder
if errorlevel 8 goto browser
if errorlevel 7 goto watchdog_log
if errorlevel 6 goto error_log
if errorlevel 5 goto access_log
if errorlevel 4 goto menu
if errorlevel 3 goto restart_server
if errorlevel 2 goto stop_server
if errorlevel 1 goto start_server
goto menu

:start_server
call :action_header "STARTING SERVICE"
call :run_control start "Service start command completed."
goto menu

:stop_server
call :action_header "STOPPING SERVICE"
call :confirm "Stop the Order Manager service"
if errorlevel 1 goto menu
call :run_control stop "Service stop command completed."
goto menu

:restart_server
call :action_header "RESTARTING SERVICE"
call :confirm "Restart the Order Manager service"
if errorlevel 1 goto menu
echo  [1/2] Stopping service...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CONTROL_SCRIPT%" stop
if errorlevel 1 (
    call :failed "Service stop failed. Restart was cancelled."
    goto menu
)
echo.
echo  [2/2] Starting service...
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CONTROL_SCRIPT%" start
if errorlevel 1 (
    call :failed "Service failed to start. Check the error log."
    goto menu
)
call :succeeded "Service restart command completed."
goto menu

:access_log
call :open_live_log "%ACCESS_LOG%" "Order Manager - Access Log"
goto menu

:error_log
call :open_live_log "%ERROR_LOG%" "Order Manager - Error Log"
goto menu

:watchdog_log
call :open_live_log "%WATCHDOG_LOG%" "Order Manager - Watchdog Log"
goto menu

:browser
start "" "%SERVICE_URL%"
goto menu

:open_folder
start "" explorer.exe "%~dp0"
goto menu

:diagnostics
call :action_header "QUICK DIAGNOSTICS"
echo  Timestamp     : %date% %time:~0,8%
echo  Computer      : %COMPUTERNAME%
echo  User          : %USERDOMAIN%\%USERNAME%
echo  Project root  : %~dp0
echo.
echo  Health endpoint
echo  ------------------------------------------------------------------------------
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "try { $r=Invoke-RestMethod -Uri '%HEALTH_URL%' -TimeoutSec 3; $r ^| ConvertTo-Json -Depth 5 } catch { Write-Host ('FAILED: ' + $_.Exception.Message) -ForegroundColor Red; exit 1 }"
echo.
echo  Listening process on TCP 3000
echo  ------------------------------------------------------------------------------
powershell.exe -NoLogo -NoProfile -Command ^
  "$c=Get-NetTCPConnection -LocalPort 3000 -State Listen -ErrorAction SilentlyContinue; if($c){$c ^| Format-Table LocalAddress,LocalPort,OwningProcess -AutoSize}else{Write-Host 'No listening process found.' -ForegroundColor Yellow}"
echo.
pause
goto menu

:run_control
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%CONTROL_SCRIPT%" %~1
if errorlevel 1 (
    call :failed "Command failed. Check the error and watchdog logs."
    exit /b 1
)
call :succeeded "%~2"
exit /b 0

:open_live_log
if not exist "%~1" (
    call :action_header "LOG NOT AVAILABLE"
    echo  The log file does not exist yet:
    echo  %~1
    echo.
    pause
    exit /b 1
)
start "%~2" powershell.exe -NoExit -NoLogo -NoProfile -ExecutionPolicy Bypass -Command ^
  "$Host.UI.RawUI.WindowTitle='%~2'; Write-Host 'Live log - close this window when finished.' -ForegroundColor Cyan; Write-Host 'File: %~1'; Write-Host ''; Get-Content -LiteralPath '%~1' -Tail 100 -Wait"
exit /b 0

:confirm
echo.
choice /c YN /n /m "  %~1? [Y/N]: "
if errorlevel 2 exit /b 1
exit /b 0

:succeeded
echo.
echo  [OK] %~1
echo.
timeout /t 2 /nobreak >nul
exit /b 0

:failed
echo.
echo  [ERROR] %~1
echo.
pause
exit /b 1

:action_header
cls
call :header
echo  %~1
echo  ------------------------------------------------------------------------------
echo.
exit /b 0

:header
echo.
echo  ============================================================================== 
echo    ORDER MANAGER  ^|  OPERATIONS CONSOLE
echo  ============================================================================== 
echo    Host: %COMPUTERNAME%  ^|  Administrator: YES
echo  ============================================================================== 
echo.
exit /b 0

:close
cls
echo.
echo  Order Manager operations console closed.
timeout /t 1 /nobreak >nul
exit /b 0
