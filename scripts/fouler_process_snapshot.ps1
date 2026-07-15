$ErrorActionPreference = "Continue"

$Repo = (Split-Path -Parent $PSScriptRoot)
$ProgramDataRoot = if ($env:ProgramData) { $env:ProgramData } else { "C:\ProgramData" }
$TruthDir = Join-Path $ProgramDataRoot "HERMES\state\fouler"
$Out = Join-Path $TruthDir "process-status.json"
New-Item -ItemType Directory -Force -Path $TruthDir | Out-Null

function Redact-CommandLine {
    param([string]$CommandLine)
    $safe = [string]$CommandLine
    $safe = [regex]::Replace(
        $safe,
        '(?i)(--ps-password(?:=|\s+))(?:"[^"]*"|''[^'']*''|\S+)',
        '$1<redacted>'
    )
    $safe = [regex]::Replace(
        $safe,
        '(?i)((?:password|passwd|token|secret|api[_-]?key|webhook(?:_url)?)\s*[=:]\s*)(?:"[^"]*"|''[^'']*''|\S+)',
        '$1<redacted>'
    )
    return $safe
}

$Rows = @()
$EscapedRepo = [regex]::Escape($Repo)
$Processes = Get-CimInstance Win32_Process |
    Where-Object {
        ($_.CommandLine -match $EscapedRepo) -or
        ($_.CommandLine -like "*run.py*--bot-mode search_ladder*")
    }

foreach ($Proc in $Processes) {
    $Cmd = [string]$Proc.CommandLine
    $IsBattleRunner = (
        ($Cmd -like "*run.py*") -and
        ($Cmd -like "*--bot-mode*search_ladder*")
    )
    $IsObsHttp = (
        ($Cmd -like "*streaming*serve_obs_page.py*") -or
        ($Cmd -like "*serve_obs_page.py*") -or
        ($Cmd -like "*streaming*run_obs_server_service.py*") -or
        ($Cmd -like "*run_obs_server_service.py*")
    )
    $IsSupervisor = (
        ($Cmd -like "*scripts/devstream_session.py*supervise*") -or
        ($Cmd -like "*scripts\devstream_session.py*supervise*")
    )
    $Rows += [ordered]@{
        pid = [int]$Proc.ProcessId
        parentPid = [int]$Proc.ParentProcessId
        name = [string]$Proc.Name
        commandLine = (Redact-CommandLine -CommandLine $Cmd)
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
$ObsRootPids = @(Get-LogicalRootPids -Pids $ObsPids)
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
    obsHttpLogicalCount = [int]$ObsRootPids.Count
    obsHttpRootPids = $ObsRootPids
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
