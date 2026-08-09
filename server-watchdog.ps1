$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = $args[0]
$Port = if ($env:PORT) { $env:PORT } else { "3000" }
$HostAddress = if ($env:HOST) { $env:HOST } else { "0.0.0.0" }
$PidFile = Join-Path $ProjectRoot "order-workflow-server.pid"
$WatchdogPidFile = Join-Path $ProjectRoot "order-workflow-watchdog.pid"
$StopFile = Join-Path $ProjectRoot "order-workflow-server.stop"
$LogFile = Join-Path $ProjectRoot "order-workflow-server.log"
$ErrorLogFile = Join-Path $ProjectRoot "order-workflow-server.err.log"
$WatchdogLog = Join-Path $ProjectRoot "order-workflow-watchdog.log"
$LogMaxBytes = if ($env:LOG_MAX_BYTES) { [long]$env:LOG_MAX_BYTES } else { 10MB }
$LogBackupCount = if ($env:LOG_BACKUP_COUNT) { [int]$env:LOG_BACKUP_COUNT } else { 5 }

function Rotate-LogFile([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path) -or (Get-Item -LiteralPath $Path).Length -lt $LogMaxBytes) {
        return
    }
    for ($index = $LogBackupCount; $index -ge 1; $index--) {
        $source = if ($index -eq 1) { $Path } else { "$Path.$($index - 1)" }
        $destination = "$Path.$index"
        if (Test-Path -LiteralPath $source) {
            Move-Item -LiteralPath $source -Destination $destination -Force
        }
    }
}

function Write-WatchdogLog([string]$Message) {
    Rotate-LogFile $WatchdogLog
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $Message
    Add-Content -LiteralPath $WatchdogLog -Value $line -Encoding UTF8
}

if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    Write-WatchdogLog "Watchdog stopped: Python executable was not found: $Python"
    exit 2
}

Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue
$PID | Set-Content -LiteralPath $WatchdogPidFile -Encoding ASCII
$env:PORT = $Port
$env:HOST = $HostAddress
$env:SERVER_LOG_FILE = $LogFile
$env:SERVER_ERROR_LOG_FILE = $ErrorLogFile
$env:LOG_MAX_BYTES = [string]$LogMaxBytes
$env:LOG_BACKUP_COUNT = [string]$LogBackupCount

try {
    while (-not (Test-Path -LiteralPath $StopFile)) {
        try {
            $server = Start-Process -FilePath $Python `
                -ArgumentList @("src/server.py") `
                -WorkingDirectory $ProjectRoot `
                -WindowStyle Hidden `
                -PassThru
            $server.Id | Set-Content -LiteralPath $PidFile -Encoding ASCII
            Write-WatchdogLog "Server started (PID $($server.Id))."
            $server.WaitForExit()
            $exitCode = $server.ExitCode
            Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue

            if (Test-Path -LiteralPath $StopFile) {
                Write-WatchdogLog "Server stopped by operator (exit code $exitCode)."
                break
            }

            Write-WatchdogLog "Server exited unexpectedly (exit code $exitCode). Restarting in 3 seconds."
        } catch {
            Write-WatchdogLog "Failed to launch or monitor server: $($_.Exception.Message). Retrying in 3 seconds."
        }
        Start-Sleep -Seconds 3
    }
} finally {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $WatchdogPidFile -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue
}
