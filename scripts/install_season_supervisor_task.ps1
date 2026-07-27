[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$SourceCommit,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^season-[0-9a-z][0-9a-z.-]{7,95}$')]
    [string]$SeasonId,
    [ValidateRange(2, 4)]
    [int]$MaxRounds = 4,
    [ValidateRange(24, 168)]
    [int]$SeasonHours = 72,
    [string]$Account = "DekuFoulerFresh",
    [string]$TeamName = "gen9/ou/fat-team-2-balance",
    [string]$RuntimeAccount = "devstream-live",
    [string]$TaskName = "DEVSTREAM-JIG-FoulerSeasonSupervisor",
    [string]$ReleaseRoot = "D:\Releases\fouler-play",
    [string]$ManifestRoot = "C:\ProgramData\Devstream\staging\fouler\manifests",
    [string]$AuthorityRoot = "C:\ProgramData\Devstream\authority\fouler",
    [string]$RuntimeRoot = "E:\DevstreamRuntime\fouler",
    [string]$EventQueueRoot = "D:\DekuEvents",
    [string]$SourceSecretEnv = "C:\ProgramData\HERMES\secrets\fouler.env",
    [string]$SourceAccountSeason = "C:\ProgramData\HERMES\authority\fouler\account-season.json",
    [string]$SourceRuntimeState = "C:\ProgramData\HERMES\state\fouler",
    [string]$BackupRoot = "E:\DevstreamRestoration",
    [switch]$Apply,
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$SourceCommit = $SourceCommit.ToLowerInvariant()
$SeasonId = $SeasonId.ToLowerInvariant()

$release = [System.IO.Path]::GetFullPath((Join-Path $ReleaseRoot $SourceCommit)).TrimEnd("\")
$manifest = [System.IO.Path]::GetFullPath((Join-Path $ManifestRoot "$SourceCommit.json"))
$python = Join-Path $release ".venv\Scripts\python.exe"
$supervisor = Join-Path $release "scripts\season_ladder_supervisor.py"
$stateRoot = Join-Path $RuntimeRoot "state"
$logRoot = Join-Path $RuntimeRoot "logs"
$cacheRoot = Join-Path $RuntimeRoot "cache"
$tempRoot = Join-Path $RuntimeRoot "temp"
$secretRoot = "C:\ProgramData\Devstream\secrets"
$secretEnv = Join-Path $secretRoot "fouler.env"
$accountSeason = Join-Path $AuthorityRoot "account-season.json"
$controlPath = Join-Path $AuthorityRoot "runtime-control.json"
$authorityPath = Join-Path $AuthorityRoot "$SeasonId.json"
$timestamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$backup = Join-Path $BackupRoot "$timestamp\fouler-season-cutover"
$legacyTasks = @("Fouler-LadderSupervisor", "HERMES-FoulerBattleSupervisor")
$runtimePrincipal = "$env:COMPUTERNAME\$RuntimeAccount"
$runtimeUser = Get-LocalUser -Name $RuntimeAccount -ErrorAction Stop
$runtimeSid = "*$($runtimeUser.SID.Value)"
$systemSid = "*S-1-5-18"
$administratorsSid = "*S-1-5-32-544"

function Assert-RegularFile {
    param([Parameter(Mandatory = $true)][string]$Path, [Parameter(Mandatory = $true)][string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "$Label is missing: $Path" }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a reparse point: $Path"
    }
}

function Assert-NoReparsePathChain {
    param([Parameter(Mandatory = $true)][string]$Path)
    $cursor = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    $root = [System.IO.Path]::GetPathRoot($cursor).TrimEnd("\")
    while ($cursor) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "path ancestry contains a reparse point: $cursor"
            }
        }
        if ([string]::Equals($cursor, $root, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = [System.IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName.TrimEnd("\")
    }
}

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = @(& git.exe -C $release @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)" }
    return ($output -join [Environment]::NewLine).Trim()
}

function Invoke-IcaclsChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = @(& "$env:SystemRoot\System32\icacls.exe" @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "icacls failed with exit code $LASTEXITCODE"
    }
}

function Protect-ReadOnlyFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-RegularFile -Path $Path -Label "protected file"
    Invoke-IcaclsChecked -Arguments @(
        $Path, "/inheritance:r",
        "/grant:r", "${systemSid}:F", "${administratorsSid}:F", "${runtimeSid}:R"
    )
}

function Protect-RuntimeDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Assert-NoReparsePathChain -Path $Path
    Invoke-IcaclsChecked -Arguments @(
        $Path, "/inheritance:r",
        "/grant:r",
        "${systemSid}:(OI)(CI)F",
        "${administratorsSid}:(OI)(CI)F",
        "${runtimeSid}:(OI)(CI)M",
        "/T", "/C"
    )
}

function Write-Utf8NoBomAtomic {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )
    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temporary = Join-Path $parent (".$([System.IO.Path]::GetFileName($Path)).$([guid]::NewGuid().ToString('N')).tmp")
    try {
        [System.IO.File]::WriteAllText($temporary, $Text, (New-Object System.Text.UTF8Encoding($false)))
        Move-Item -LiteralPath $temporary -Destination $Path -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Get-TaskSnapshot {
    param([Parameter(Mandatory = $true)][string]$Name)
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        return [ordered]@{ name = $Name; present = $false; enabled = $false; running = $false }
    }
    $xmlPath = Join-Path $backup ("scheduled-task-" + ($Name -replace '[^A-Za-z0-9_.-]', '_') + ".xml")
    Export-ScheduledTask -TaskName $Name | Set-Content -LiteralPath $xmlPath -Encoding UTF8
    return [ordered]@{
        name = $Name
        present = $true
        enabled = $task.State -ne "Disabled"
        running = $task.State -eq "Running"
        xmlPath = $xmlPath
    }
}

function Restore-TaskSnapshot {
    param([Parameter(Mandatory = $true)][System.Collections.IDictionary]$Snapshot)
    $name = [string]$Snapshot.name
    if (-not [bool]$Snapshot.present) {
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
        return
    }
    Register-ScheduledTask -TaskName $name -Xml (Get-Content -LiteralPath $Snapshot.xmlPath -Raw) -Force | Out-Null
    if (-not [bool]$Snapshot.enabled) {
        Disable-ScheduledTask -TaskName $name | Out-Null
    }
    elseif ([bool]$Snapshot.running) {
        Start-ScheduledTask -TaskName $name
    }
}

function Get-MutableFoulerProcesses {
    $root = "D:\Projects\fouler-play"
    return @(Get-CimInstance Win32_Process | Where-Object {
        $command = [string]$_.CommandLine
        $executable = [string]$_.ExecutablePath
        ($command -match '(?i)(ladder_supervisor\.py|ladder_run\.py|\\run\.py)') -and
        ($command.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase) -or
         $executable.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase) -or
         $command -match '(?i)ladder_(supervisor|run)\.py')
    })
}

function Quiesce-MutableFouler {
    foreach ($name in $legacyTasks) {
        if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
            Disable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
        }
    }
    # Stop only the mutable supervisors first.  Their battle child remains
    # alive long enough to consume the normal run.py drain request.
    $supervisors = @(Get-MutableFoulerProcesses | Where-Object {
        [string]$_.CommandLine -match '(?i)ladder_supervisor\.py'
    })
    foreach ($process in $supervisors) {
        Stop-Process -Id ([int]$process.ProcessId) -ErrorAction SilentlyContinue
    }
    $drainPath = "D:\Projects\fouler-play\pids\drain.request"
    New-Item -ItemType Directory -Path (Split-Path -Parent $drainPath) -Force | Out-Null
    Write-Utf8NoBomAtomic -Path $drainPath -Text "immutable finite-season cutover`n"
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes(20)
    do {
        $children = @(Get-MutableFoulerProcesses | Where-Object {
            [string]$_.CommandLine -match '(?i)(ladder_run\.py|\\run\.py)'
        })
        if ($children.Count -eq 0) { break }
        Start-Sleep -Seconds 5
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    if ($children.Count -gt 0) {
        throw "mutable Fouler battle child did not drain within 20 minutes; it was left intact"
    }
}

Assert-RegularFile -Path $python -Label "immutable release Python"
Assert-RegularFile -Path $supervisor -Label "finite season supervisor"
Assert-RegularFile -Path $manifest -Label "immutable release manifest"
Assert-RegularFile -Path $SourceSecretEnv -Label "source secret environment"
Assert-RegularFile -Path $SourceAccountSeason -Label "source account-season authority"
foreach ($path in @($release, $manifest, $AuthorityRoot, $RuntimeRoot, $EventQueueRoot, $backup)) {
    Assert-NoReparsePathChain -Path $path
}
$head = (Invoke-Git -Arguments @("rev-parse", "HEAD")).ToLowerInvariant()
if ($head -ne $SourceCommit) { throw "immutable release HEAD does not match SourceCommit" }
$tracked = Invoke-Git -Arguments @("status", "--porcelain=v1", "--untracked-files=no")
if ($tracked) { throw "immutable release contains tracked modifications" }
$manifestPayload = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
if ($manifestPayload.schemaVersion -ne "fouler-bootstrap-manifest/v1" -or
    $manifestPayload.projectId -ne "fouler-play" -or
    ([string]$manifestPayload.sourceCommit).ToLowerInvariant() -ne $SourceCommit) {
    throw "immutable release manifest identity does not match SourceCommit"
}
$accountPayload = Get-Content -LiteralPath $SourceAccountSeason -Raw -Encoding UTF8 | ConvertFrom-Json
if ($accountPayload.schemaVersion -ne "fouler-play-account-season/v1" -or
    -not [string]::Equals([string]$accountPayload.account, $Account, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "protected account-season authority does not match requested account"
}
$secretAccountLine = @(Get-Content -LiteralPath $SourceSecretEnv -Encoding UTF8 | Where-Object {
    $_ -match '^\s*PS_USERNAME\s*='
}) | Select-Object -Last 1
if (-not $secretAccountLine) { throw "source secret environment has no PS_USERNAME identity" }
$secretAccount = (($secretAccountLine -split '=', 2)[1]).Trim().Trim('"').Trim("'")
if (-not [string]::Equals($secretAccount, $Account, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "source secret environment account does not match requested account"
}

$plan = [ordered]@{
    schemaVersion = "fouler-season-supervisor-install-plan/v1"
    ok = $true
    apply = [bool]$Apply
    start = [bool]$Start
    taskName = $TaskName
    sourceCommit = $SourceCommit
    release = $release
    manifest = $manifest
    seasonId = $SeasonId
    maxRounds = $MaxRounds
    maxGames = 30 * $MaxRounds
    seasonHours = $SeasonHours
    account = $Account
    runtimeRoot = $RuntimeRoot
    publicOutputChanged = $false
    startStreaming = $false
}
if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 8
    exit 0
}

New-Item -ItemType Directory -Path $backup -Force | Out-Null
$taskSnapshots = @{}
foreach ($name in @($TaskName) + $legacyTasks) {
    $taskSnapshots[$name] = Get-TaskSnapshot -Name $name
}
foreach ($path in @($secretEnv, $accountSeason, $controlPath, $authorityPath)) {
    if (Test-Path -LiteralPath $path -PathType Leaf) {
        Copy-Item -LiteralPath $path -Destination (Join-Path $backup ([System.IO.Path]::GetFileName($path))) -Force
    }
}

$cutoverStarted = $false
try {
    New-Item -ItemType Directory -Path $stateRoot -Force | Out-Null
    if (-not (Test-Path -LiteralPath (Join-Path $stateRoot "battle_stats.json") -PathType Leaf) -and
        (Test-Path -LiteralPath $SourceRuntimeState -PathType Container)) {
        foreach ($name in @("battle_stats.json", "replay_analysis", "learning")) {
            $source = Join-Path $SourceRuntimeState $name
            $destination = Join-Path $stateRoot $name
            if (Test-Path -LiteralPath $source) {
                Copy-Item -LiteralPath $source -Destination $destination -Recurse -Force
            }
        }
    }
    foreach ($path in @($stateRoot, $logRoot, $cacheRoot, $tempRoot, $EventQueueRoot)) {
        Protect-RuntimeDirectory -Path $path
    }
    New-Item -ItemType Directory -Path $secretRoot, $AuthorityRoot -Force | Out-Null
    Copy-Item -LiteralPath $SourceSecretEnv -Destination $secretEnv -Force
    Copy-Item -LiteralPath $SourceAccountSeason -Destination $accountSeason -Force

    $existingEpoch = -1
    if (Test-Path -LiteralPath $controlPath -PathType Leaf) {
        try {
            $existingControl = Get-Content -LiteralPath $controlPath -Raw -Encoding UTF8 | ConvertFrom-Json
            $existingEpoch = [int]$existingControl.pauseEpoch
        }
        catch {
            throw "existing runtime control cannot be parsed; refusing to replace it"
        }
    }
    $pauseEpoch = $existingEpoch + 1
    $control = [ordered]@{
        schemaVersion = "devstream-runtime-control/v1"
        projectId = "fouler-play"
        state = "RUNNING"
        pauseEpoch = $pauseEpoch
        updatedAt = [DateTimeOffset]::UtcNow.ToString("o")
        reason = "owner-approved finite season $SeasonId"
    }
    Write-Utf8NoBomAtomic -Path $controlPath -Text (($control | ConvertTo-Json -Depth 8) + [Environment]::NewLine)

    $startsAt = [DateTimeOffset]::UtcNow.AddMinutes(-1)
    $expiresAt = $startsAt.AddHours($SeasonHours)
    $authority = [ordered]@{
        schemaVersion = "fouler-play-season-authority/v1"
        projectId = "fouler-play"
        active = $true
        seasonId = $SeasonId
        generation = 1
        pauseEpoch = $pauseEpoch
        sourceCommit = $SourceCommit
        releaseRoot = $release
        releaseManifestPath = $manifest
        releaseManifestSha256 = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
        machine = $env:COMPUTERNAME
        account = $Account
        proofWindow = [ordered]@{
            startsAt = $startsAt.ToString("o")
            expiresAt = $expiresAt.ToString("o")
        }
        limits = [ordered]@{
            roundSize = 30
            maxRounds = $MaxRounds
            maxGames = 30 * $MaxRounds
        }
        battleScope = [ordered]@{
            botMode = "search_ladder"
            websocketUri = "wss://sim3.psim.us/showdown/websocket"
            pokemonFormat = "gen9ou"
            teamName = $TeamName
            maxConcurrentBattles = 3
            searchParallelism = 2
            replayBehavior = "always"
        }
        stopLoss = [ordered]@{
            ratingWindow = 60
            maxRatingDrawdown = 75.0
        }
        runtime = [ordered]@{
            stateRoot = $stateRoot
            logRoot = $logRoot
            cacheRoot = $cacheRoot
            tempRoot = $tempRoot
            secretEnvFile = $secretEnv
            accountSeasonPath = $accountSeason
            eventQueueRoot = $EventQueueRoot
            controlPath = $controlPath
        }
        grants = [ordered]@{
            automaticRoundContinuation = $true
            sourceChanges = $false
            teamChanges = $false
            automaticImprovement = $false
            publicOutput = $false
        }
        createdAt = [DateTimeOffset]::UtcNow.ToString("o")
    }
    Write-Utf8NoBomAtomic -Path $authorityPath -Text (($authority | ConvertTo-Json -Depth 12) + [Environment]::NewLine)
    foreach ($path in @($secretEnv, $accountSeason, $controlPath, $authorityPath, $manifest)) {
        Protect-ReadOnlyFile -Path $path
    }
    Invoke-IcaclsChecked -Arguments @(
        $release, "/inheritance:r",
        "/grant:r",
        "${systemSid}:(OI)(CI)F",
        "${administratorsSid}:(OI)(CI)F",
        "${runtimeSid}:(OI)(CI)RX",
        "/T", "/C"
    )

    $authoritySha256 = (Get-FileHash -LiteralPath $authorityPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument "-I -B -u `"$supervisor`" --authority `"$authorityPath`" --authority-sha256 $authoritySha256" `
        -WorkingDirectory $release
    $trigger = New-ScheduledTaskTrigger -AtStartup
    $principal = New-ScheduledTaskPrincipal `
        -UserId $runtimePrincipal `
        -LogonType S4U `
        -RunLevel Limited
    $settings = New-ScheduledTaskSettingsSet `
        -StartWhenAvailable `
        -RestartCount 3 `
        -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -MultipleInstances IgnoreNew `
        -AllowStartIfOnBatteries `
        -DontStopIfGoingOnBatteries
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Principal $principal `
        -Settings $settings `
        -Description "Immutable, finite-budget Fouler ladder season supervisor" `
        -Force | Out-Null

    if ($Start) {
        $cutoverStarted = $true
        Quiesce-MutableFouler
        Start-ScheduledTask -TaskName $TaskName
        $statePath = Join-Path $stateRoot "seasons\$SeasonId.json"
        $deadline = [DateTimeOffset]::UtcNow.AddMinutes(3)
        $admitted = $false
        do {
            if (Test-Path -LiteralPath $statePath -PathType Leaf) {
                try {
                    $state = Get-Content -LiteralPath $statePath -Raw -Encoding UTF8 | ConvertFrom-Json
                    $admitted = $state.schemaVersion -eq "fouler-play-season-state/v1" -and
                        $state.seasonId -eq $SeasonId -and
                        $state.sourceCommit -eq $SourceCommit -and
                        $state.status -in @("ready", "running", "boundary-clear")
                }
                catch { $admitted = $false }
            }
            if (-not $admitted) { Start-Sleep -Seconds 3 }
        } while (-not $admitted -and [DateTimeOffset]::UtcNow -lt $deadline)
        if (-not $admitted) { throw "new finite season task did not reach an admitted runtime state" }
    }

    $result = [ordered]@{
        schemaVersion = "fouler-season-supervisor-install-result/v1"
        ok = $true
        taskName = $TaskName
        taskState = (Get-ScheduledTask -TaskName $TaskName).State.ToString()
        taskUser = $runtimePrincipal
        sourceCommit = $SourceCommit
        release = $release
        manifestSha256 = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
        seasonId = $SeasonId
        authorityPath = $authorityPath
        authoritySha256 = $authoritySha256
        pauseEpoch = $pauseEpoch
        maxRounds = $MaxRounds
        maxGames = 30 * $MaxRounds
        startsAt = $startsAt.ToString("o")
        expiresAt = $expiresAt.ToString("o")
        runtimeRoot = $RuntimeRoot
        backup = $backup
        legacyTasks = @($legacyTasks | ForEach-Object {
            $task = Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue
            [ordered]@{ name = $_; state = if ($task) { $task.State.ToString() } else { "missing" } }
        })
        publicOutputChanged = $false
        startStreaming = $false
    }
    $result | ConvertTo-Json -Depth 10
}
catch {
    try {
        Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
        foreach ($name in @($TaskName) + $legacyTasks) {
            Restore-TaskSnapshot -Snapshot $taskSnapshots[$name]
        }
        if ($cutoverStarted) {
            Remove-Item -LiteralPath "D:\Projects\fouler-play\pids\drain.request" -Force -ErrorAction SilentlyContinue
        }
    }
    catch {
        [Console]::Error.WriteLine("rollback also failed; inspect backup $backup")
    }
    $failure = [ordered]@{
        schemaVersion = "fouler-season-supervisor-install-result/v1"
        ok = $false
        sourceCommit = $SourceCommit
        seasonId = $SeasonId
        backup = $backup
        error = $_.Exception.Message
        publicOutputChanged = $false
        startStreaming = $false
    }
    $failure | ConvertTo-Json -Depth 8
    exit 2
}
