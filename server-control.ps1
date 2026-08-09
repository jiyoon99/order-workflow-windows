param(
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "Order Workflow Server 24x7"
$HealthUrl = "http://127.0.0.1:3000/api/health"
$PidFile = Join-Path $ProjectRoot "order-workflow-server.pid"
$WatchdogPidFile = Join-Path $ProjectRoot "order-workflow-watchdog.pid"

function Get-ServerStatus {
    $taskState = "Not installed"
    try {
        $taskState = (Get-ScheduledTask -TaskName $TaskName -ErrorAction Stop).State
    } catch { }

    $serverPid = Get-Content -LiteralPath $PidFile -ErrorAction SilentlyContinue
    $watchdogPid = Get-Content -LiteralPath $WatchdogPidFile -ErrorAction SilentlyContinue
    $healthy = $false
    $uptime = 0
    $networkIp = (Get-NetIPConfiguration -ErrorAction SilentlyContinue |
        Where-Object { $_.IPv4DefaultGateway -and $_.IPv4Address } |
        Select-Object -First 1 -ExpandProperty IPv4Address).IPAddress
    try {
        $health = Invoke-RestMethod -Uri $HealthUrl -TimeoutSec 2
        $healthy = $health.ok -eq $true
        $uptime = [int]$health.uptimeSeconds
    } catch { }

    Write-Host ""
    Write-Host "========================================"
    Write-Host " Order Manager Server Status"
    Write-Host "========================================"
    Write-Host (" Service task : {0}" -f $taskState)
    Write-Host (" Health       : {0}" -f $(if ($healthy) { "ONLINE" } else { "OFFLINE" })) -ForegroundColor $(if ($healthy) { "Green" } else { "Red" })
    Write-Host (" Server PID   : {0}" -f $(if ($serverPid) { $serverPid } else { "-" }))
    Write-Host (" Watchdog PID : {0}" -f $(if ($watchdogPid) { $watchdogPid } else { "-" }))
    Write-Host (" Uptime       : {0:00}d {1:00}h {2:00}m {3:00}s" -f [math]::Floor($uptime / 86400), [math]::Floor(($uptime % 86400) / 3600), [math]::Floor(($uptime % 3600) / 60), ($uptime % 60))
    Write-Host " Local URL    : http://127.0.0.1:3000"
    Write-Host (" Network URL  : {0}" -f $(if ($networkIp) { "http://${networkIp}:3000" } else { "Unavailable" }))
    Write-Host "========================================"
    return $healthy
}

switch ($Action) {
    "start" {
        $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        if ($task) {
            Remove-Item (Join-Path $ProjectRoot "order-workflow-server.stop") -Force -ErrorAction SilentlyContinue
            Start-ScheduledTask -TaskName $TaskName
        } else {
            & (Join-Path $ProjectRoot "start-server.ps1")
        }
        Start-Sleep -Seconds 2
        $null = Get-ServerStatus
    }
    "stop" {
        & (Join-Path $ProjectRoot "stop-server.ps1")
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        Start-Sleep -Milliseconds 500
        $null = Get-ServerStatus
    }
    "status" { $null = Get-ServerStatus }
}
