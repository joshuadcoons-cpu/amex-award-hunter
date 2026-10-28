<#
Registers a daily Windows Task Scheduler job that runs scripts\run_daily_scan.bat.

Run from an elevated or normal PowerShell prompt in the project folder:
    powershell -ExecutionPolicy Bypass -File scripts\setup_windows_task.ps1 -Time 07:30

The computer must be on and connected to the internet at the scheduled time.
The task does not need the dashboard or a browser to be open.
#>
param(
    [string]$Time = "07:30",
    [string]$TaskName = "Amex Award Hunter Daily Scan"
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$bat = Join-Path $projectRoot "scripts\run_daily_scan.bat"
if (-not (Test-Path $bat)) { throw "Could not find $bat" }

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$bat`"" -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Daily -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2) `
    -MultipleInstances IgnoreNew -RestartCount 2 -RestartInterval (New-TimeSpan -Minutes 15)

if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
}
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings `
    -Description "Searches Seats.aero for business-class awards and sends alerts (Amex Award Hunter)." | Out-Null

Write-Host "Registered task '$TaskName' to run daily at $Time."
Write-Host "Test it now with:  Start-ScheduledTask -TaskName '$TaskName'"
Write-Host "Output is appended to $projectRoot\logs\scan_output.log"
