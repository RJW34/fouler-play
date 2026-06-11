# repoint_task.ps1 -- re-point Claude-FoulerImproveLoop at the coordinated
# improve-window wrapper instead of the raw lowload runner (which only ever hit
# the lease-block). Keeps it low-frequency, RAM-gated, S4U.
#
# ExecutionTimeLimit raised 1h -> PT1H45M (2026-06-11): the old 1h cap KILLED
# the self-play gate before it could gather the production decisive floor
# (MIN_DECISIVE=30 at ~0.41 decisive/min needs ~75-90 min of eval), so every
# window run died with ZERO verdict. PT1H45M lets the gate complete while
# staying safely UNDER the PT2H trigger interval; -MultipleInstances IgnoreNew
# additionally guarantees no overlap, and the wrapper's FINALLY block ALWAYS
# restarts the ladder on every exit path so a hang can never keep it down.
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

# Settings: PT1H45M cap (reachable gate, < PT2H trigger); one instance only.
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1 -Minutes 45) `
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
