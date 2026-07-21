param(
    [int]$RunCount = 0,
    [int]$MaxConcurrentBattles = 3,
    [int]$SearchParallelism = 2,
    [int]$MaxCycles = 0,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [string]$RuntimeLease = "",
    [string]$RuntimeStateRoot = "C:\ProgramData\HERMES\state\fouler",
    [string]$RuntimeLogRoot = "C:\ProgramData\HERMES\logs\fouler",
    [string]$RuntimeCacheRoot = "C:\ProgramData\HERMES\cache\fouler",
    [string]$AccountSeasonPath = "C:\ProgramData\HERMES\authority\fouler\account-season.json",
    [string]$SecretEnvFile = "C:\ProgramData\HERMES\secrets\fouler.env",
    [switch]$AutoImprove,
    [switch]$ClearStopFile,
    [switch]$ClearDrainRequest,
    [ValidateSet("0", "1")]
    [string]$LoopBreak = "0",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"
$CanonicalAccountSeasonPath = "C:\ProgramData\HERMES\authority\fouler\account-season.json"
$CanonicalDekuEventQueueRoot = "D:\DekuEvents"
if ($MaxConcurrentBattles -ne 3) {
    Write-Error "owner-locked live pilot MaxConcurrentBattles must equal 3"
    exit 2
}
if ($SearchParallelism -ne 2) {
    Write-Error "owner-locked live pilot SearchParallelism must equal 2"
    exit 2
}
if (-not [string]::Equals([System.IO.Path]::GetFullPath($AccountSeasonPath), $CanonicalAccountSeasonPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    Write-Error "AccountSeasonPath must equal the canonical protected Fouler account-season authority"
    exit 2
}
if (-not $Foreground) {
    Write-Error "Fouler battle supervisor may launch only in the canonical scheduled-task foreground mode"
    exit 2
}
$AccountSeasonPath = $CanonicalAccountSeasonPath
$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path.TrimEnd("\")
if ($ProjectDir -notmatch '^D:\\Releases\\fouler-play\\[0-9a-f]{40}$') {
    Write-Error "ProjectDir must be an immutable D:\Releases\fouler-play\<commit> release"
    exit 2
}
$pathCursor = $ProjectDir
while (-not [string]::IsNullOrWhiteSpace($pathCursor)) {
    if (Test-Path -LiteralPath $pathCursor) {
        $pathItem = Get-Item -LiteralPath $pathCursor -Force -ErrorAction Stop
        if (($pathItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "ProjectDir ancestry contains a reparse point: $pathCursor" }
    }
    $pathParent = [System.IO.Directory]::GetParent($pathCursor)
    if ($null -eq $pathParent) { break }
    $pathCursor = $pathParent.FullName.TrimEnd("\")
}
function Assert-NoRuntimePathOverlap {
    param([string]$Path, [string]$Label)
    $candidate = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    if ([string]::Equals($candidate, $ProjectDir, [System.StringComparison]::OrdinalIgnoreCase) -or $candidate.StartsWith($ProjectDir + "\", [System.StringComparison]::OrdinalIgnoreCase) -or $ProjectDir.StartsWith($candidate + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must not equal, contain, or be contained by ProjectDir"
    }
}
foreach ($runtimePath in @($RuntimeStateRoot, $RuntimeLogRoot, $RuntimeCacheRoot, (Join-Path $RuntimeStateRoot "tmp"), $SecretEnvFile, $AccountSeasonPath, $CanonicalDekuEventQueueRoot)) {
    Assert-NoRuntimePathOverlap -Path $runtimePath -Label "supervisor runtime path"
}
Set-Location -LiteralPath $ProjectDir
$env:PYTHONDONTWRITEBYTECODE = "1"
$env:GIT_OPTIONAL_LOCKS = "0"
$env:FOULER_ENV_FILE = [System.IO.Path]::GetFullPath($SecretEnvFile)
$env:FOULER_ACCOUNT_SEASON_PATH = $AccountSeasonPath
$env:SEARCH_PARALLELISM = "2"
if (-not (Test-Path -LiteralPath $env:FOULER_ENV_FILE -PathType Leaf)) {
    Write-Error "protected Fouler secret environment file is missing"
    exit 2
}
if (-not (Test-Path -LiteralPath $AccountSeasonPath -PathType Leaf)) {
    Write-Error "canonical protected Fouler account-season authority is missing"
    exit 2
}
$accountSeasonAttributes = [System.IO.File]::GetAttributes($AccountSeasonPath)
if (($accountSeasonAttributes -band [System.IO.FileAttributes]::ReadOnly) -eq 0) {
    Write-Error "canonical protected Fouler account-season authority is not read-only"
    exit 2
}

$Py = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) {
    Write-Error "exact release venv Python is missing"
    exit 2
}
New-Item -ItemType Directory -Force -Path (Join-Path $RuntimeStateRoot "pids") | Out-Null
New-Item -ItemType Directory -Force -Path $RuntimeLogRoot | Out-Null
New-Item -ItemType Directory -Force -Path $RuntimeCacheRoot | Out-Null
$RuntimeTempRoot = Join-Path $RuntimeStateRoot "tmp"
New-Item -ItemType Directory -Force -Path $RuntimeTempRoot | Out-Null
$env:TEMP = $RuntimeTempRoot
$env:TMP = $RuntimeTempRoot

$LaunchLockPath = Join-Path $RuntimeStateRoot "pids\battle-supervisor-launch.lock"
$LaunchLockStream = $null
try {
    $LaunchLockStream = [System.IO.File]::Open(
        $LaunchLockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    $lockBytes = [System.Text.Encoding]::ASCII.GetBytes("pid=$PID started=$(Get-Date -Format o)")
    $LaunchLockStream.SetLength(0)
    $LaunchLockStream.Write($lockBytes, 0, $lockBytes.Length)
    $LaunchLockStream.Flush()
} catch {
    Write-Error "Another battle supervisor launcher owns $LaunchLockPath; refusing duplicate launch."
    exit 3
}

if ($RunCount -le 0 -or $MaxCycles -le 0) {
    Write-Error "Fouler battle supervisor requires explicit positive -RunCount and -MaxCycles bounds."
    exit 2
}

function Close-LaunchLock {
    if ($null -ne $LaunchLockStream) {
        try { $LaunchLockStream.Close() } catch {}
        try { $LaunchLockStream.Dispose() } catch {}
    }
}

function Split-WindowsCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return @() }
    $tokens = @()
    foreach ($match in [regex]::Matches($CommandLine, '(?:"(?<quoted>(?:[^"\\]|\\.)*)"|(?<bare>\S+))')) {
        $tokens += if ($match.Groups["quoted"].Success) { $match.Groups["quoted"].Value } else { $match.Groups["bare"].Value }
    }
    return $tokens
}

function Resolve-ProjectCommandPath {
    param([string]$Token)
    if ([string]::IsNullOrWhiteSpace($Token)) { return "" }
    try {
        $candidate = $Token.Trim().Trim('"')
        if (-not [System.IO.Path]::IsPathRooted($candidate)) {
            $candidate = Join-Path $ProjectDir $candidate
        }
        return [System.IO.Path]::GetFullPath($candidate)
    } catch {
        return ""
    }
}

function Get-CommandArgumentValues {
    param([string[]]$Tokens, [string]$Name)
    $values = @()
    for ($index = 0; $index -lt $Tokens.Count; $index++) {
        if ([string]::Equals($Tokens[$index], $Name, [System.StringComparison]::OrdinalIgnoreCase)) {
            if ($index + 1 -lt $Tokens.Count) { $values += $Tokens[$index + 1] }
            continue
        }
        $prefix = "$Name="
        if ($Tokens[$index].StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            $values += $Tokens[$index].Substring($prefix.Length)
        }
    }
    return $values
}

function Get-CommandArgument {
    param([string[]]$Tokens, [string]$Name)
    $values = @(Get-CommandArgumentValues -Tokens $Tokens -Name $Name)
    if ($values.Count -ne 1) { return "" }
    return [string]$values[0]
}

function Test-PositiveCommandInteger {
    param([string[]]$Tokens, [string]$Name)
    $value = Get-CommandArgument -Tokens $Tokens -Name $Name
    $parsed = 0
    return [int]::TryParse($value, [ref]$parsed) -and $parsed -gt 0
}

function Test-PinnedPythonProcess {
    param($Process)
    try {
        return (
            [string]::Equals([string]$Process.Name, "python.exe", [System.StringComparison]::OrdinalIgnoreCase) -and
            [string]::Equals([System.IO.Path]::GetFullPath([string]$Process.ExecutablePath), $Py, [System.StringComparison]::OrdinalIgnoreCase)
        )
    }
    catch { return $false }
}

function Test-CompleteLadderCommandIdentity {
    param($Process, [string]$Account)
    if (-not (Test-PinnedPythonProcess -Process $Process)) { return $false }
    $tokens = @(Split-WindowsCommandLine -CommandLine ([string]$Process.CommandLine))
    $runIndex = -1
    for ($index = 0; $index -lt $tokens.Count; $index++) {
        if ([string]::Equals((Resolve-ProjectCommandPath -Token $tokens[$index]), (Join-Path $ProjectDir "run.py"), [System.StringComparison]::OrdinalIgnoreCase)) {
            $runIndex = $index
            break
        }
    }
    if ($runIndex -ne 1 -or -not [string]::Equals((Resolve-ProjectCommandPath -Token $tokens[0]), $Py, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not [string]::Equals((Get-CommandArgument -Tokens $tokens -Name "--bot-mode"), "search_ladder", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not [string]::Equals((Get-CommandArgument -Tokens $tokens -Name "--pokemon-format"), "gen9ou", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not (Test-PositiveCommandInteger -Tokens $tokens -Name "--run-count")) { return $false }
    if ((Get-CommandArgument -Tokens $tokens -Name "--max-concurrent-battles") -ne "3") { return $false }
    if ((Get-CommandArgument -Tokens $tokens -Name "--search-parallelism") -ne "2") { return $false }
    $processAccount = Get-CommandArgument -Tokens $tokens -Name "--ps-username"
    return -not [string]::IsNullOrWhiteSpace($Account) -and [string]::Equals($processAccount, $Account, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-ExistingLadderRunnerPids {
    param([string]$Account)
    $runners = @()
    foreach ($p in @(Get-CimInstance Win32_Process -Filter "name like 'python%'" -ErrorAction SilentlyContinue)) {
        if (Test-CompleteLadderCommandIdentity -Process $p -Account $Account) {
            $runners += $p.ProcessId
        }
    }
    return $runners
}

function Test-AndApplyRuntimeLease {
    param([string]$RuntimeLease)
    $leasePath = if ([string]::IsNullOrWhiteSpace($RuntimeLease)) {
        "C:\ProgramData\HERMES\authority\fouler\runtime-lease.json"
    } elseif ([System.IO.Path]::IsPathRooted($RuntimeLease)) {
        [System.IO.Path]::GetFullPath($RuntimeLease)
    } else {
        [System.IO.Path]::GetFullPath((Join-Path $ProjectDir $RuntimeLease))
    }
    Assert-NoRuntimePathOverlap -Path $leasePath -Label "runtime lease path"
    $sourceCommit = Split-Path -Leaf $ProjectDir
    if ($sourceCommit -notmatch '^[0-9a-f]{40}$') {
        Write-Error "Current Fouler source commit is unavailable before supervisor launch."
        return $null
    }
    $validatorArgs = @(
        (Join-Path $ProjectDir "scripts\devstream_runtime_lease.py"),
        "--purpose", "devstream-supervise",
        "--runtime-lease", $leasePath,
        "--run-count", "$RunCount",
        "--max-cycles", "$MaxCycles",
        "--max-concurrent-battles", "$MaxConcurrentBattles",
        "--source-commit", $sourceCommit.ToLowerInvariant(),
        "--require-run-count",
        "--require-max-cycles",
        "--require-max-concurrent-battles",
        "--require-replay-behavior",
        "--replay-behavior", "always",
        "--require-deployment-receipt",
        "--verify-deployment-checkout"
    )
    $raw = (& $Py -I -B @validatorArgs 2>&1 | Out-String).Trim()
    $code = $LASTEXITCODE
    try {
        $validation = $raw | ConvertFrom-Json
    } catch {
        Write-Error "Runtime lease validator did not return valid JSON (exit=$code)."
        return $null
    }
    if ($code -ne 0 -or -not $validation.ok) {
        $blockers = @($validation.blockers | ForEach-Object { "$_" })
        Write-Error ("Runtime lease validation failed before supervisor mutation: " + ($blockers -join "; "))
        return $null
    }
    if (-not $validation.environment) {
        Write-Error "Runtime lease validator omitted the approved process environment."
        return $null
    }
    foreach ($property in $validation.environment.PSObject.Properties) {
        [Environment]::SetEnvironmentVariable($property.Name, "$($property.Value)", "Process")
    }
    return $validation
}

$leaseValidation = Test-AndApplyRuntimeLease -RuntimeLease $RuntimeLease
if (-not $leaseValidation) {
    Close-LaunchLock
    exit 2
}
$resolvedRuntimeLease = "$($leaseValidation.path)".Trim()
if (-not [System.IO.Path]::IsPathRooted($resolvedRuntimeLease) -or -not (Test-Path -LiteralPath $resolvedRuntimeLease -PathType Leaf)) {
    Write-Error "Runtime lease validator did not return an existing absolute lease path."
    Close-LaunchLock
    exit 2
}
$leaseAccount = "$($leaseValidation.lease.account)".Trim()

# --- SINGLETON GUARD ------------------------------------------------------
# A scheduled or duplicate invocation must never preempt a valid supervisor.
# Deliberate replacement is owned by the installer/stop path after it validates
# the candidate lease; this wrapper only starts when no same-repo owner exists.
$selfPid = $PID
$existingSupervisors = @()
$alternateSupervisors = @()
foreach ($p in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
    $commandLine = [string]$p.CommandLine
    if ($commandLine -notmatch '(?i)devstream_session\.py(?:"|\s).*?\bsupervise\b') { continue }
    $tokens = @(Split-WindowsCommandLine -CommandLine ([string]$p.CommandLine))
    $scriptIndex = -1
    for ($index = 0; $index -lt $tokens.Count; $index++) {
        if ([string]::Equals((Resolve-ProjectCommandPath -Token $tokens[$index]), (Join-Path $ProjectDir "scripts\devstream_session.py"), [System.StringComparison]::OrdinalIgnoreCase)) {
            $scriptIndex = $index
            break
        }
    }
    $exactIdentity = (
        (Test-PinnedPythonProcess -Process $p) -and
        $scriptIndex -eq 3 -and
        [string]::Equals((Resolve-ProjectCommandPath -Token $tokens[0]), $Py, [System.StringComparison]::OrdinalIgnoreCase) -and
        $tokens[1] -ceq "-I" -and
        $tokens[2] -ceq "-B" -and
        $scriptIndex + 1 -lt $tokens.Count -and
        [string]::Equals($tokens[$scriptIndex + 1], "supervise", [System.StringComparison]::OrdinalIgnoreCase) -and
        (Test-PositiveCommandInteger -Tokens $tokens -Name "--run-count") -and
        (Get-CommandArgument -Tokens $tokens -Name "--max-concurrent-battles") -eq "3" -and
        (Test-PositiveCommandInteger -Tokens $tokens -Name "--max-cycles")
    )
    if (-not $exactIdentity) {
        $alternateSupervisors += $p.ProcessId
        continue
    }
    $existingLease = Resolve-ProjectCommandPath -Token (Get-CommandArgument -Tokens $tokens -Name "--runtime-lease")
    if (-not [string]::Equals($existingLease, $resolvedRuntimeLease, [System.StringComparison]::OrdinalIgnoreCase)) {
        $alternateSupervisors += $p.ProcessId
        continue
    }
    if ($p.ProcessId -ne $selfPid) {
        $existingSupervisors += $p.ProcessId
    }
}
if ($alternateSupervisors.Count -gt 0) {
    Write-Error "Mutable or alternate Fouler supervisor process blocks launch: $($alternateSupervisors -join ', ')"
    Close-LaunchLock
    exit 2
}
if ($existingSupervisors.Count -gt 0) {
    Write-Output "[singleton-guard] same-repo supervisor already owns runtime PID(s) $($existingSupervisors -join ', '); refusing incidental replacement."
    Close-LaunchLock
    exit 0
}
# --- END SINGLETON GUARD --------------------------------------------------

$existingRunners = @(Get-ExistingLadderRunnerPids -Account $leaseAccount)
$alternateRunners = @(
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $candidateCommand = [string]$_.CommandLine
        $candidateCommand -match '(?i)(?:^|[\\/])run\.py(?:"|\s)' -and
        $candidateCommand -match '(?i)--bot-mode(?:=|\s+)search_ladder\b' -and
        -not (Test-CompleteLadderCommandIdentity -Process $_ -Account $leaseAccount)
    } | Select-Object -ExpandProperty ProcessId
)
if ($alternateRunners.Count -gt 0) {
    Write-Error "Mutable or alternate Fouler ladder process blocks launch: $($alternateRunners -join ', ')"
    Close-LaunchLock
    exit 2
}
if ($existingRunners.Count -gt 0) {
    Write-Output "[singleton-guard] existing ladder runner(s) for account '$leaseAccount': $($existingRunners -join ', '); launching supervisor in monitor/adopt mode."
    Write-Output "[singleton-guard] devstream_session.py supervise will observe the live runner and must not start another batch while it is in flight."
}

$stopFile = Join-Path $RuntimeStateRoot "pids\supervisor.stop"
$drainFile = Join-Path $RuntimeStateRoot "pids\drain.request"
$recoveryProofWindowFile = Join-Path $RuntimeStateRoot "pids\recovery-proof-window.json"
if ($ClearStopFile -and $ClearDrainRequest) {
    $proofWindowErrors = @()
    if ($RunCount -lt 1 -or $RunCount -gt 5) {
        $proofWindowErrors += "RunCount must be 1-5 for a stop-loss recovery proof window"
    }
    if ($MaxCycles -ne 1) {
        $proofWindowErrors += "MaxCycles must be 1 for a stop-loss recovery proof window"
    }
    if ($MaxConcurrentBattles -ne 3) {
        $proofWindowErrors += "MaxConcurrentBattles must be 3 for the owner-locked live pilot"
    }
    if ($LoopBreak -ne "0") {
        $proofWindowErrors += "LoopBreak must be 0 for a stop-loss recovery proof window"
    }
    if ($proofWindowErrors.Count -gt 0) {
        Write-Error ("Refusing to open recovery proof window: " + ($proofWindowErrors -join "; "))
        Close-LaunchLock
        exit 2
    }
    $launchedAt = [DateTime]::UtcNow
    $marker = [ordered]@{
        schemaVersion = "fouler-play-recovery-proof-window/v1"
        approved = $true
        purpose = "stop-loss-recovery-proof-window"
        launchedAtUtc = $launchedAt.ToString("o")
        expiresAtUtc = $launchedAt.AddMinutes(30).ToString("o")
        runCount = $RunCount
        maxCycles = $MaxCycles
        maxConcurrentBattles = $MaxConcurrentBattles
        loopBreak = $LoopBreak
        noStreamStart = $true
    }
    $marker | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $recoveryProofWindowFile -Encoding ASCII
    Write-Output "[start-gate] wrote finite recovery proof window marker"
}
# A refusal here exits 0 and the scheduled task drops straight back to Ready
# with LastTaskResult=0, so the runtime looks healthy while it is in fact dark.
# Leave a durable, timestamped trace of every refusal so a start that did not
# start is visible without having to reproduce it interactively.
function Write-StartGateRefusal {
    param([string]$Reason)
    Write-Output "[start-gate] $Reason; refusing to launch a battle supervisor"
    $line = "{0}  refused: {1}" -f (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ"), $Reason
    try {
        Add-Content -LiteralPath (Join-Path $RuntimeLogRoot "battle-supervisor-start-gate.log") -Value $line -ErrorAction Stop
    } catch {
        Write-Output "[start-gate] could not record the refusal: $($_.Exception.Message)"
    }
}

if (Test-Path -LiteralPath $stopFile -PathType Leaf) {
    if ($ClearStopFile) {
        Remove-Item -LiteralPath $stopFile -Force
        Write-Output "[start-gate] cleared supervisor.stop because -ClearStopFile was explicitly supplied"
    } else {
        Write-StartGateRefusal -Reason "supervisor.stop is present"
        Close-LaunchLock
        exit 0
    }
}

if (Test-Path -LiteralPath $drainFile -PathType Leaf) {
    if ($ClearDrainRequest) {
        Remove-Item -LiteralPath $drainFile -Force
        Write-Output "[start-gate] cleared drain.request because -ClearDrainRequest was explicitly supplied"
    } else {
        Write-StartGateRefusal -Reason "drain.request is present"
        Close-LaunchLock
        exit 0
    }
}

$env:LOSS_TRIGGERED_DRAIN = "0"
$env:BATTLE_STATS_MAX_ENTRIES = "5000"
$env:BOT_LOG_TO_FILE = "1"
$env:FOULER_RUNTIME_STATE_ROOT = $RuntimeStateRoot
$env:FOULER_RUNTIME_LOG_ROOT = $RuntimeLogRoot
$env:FOULER_RUNTIME_CACHE_ROOT = $RuntimeCacheRoot
$env:FOULER_RUNTIME_TEMP_ROOT = $RuntimeTempRoot
$env:DEKU_EVENT_QUEUE_ROOT = $CanonicalDekuEventQueueRoot
$env:FOULER_LOG_DIR = $RuntimeLogRoot
$env:DECISION_TRACE_DIR = Join-Path $RuntimeLogRoot "decision_traces"
$env:FOULER_PLAY_ENABLE_AUTO_IMPROVE = if ($AutoImprove) { "1" } else { "0" }
$env:FOULER_LOOP_BREAK = $LoopBreak

$supervisorArgs = @(
    (Join-Path $ProjectDir "scripts\devstream_session.py"),
    "supervise",
    "--run-count", "$RunCount",
    "--max-concurrent-battles", "$MaxConcurrentBattles",
    "--max-cycles", "$MaxCycles",
    "--queue-timeout-seconds", "$QueueTimeoutSeconds",
    "--sleep-seconds", "$SleepSeconds",
    "--runtime-lease", $resolvedRuntimeLease
)
if ($AutoImprove) {
    $supervisorArgs += "--enable-auto-improve"
} else {
    $supervisorArgs += "--skip-improve"
}

& $Py -I -B @supervisorArgs
$supervisorExitCode = $LASTEXITCODE
Close-LaunchLock
exit $supervisorExitCode
