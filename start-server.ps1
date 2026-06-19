$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Port = if ($env:PORT) { $env:PORT } else { "3000" }
$HostAddress = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$LogFile = Join-Path $ProjectRoot "order-workflow-server.log"
$ErrorLogFile = Join-Path $ProjectRoot "order-workflow-server.err.log"
$PidFile = Join-Path $ProjectRoot "order-workflow-server.pid"

function Resolve-Python {
    $knownPaths = @(
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

if (Test-Path $PidFile) {
    $existingPid = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($existingPid -and (Get-Process -Id $existingPid -ErrorAction SilentlyContinue)) {
        Write-Host "Server is already running: http://$HostAddress`:$Port"
        exit 0
    }
    Remove-Item $PidFile -Force
}

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
    -FilePath $Python `
    -ArgumentList @("src/server.py") `
    -WorkingDirectory $ProjectRoot `
    -WindowStyle Hidden `
    -RedirectStandardOutput $LogFile `
    -RedirectStandardError $ErrorLogFile `
    -PassThru

$process.Id | Set-Content -Path $PidFile -Encoding ASCII
Start-Sleep -Seconds 1
if ($process.HasExited) {
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
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
    Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Write-Host "Server did not respond to health check: $HealthUrl"
    Write-Host "See: $ErrorLogFile"
    exit 1
}
Write-Host "Server started: http://$HostAddress`:$Port"
Write-Host "Process ID: $($process.Id)"
Write-Host "Log file: $LogFile"
