# Greenfield continuous-ladder supervisor task registration.
# Disables the old bounded-season/lease supervisor; installs an S4U task
# (sessionless, survives reboot, runs over SSH-admin per the JIGGLY S4U rule).
$ErrorActionPreference = 'Stop'
$Root = 'D:\Projects\fouler-play'
$Py   = "$Root\.venv\Scripts\python.exe"
$TaskName = 'Fouler-LadderSupervisor'

# 1. Disable the old idle-by-design bounded-season supervisor so it can't
#    launch a competing lease-bounded runner.
foreach ($old in 'HERMES-FoulerBattleSupervisor') {
    $t = Get-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue
    if ($t) {
        try { Stop-ScheduledTask -TaskName $old -ErrorAction SilentlyContinue } catch {}
        Disable-ScheduledTask -TaskName $old | Out-Null
        Write-Output "disabled old task: $old"
    } else {
        Write-Output "old task not present: $old"
    }
}

# 2. Register the greenfield supervisor task.
$existing = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existing) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Output "removed pre-existing $TaskName"
}

$action = New-ScheduledTaskAction -Execute $Py -Argument '-u ladder_supervisor.py' -WorkingDirectory $Root
$trigger = New-ScheduledTaskTrigger -AtStartup
$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" -LogonType S4U -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -StartWhenAvailable -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -RestartCount 5 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Description 'Greenfield continuous gen9ou ladder supervisor (no lease/broker/season).' | Out-Null
Write-Output "registered $TaskName"

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 3
$info = Get-ScheduledTask -TaskName $TaskName | Get-ScheduledTaskInfo
Write-Output ("state={0} lastResult={1} lastRun={2}" -f (Get-ScheduledTask -TaskName $TaskName).State, $info.LastTaskResult, $info.LastRunTime)
