$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = if ($env:PORT) { $env:PORT } else { "3000" }
$HostAddress = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$LogFile = Join-Path $ProjectRoot "order-workflow-server.log"
$ErrorLogFile = Join-Path $ProjectRoot "order-workflow-server.err.log"
$PidFile = Join-Path $ProjectRoot "order-workflow-server.pid"
$WatchdogPidFile = Join-Path $ProjectRoot "order-workflow-watchdog.pid"
$StopFile = Join-Path $ProjectRoot "order-workflow-server.stop"

function Remove-StalePidFile {
    if (-not (Test-Path $PidFile)) {
        return
    }

    try {
        Remove-Item -LiteralPath $PidFile -Force -ErrorAction Stop
    } catch {
        Write-Host "Could not remove stale pid file. It will be overwritten after startup: $PidFile"
        Write-Host "Original error: $($_.Exception.Message)"
    }
}

function Resolve-Python {
    $knownPaths = @(
        (Join-Path $ProjectRoot ".runtime\python\python.exe"),
        "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe"
    )

    foreach ($path in $knownPaths) {
        if (Test-Path $path) {
            return $path
        }
    }

    $candidates = @("python", "python3")

    foreach ($candidate in $candidates) {
        try {
            $command = Get-Command $candidate -ErrorAction Stop
            if ($command.Source -like "*\Microsoft\WindowsApps\*") {
                continue
            }
            $null = & $command.Source --version 2>&1
            if ($LASTEXITCODE -eq 0) {
                return $command.Source
            }
        } catch {
            continue
        }
    }

    throw "Python 3.11+ is required. Install Python from https://www.python.org/downloads/windows/ and enable 'Add python.exe to PATH'."
}

if (Test-Path $WatchdogPidFile) {
    $existingPid = Get-Content $WatchdogPidFile -ErrorAction SilentlyContinue
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Server is already running: http://$HostAddress`:$Port"
        exit 0
    }
    Remove-Item -LiteralPath $WatchdogPidFile -Force -ErrorAction SilentlyContinue
}
Remove-StalePidFile
Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue

$Python = Resolve-Python
$env:PORT = $Port
$env:HOST = $HostAddress
$PathValue = [Environment]::GetEnvironmentVariable("Path", "Process")
if (-not $PathValue) {
    $PathValue = [Environment]::GetEnvironmentVariable("PATH", "Process")
}
if ($PathValue) {
    [Environment]::SetEnvironmentVariable("PATH", $null, "Process")
    [Environment]::SetEnvironmentVariable("Path", $PathValue, "Process")
}

$process = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ProjectRoot "server-watchdog.ps1"), $Python) `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -PassThru

try {
    $process.Id | Set-Content -Path $WatchdogPidFile -Encoding ASCII -ErrorAction Stop
} catch {
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Write-Host "Server started but pid file could not be updated: $PidFile"
    Write-Host "Close any window or editor using this folder, then run this script again."
    Write-Host "Original error: $($_.Exception.Message)"
    exit 1
}
Start-Sleep -Seconds 1
if ($process.HasExited) {
    Remove-Item $WatchdogPidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Server failed to start. See: $ErrorLogFile"
    exit 1
}

$HealthUrl = "http://127.0.0.1`:$Port/api/health"
$healthy = $false
for ($attempt = 0; $attempt -lt 10; $attempt++) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $HealthUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Milliseconds 500
    }
}
if (-not $healthy) {
    Set-Content -LiteralPath $StopFile -Value "stop" -Encoding ASCII
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $WatchdogPidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Server did not respond to health check: $HealthUrl"
    Write-Host "See: $ErrorLogFile"
    exit 1
}
Write-Host "Server started: http://$HostAddress`:$Port"
Write-Host "Watchdog process ID: $($process.Id)"
Write-Host "Log file: $LogFile"
