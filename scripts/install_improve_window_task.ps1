# repoint_task.ps1 -- re-point Claude-FoulerImproveLoop at the coordinated
# improve-window wrapper instead of the raw lowload runner (which only ever hit
# the lease-block). Keeps it low-frequency, RAM-gated, S4U, ExecutionTimeLimit
# ~1h so a hang can never keep the ladder down.
$ErrorActionPreference = "Stop"
$taskName = "Claude-FoulerImproveLoop"
$proj = "D:\Projects\fouler-play"
$wrapper = "$proj\scripts\run_improve_window.ps1"

$t = Get-ScheduledTask -TaskName $taskName

$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$wrapper`" -Battles 40 -MinFreeGB 3.5" `
    -WorkingDirectory $proj

# Preserve the existing trigger(s): every ~2h. (We do not widen frequency.)
$triggers = $t.Triggers

# Principal: S4U so it runs without an interactive session.
$principal = New-ScheduledTaskPrincipal -UserId "ryanj" -LogonType S4U -RunLevel Limited

# Settings: hard 1h cap so a hang can't keep the ladder down; one instance only.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -MultipleInstances IgnoreNew -StartWhenAvailable

Set-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings | Out-Null

Write-Output "=== re-pointed $taskName ==="
$nt = Get-ScheduledTask -TaskName $taskName
$nt.Actions | Format-List Execute, Arguments, WorkingDirectory
$nt.Principal | Format-List UserId, LogonType, RunLevel
$nt.Settings | Format-List ExecutionTimeLimit, MultipleInstances, Enabled
Write-Output "--- triggers ---"
$nt.Triggers | Format-List
