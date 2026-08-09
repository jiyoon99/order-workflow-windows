$ErrorActionPreference = "Stop"

$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$TaskName = "Order Workflow Server 24x7"
$WatchdogScript = Join-Path $ProjectRoot "server-watchdog.ps1"
$Python = Join-Path $ProjectRoot ".runtime\python\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Bundled Python was not found: $Python"
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Run this installer as Administrator."
}

# Stop the manually launched copy before Task Scheduler takes ownership.
& (Join-Path $ProjectRoot "stop-server.ps1")

$arguments = '-NoProfile -ExecutionPolicy Bypass -File "{0}" "{1}"' -f $WatchdogScript, $Python
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument $arguments -WorkingDirectory $ProjectRoot
$trigger = New-ScheduledTaskTrigger -AtStartup
$taskPrincipal = New-ScheduledTaskPrincipal -UserId "SYSTEM" -LogonType ServiceAccount -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $taskPrincipal -Settings $settings -Force | Out-Null

# An always-on server must not enter sleep or hibernation while connected to AC power.
powercfg.exe /change standby-timeout-ac 0
powercfg.exe /change hibernate-timeout-ac 0

Start-ScheduledTask -TaskName $TaskName

$healthUrl = "http://127.0.0.1:3000/api/health"
for ($attempt = 0; $attempt -lt 20; $attempt++) {
    Start-Sleep -Milliseconds 500
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $healthUrl -TimeoutSec 2
        if ($response.StatusCode -eq 200) {
            Write-Host "24x7 service installed and healthy: $healthUrl"
            Write-Host "Scheduled task: $TaskName"
            exit 0
        }
    } catch { }
}

throw "The scheduled task was installed, but the health check failed: $healthUrl"
