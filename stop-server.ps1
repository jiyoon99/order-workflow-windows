$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $ProjectRoot "order-workflow-server.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "No server pid file found."
    exit 0
}

$ServerPid = Get-Content $PidFile -ErrorAction SilentlyContinue
if ($ServerPid -and (Get-Process -Id $ServerPid -ErrorAction SilentlyContinue)) {
    Stop-Process -Id $ServerPid
    Write-Host "Server stopped."
} else {
    Write-Host "Server process is not running."
}

Remove-Item $PidFile -Force
