# Installer for HERMES-FoulerMatchupABReport (2026-07-04, claude)
# Registers the daily matchup A/B verdict reporter: S4U (ryanj), daily 06:30
# + AtStartup, RunLevel Limited. Idempotent (-Force).
$ErrorActionPreference = 'Stop'
$taskName = 'HERMES-FoulerMatchupABReport'
$repo = 'D:\Projects\fouler-play'
$wrapper = Join-Path $repo 'scripts\matchup_ab_report.ps1'
if (-not (Test-Path $wrapper)) { throw "wrapper not found: $wrapper" }

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $wrapper + '"') `
    -WorkingDirectory $repo
$trigDaily = New-ScheduledTaskTrigger -Daily -At '06:30'
$trigBoot  = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId 'ryanj' -LogonType S4U -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 10)

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigDaily, $trigBoot `
    -Principal $principal -Settings $settings `
    -Description 'Daily matchup-memory A/B verdict: analyze_matchup_ab.py -> logs\matchup_ab_verdicts.log (measurement never goes dark).' `
    -Force | Out-Null

$t = Get-ScheduledTask -TaskName $taskName
Write-Output ("REGISTERED: " + $t.TaskName + " state=" + $t.State + " logon=" + $t.Principal.LogonType + " runlevel=" + $t.Principal.RunLevel + " user=" + $t.Principal.UserId)
