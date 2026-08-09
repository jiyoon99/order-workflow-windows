$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $ProjectRoot "order-workflow-server.pid"
$WatchdogPidFile = Join-Path $ProjectRoot "order-workflow-watchdog.pid"
$StopFile = Join-Path $ProjectRoot "order-workflow-server.stop"

function Remove-PidFile {
    if (-not (Test-Path $PidFile)) {
        return
    }

    try {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction Stop
    } catch {
        Write-Host "Cannot remove pid file: $PidFile"
        Write-Host "The next startup will overwrite it if the file can be written."
        Write-Host "Original error: $($_.Exception.Message)"
    }
}

if (-not (Test-Path $PidFile) -and -not (Test-Path $WatchdogPidFile)) {
    Write-Host "No server pid file found."
    exit 0
}

Set-Content -LiteralPath $StopFile -Value "stop" -Encoding ASCII

$ServerPid = Get-Content $PidFile -ErrorAction SilentlyContinue
if ($ServerPid -and (Get-Process -Id $ServerPid -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $ServerPid
    Write-Host "Server stopped."
} else {
    Write-Host "Server process is not running."
}

Remove-PidFile

$WatchdogPid = Get-Content $WatchdogPidFile -ErrorAction SilentlyContinue
if ($WatchdogPid) {
    for ($attempt = 0; $attempt -lt 20; $attempt++) {
        if (-not (Get-Process -Id $WatchdogPid -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Milliseconds 250
    }
    if (Get-Process -Id $WatchdogPid -ErrorAction SilentlyContinue) {
        Stop-Process -Id $WatchdogPid -Force -ErrorAction SilentlyContinue
    }
}
Remove-Item -LiteralPath $WatchdogPidFile -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue
