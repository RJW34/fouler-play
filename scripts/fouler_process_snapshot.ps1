$ErrorActionPreference = "Continue"

$Repo = "D:\Projects\fouler-play"
$TruthDir = Join-Path $Repo "devstream\truth"
$Out = Join-Path $TruthDir "process-status.json"
New-Item -ItemType Directory -Force -Path $TruthDir | Out-Null

$Rows = @()
$Processes = Get-CimInstance Win32_Process |
    Where-Object {
        ($_.CommandLine -like "*D:\Projects\fouler-play*") -or
        ($_.CommandLine -like "*run.py*--bot-mode search_ladder*")
    }

foreach ($Proc in $Processes) {
    $Cmd = [string]$Proc.CommandLine
    $IsBattleRunner = (
        ($Cmd -like "*run.py*") -and
        ($Cmd -like "*--bot-mode*search_ladder*") -and
        ($Cmd -like "*--ps-username*LEBOTJAMESXD00N*")
    )
    $IsObsHttp = (
        ($Cmd -like "*streaming*serve_obs_page.py*") -or
        ($Cmd -like "*serve_obs_page.py*")
    )
    $IsSupervisor = (
        ($Cmd -like "*scripts/devstream_session.py*supervise*") -or
        ($Cmd -like "*scripts\devstream_session.py*supervise*")
    )
    $Rows += [ordered]@{
        pid = [int]$Proc.ProcessId
        parentPid = [int]$Proc.ParentProcessId
        name = [string]$Proc.Name
        commandLine = $Cmd
        isBattleRunner = [bool]$IsBattleRunner
        isObsHttp = [bool]$IsObsHttp
        isSupervisor = [bool]$IsSupervisor
    }
}

$BattlePids = @($Rows | Where-Object { $_.isBattleRunner } | ForEach-Object { $_.pid })
$ObsPids = @($Rows | Where-Object { $_.isObsHttp } | ForEach-Object { $_.pid })
$SupervisorPids = @($Rows | Where-Object { $_.isSupervisor } | ForEach-Object { $_.pid })

function Get-LogicalRootPids {
    param([object[]]$Pids)
    $PidSet = @{}
    foreach ($ProcId in $Pids) {
        $PidSet[[int]$ProcId] = $true
    }
    $ParentByPid = @{}
    foreach ($Row in $Rows) {
        $ParentByPid[[int]$Row.pid] = [int]$Row.parentPid
    }
    $Roots = @{}
    foreach ($ProcId in $Pids) {
        $Current = [int]$ProcId
        while ($ParentByPid.ContainsKey($Current) -and $PidSet.ContainsKey([int]$ParentByPid[$Current])) {
            $Current = [int]$ParentByPid[$Current]
        }
        $Roots[$Current] = $true
    }
    return @($Roots.Keys | Sort-Object)
}

$BattleRootPids = @(Get-LogicalRootPids -Pids $BattlePids)
$SupervisorRootPids = @(Get-LogicalRootPids -Pids $SupervisorPids)

$Payload = [ordered]@{
    schemaVersion = "fouler-process-status/v1"
    generatedAt = (Get-Date).ToUniversalTime().ToString("o")
    machine = "JIGGLYPUFF"
    repo = $Repo
    battleRunnerAlive = ($BattlePids.Count -gt 0)
    battleRunnerProcessCount = [int]$BattlePids.Count
    battleRunnerPids = $BattlePids
    battleRunnerLogicalCount = [int]$BattleRootPids.Count
    battleRunnerRootPids = $BattleRootPids
    obsHttpAlive = ($ObsPids.Count -gt 0)
    obsHttpProcessCount = [int]$ObsPids.Count
    obsHttpPids = $ObsPids
    supervisorAlive = ($SupervisorPids.Count -gt 0)
    supervisorProcessCount = [int]$SupervisorPids.Count
    supervisorPids = $SupervisorPids
    supervisorLogicalCount = [int]$SupervisorRootPids.Count
    supervisorRootPids = $SupervisorRootPids
    processCount = [int]$Rows.Count
    processes = $Rows
}

$Json = $Payload | ConvertTo-Json -Depth 6
$Utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($Out, $Json, $Utf8NoBom)
Write-Host ("PROCESS_STATUS|" + $Out)
Write-Host ("BATTLE_RUNNER_ALIVE|" + $Payload.battleRunnerAlive)
Write-Host ("BATTLE_RUNNER_COUNT|" + $Payload.battleRunnerProcessCount)
Write-Host ("BATTLE_RUNNER_LOGICAL_COUNT|" + $Payload.battleRunnerLogicalCount)
