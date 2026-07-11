param(
    [ValidateSet("status", "bootstrap", "start", "stop", "login-proof")]
    [string]$Command = "status",
    [int]$RunCount = 0,
    [int]$MaxConcurrentBattles = 3,
    [int]$MaxCycles = 0,
    [string]$RuntimeLease = "",
    [switch]$ObsOnly,
    [switch]$AutoImprove,
    [switch]$Execute,
    [switch]$NoWrite
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$TruthDir = Join-Path $RepoRoot "devstream\truth"
$LogDir = Join-Path $RepoRoot "logs"
$PidDir = Join-Path $RepoRoot ".pids"
$RemoteTruthPath = Join-Path $TruthDir "jigglypuff-runtime.json"

function Get-IsoNow {
    return [DateTimeOffset]::UtcNow.ToString("o")
}

function Get-ProducerEvidence {
    $hostName = $env:COMPUTERNAME
    if ([string]::IsNullOrWhiteSpace($hostName)) {
        try {
            $hostName = [System.Net.Dns]::GetHostName()
        } catch {
            $hostName = "unknown"
        }
    }
    $expected = "JIGGLYPUFF"
    return @{
        schemaVersion = "fouler-play-runtime-producer/v1"
        host = $hostName
        expectedHost = $expected
        expectedHostMatched = $hostName.Equals($expected, [StringComparison]::OrdinalIgnoreCase)
    }
}

function Write-JsonFile {
    param([string]$Path, [object]$Payload)
    $dir = Split-Path -Parent $Path
    if ($dir -and -not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }
    $Payload | ConvertTo-Json -Depth 8 | Set-Content -Path $Path -Encoding UTF8
}

function Read-JsonFile {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        return $null
    }
    try {
        return Get-Content -Path $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Resolve-RuntimeLeasePath {
    param([string]$RuntimeLease)
    if ([string]::IsNullOrWhiteSpace($RuntimeLease)) {
        return (Join-Path $RepoRoot "devstream\truth\runtime-lease.json")
    }
    if ([System.IO.Path]::IsPathRooted($RuntimeLease)) {
        return $RuntimeLease
    }
    return (Join-Path $RepoRoot $RuntimeLease)
}

function Get-RuntimeLeaseAccount {
    param([string]$RuntimeLease)
    $lease = Read-JsonFile -Path (Resolve-RuntimeLeasePath -RuntimeLease $RuntimeLease)
    if ($null -eq $lease) {
        return ""
    }
    $candidates = @(
        $lease.account,
        $lease.psUsername,
        $lease.showdownAccount,
        $lease.battleScope.account,
        $lease.battleScope.psUsername
    )
    foreach ($candidate in $candidates) {
        $value = "$candidate".Trim()
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value
        }
    }
    return ""
}

function ConvertTo-CmdSetAssignment {
    param([string]$Name, [string]$Value)
    if ([string]::IsNullOrWhiteSpace($Name) -or [string]::IsNullOrWhiteSpace($Value)) {
        return $null
    }
    if ($Value -notmatch '^[A-Za-z0-9_.-]+$') {
        return $null
    }
    return "set $Name=$Value"
}

function Invoke-Checked {
    param(
        [string]$FilePath,
        [string[]]$ArgumentList,
        [int]$TimeoutSeconds = 120
    )
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $FilePath
    $psi.Arguments = ($ArgumentList | ForEach-Object { ConvertTo-CommandLineArgument $_ }) -join " "
    $psi.WorkingDirectory = $RepoRoot
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $proc = [System.Diagnostics.Process]::Start($psi)
    $finished = $proc.WaitForExit($TimeoutSeconds * 1000)
    if (-not $finished) {
        try { $proc.Kill($true) } catch {}
        return @{
            ok = $false
            timedOut = $true
            returnCode = $null
            stdout = ""
            stderr = "timeout after $TimeoutSeconds seconds"
        }
    }
    return @{
        ok = ($proc.ExitCode -eq 0)
        timedOut = $false
        returnCode = $proc.ExitCode
        stdout = $proc.StandardOutput.ReadToEnd().Trim()
        stderr = $proc.StandardError.ReadToEnd().Trim()
    }
}

function ConvertTo-CommandLineArgument {
    param([string]$Value)
    if ($null -eq $Value) {
        return '""'
    }
    if ($Value -match '^[A-Za-z0-9_\-./:=\\]+$') {
        return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Get-PythonPath {
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $venvPython) {
        return $venvPython
    }
    return "python"
}

function Test-RuntimeLease {
    param(
        [int]$RunCount,
        [int]$MaxConcurrentBattles,
        [int]$MaxCycles,
        [string]$RuntimeLease
    )
    $python = Get-PythonPath
    $args = @(
        "scripts\devstream_runtime_lease.py",
        "--purpose", "jigglypuff-runtime-start",
        "--run-count", "$RunCount",
        "--max-concurrent-battles", "$MaxConcurrentBattles",
        "--max-cycles", "$MaxCycles",
        "--require-run-count",
        "--require-max-cycles",
        "--require-max-concurrent-battles",
        "--require-replay-behavior"
    )
    if (-not [string]::IsNullOrWhiteSpace($RuntimeLease)) {
        $args += @("--runtime-lease", $RuntimeLease)
    }
    $result = Invoke-Checked -FilePath $python -ArgumentList $args -TimeoutSeconds 20
    $payload = $null
    if ($result.stdout) {
        try { $payload = $result.stdout | ConvertFrom-Json } catch {}
    }
    return @{ ok = [bool]$result.ok; result = $result; payload = $payload }
}

function Get-GitInfo {
    if (-not (Test-Path (Join-Path $RepoRoot ".git"))) {
        return @{ present = $false }
    }
    $runtimeCodePaths = @(
        "run.py",
        "config.py",
        "process_lock.py",
        "constants.py",
        "constants_pkg",
        "data",
        "fp",
        "scripts/devstream_session.py",
        "start_one_touch.bat",
        "teams"
    )
    $head = Invoke-Checked -FilePath "git" -ArgumentList @("rev-parse", "--short", "HEAD") -TimeoutSeconds 10
    $branch = Invoke-Checked -FilePath "git" -ArgumentList @("branch", "--show-current") -TimeoutSeconds 10
    $commitTime = Invoke-Checked -FilePath "git" -ArgumentList @("show", "-s", "--format=%cI", "HEAD") -TimeoutSeconds 10
    $runtimeCodeHead = Invoke-Checked -FilePath "git" -ArgumentList (@("log", "-1", "--format=%h", "--") + $runtimeCodePaths) -TimeoutSeconds 10
    $runtimeCodeCommitTime = Invoke-Checked -FilePath "git" -ArgumentList (@("log", "-1", "--format=%cI", "--") + $runtimeCodePaths) -TimeoutSeconds 10
    $status = Invoke-Checked -FilePath "git" -ArgumentList @("status", "--short") -TimeoutSeconds 10
    return @{
        present = $true
        head = $head.stdout
        commitTime = $commitTime.stdout
        runtimeCodeHead = $runtimeCodeHead.stdout
        runtimeCodeCommitTime = $runtimeCodeCommitTime.stdout
        runtimeCodePaths = $runtimeCodePaths
        branch = $branch.stdout
        dirty = -not [string]::IsNullOrWhiteSpace($status.stdout)
        statusShort = $status.stdout
    }
}

function Redact-CommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return $CommandLine
    }
    $redacted = $CommandLine -replace '(--ps-password\s+)"[^"]*"', '$1"[redacted]"'
    $redacted = $redacted -replace '(--ps-password\s+)[^\s"]+', '$1[redacted]'
    $redacted = $redacted -replace '(PS_PASSWORD=)[^&\s"]+', '$1[redacted]'
    return $redacted
}

function Get-ProcessRole {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) {
        return "unknown"
    }
    if (
        $CommandLine -match "streaming[\\/]+serve_obs_page\.py" -or
        $CommandLine -match "streaming\.serve_obs_page" -or
        $CommandLine -match "streaming[\\/]+run_obs_server_service\.py"
    ) {
        return "obsServer"
    }
    if ($CommandLine -match "run\.py" -and $CommandLine -match "search_ladder") {
        return "battleSession"
    }
    if ($CommandLine -match "devstream_session\.py" -and $CommandLine -match "\bsupervise\b") {
        return "battleSupervisor"
    }
    if ($CommandLine -match "start_one_touch\.bat") {
        return "battleLauncher"
    }
    return "foulerProcess"
}

function Get-ProcessInfo {
    $escapedRepo = [regex]::Escape($RepoRoot)
    $items = Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        (
            (
                $_.CommandLine -match $escapedRepo -and
                (
                    $_.CommandLine -match "run\.py" -or
                    ($_.CommandLine -match "devstream_session\.py" -and $_.CommandLine -match "\bsupervise\b") -or
                    $_.CommandLine -match "start_one_touch\.bat" -or
                    $_.CommandLine -match "streaming[\\/]+serve_obs_page\.py" -or
                    $_.CommandLine -match "streaming\.serve_obs_page" -or
                    $_.CommandLine -match "streaming[\\/]+run_obs_server_service\.py"
                )
            ) -or
            (
                $_.CommandLine -match "run\.py" -and
                $_.CommandLine -match "search_ladder" -and
                $_.CommandLine -match "--ps-username"
            ) -or
            $_.CommandLine -match "streaming[\\/]+serve_obs_page\.py" -or
            $_.CommandLine -match "streaming\.serve_obs_page" -or
            $_.CommandLine -match "streaming[\\/]+run_obs_server_service\.py"
        )
    } | ForEach-Object {
        $redactedCommandLine = Redact-CommandLine -CommandLine $_.CommandLine
        @{
            pid = $_.ProcessId
            parentPid = $_.ParentProcessId
            name = $_.Name
            role = Get-ProcessRole -CommandLine $redactedCommandLine
            commandLine = $redactedCommandLine
            creationDate = $_.CreationDate
        }
    }
    return @($items)
}

function Get-LeafProcessesForRole {
    param(
        [array]$Processes,
        [string]$Role
    )
    $candidates = @($Processes | Where-Object {
        $_.role -eq $Role -and $_.name -match "^(py|python).*\.exe$"
    })
    $parentIds = @($candidates | ForEach-Object { [int]$_.parentPid })
    return @($candidates | Where-Object { $parentIds -notcontains [int]$_.pid })
}

function Get-LogicalProcessSummary {
    param([array]$Processes)
    $obsLeafs = @(Get-LeafProcessesForRole -Processes $Processes -Role "obsServer")
    $battleLeafs = @(Get-LeafProcessesForRole -Processes $Processes -Role "battleSession")
    $supervisorLeafs = @(Get-LeafProcessesForRole -Processes $Processes -Role "battleSupervisor")
    return @{
        obsServer = @{
            leafCount = @($obsLeafs).Count
            pids = @($obsLeafs | ForEach-Object { $_.pid })
        }
        battleSupervisor = @{
            leafCount = @($supervisorLeafs).Count
            pids = @($supervisorLeafs | ForEach-Object { $_.pid })
        }
        battleSession = @{
            leafCount = @($battleLeafs).Count
            pids = @($battleLeafs | ForEach-Object { $_.pid })
        }
        rawProcessCount = @($Processes).Count
    }
}

function Stop-FoulerProcesses {
    $stopped = @()
    foreach ($proc in Get-ProcessInfo) {
        try {
            Stop-Process -Id ([int]$proc.pid) -Force -ErrorAction Stop
            $stopped += @{ pid = $proc.pid; name = $proc.name; ok = $true }
        } catch {
            $stopped += @{ pid = $proc.pid; name = $proc.name; ok = $false; error = $_.Exception.Message }
        }
    }
    return $stopped
}

function Test-LocalPort {
    param([int]$Port)
    try {
        $client = New-Object Net.Sockets.TcpClient
        $iar = $client.BeginConnect("127.0.0.1", $Port, $null, $null)
        $success = $iar.AsyncWaitHandle.WaitOne(1000, $false)
        if ($success) {
            $client.EndConnect($iar)
        }
        $client.Close()
        return [bool]$success
    } catch {
        return $false
    }
}

function Start-DetachedCommand {
    param(
        [string]$CommandLine,
        [string]$WorkingDirectory
    )
    try {
        $result = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = $CommandLine
            CurrentDirectory = $WorkingDirectory
        }
        return @{
            ok = ([int]$result.ReturnValue -eq 0)
            returnValue = [int]$result.ReturnValue
            pid = [int]$result.ProcessId
            commandLine = $CommandLine
        }
    } catch {
        return @{
            ok = $false
            error = $_.Exception.Message
            commandLine = $CommandLine
        }
    }
}

function Rotate-LogFileIfLarge {
    param(
        [string]$Path,
        [int64]$MaxBytes = 10485760,
        [int]$Keep = 6
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    $item = Get-Item -LiteralPath $Path -ErrorAction SilentlyContinue
    if (-not $item -or $item.Length -lt $MaxBytes) { return }
    $archive = Join-Path (Split-Path -Parent $Path) "archive"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $name = [IO.Path]::GetFileName($Path)
    Move-Item -LiteralPath $Path -Destination (Join-Path $archive "$stamp-$name") -Force
    Get-ChildItem -LiteralPath $archive -Filter "*-$name" |
        Sort-Object LastWriteTime -Descending |
        Select-Object -Skip $Keep |
        Remove-Item -Force -ErrorAction SilentlyContinue
}

function Invoke-CurlEndpoint {
    param(
        [string]$Url,
        [int]$Timeout
    )
    $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
    if (-not $curl) {
        return $null
    }
    $previousErrorAction = $ErrorActionPreference
    $nativeError = $null
    try {
        $ErrorActionPreference = "Continue"
        $statusCodeText = & $curl.Source -sS -o NUL -w "%{http_code}" --max-time $Timeout $Url 2>$null
        $curlExitCode = $LASTEXITCODE
    } catch {
        $nativeError = $_.Exception.Message
        $curlExitCode = 1
        $statusCodeText = ""
    } finally {
        $ErrorActionPreference = $previousErrorAction
    }
    if ($curlExitCode -ne 0) {
        $reason = "curl.exe exited $curlExitCode"
        if ($nativeError) {
            $reason = "$reason`: $nativeError"
        }
        return @{ url = $Url; ok = $false; error = $reason; probe = "curl.exe" }
    }
    $statusCodeText = "$statusCodeText".Trim()
    if ($statusCodeText -notmatch "^\d{3}$") {
        return @{ url = $Url; ok = $false; error = "curl.exe returned invalid status code: $statusCodeText"; probe = "curl.exe" }
    }
    $statusCode = [int]$statusCodeText
    return @{ url = $Url; ok = ($statusCode -ge 200 -and $statusCode -lt 400); statusCode = $statusCode; probe = "curl.exe" }
}

function Get-Endpoint {
    param([string]$Path)
    $url = "http://127.0.0.1:8777$Path"
    $timeout = 3
    if ($Path -eq "/health") {
        $timeout = 10
    }
    try {
        $response = Invoke-WebRequest -UseBasicParsing -Uri $url -TimeoutSec $timeout
        return @{ url = $url; ok = $true; statusCode = [int]$response.StatusCode; probe = "Invoke-WebRequest" }
    } catch {
        $webError = $_.Exception.Message
        $curlResult = Invoke-CurlEndpoint -Url $url -Timeout $timeout
        if ($null -ne $curlResult) {
            $curlResult.invokeWebRequestError = $webError
            return $curlResult
        }
        return @{ url = $url; ok = $false; error = $webError; probe = "Invoke-WebRequest" }
    }
}

function Get-TruthStatus {
    $paths = @(
        "active_battles.json",
        "stream_status.json",
        "daily_stats.json",
        "battle_stats.json",
        "devstream\truth\showdown-login-proof.json"
    )
    $items = @()
    foreach ($rel in $paths) {
        $path = Join-Path $RepoRoot $rel
        $exists = Test-Path $path
        $summary = $null
        if ($exists -and $path.EndsWith(".json")) {
            $parsed = Read-JsonFile -Path $path
            if ($null -ne $parsed) {
                if ($rel -eq "active_battles.json") {
                    $summary = @{
                        battleCount = @($parsed.battles).Count
                        updated = $parsed.updated
                    }
                } elseif ($rel -eq "stream_status.json") {
                    $summary = @{
                        status = $parsed.status
                        runtimeBlocked = [bool]$parsed.runtime_blocked
                        blockerCode = $parsed.blocker_code
                        blockerSummary = $parsed.blocker_summary
                        updated = $parsed.updated
                    }
                } elseif ($rel -eq "daily_stats.json") {
                    $summary = @{
                        date = $parsed.date
                        wins = $parsed.wins
                        losses = $parsed.losses
                    }
                } elseif ($rel -eq "battle_stats.json") {
                    $battles = @($parsed.battles)
                    $lastBattle = $null
                    if ($battles.Count -gt 0) {
                        $last = $battles | Select-Object -Last 1
                        $lastBattle = @{
                            battleId = $last.battle_id
                            timestamp = $last.timestamp
                            result = $last.result
                            replayId = $last.replay_id
                            rating = $last.rating
                            teamFile = $last.team_file
                        }
                    }
                    $summary = @{
                        battleCount = $battles.Count
                        lastBattle = $lastBattle
                    }
                } elseif ($rel -eq "devstream\truth\showdown-login-proof.json") {
                    $summary = @{
                        ok = [bool]$parsed.ok
                        checkedAt = $parsed.checkedAt
                        loginOk = [bool]$parsed.loginOk
                    }
                }
            }
        }
        $mtime = $null
        if ($exists) {
            $mtime = (Get-Item $path).LastWriteTimeUtc.ToString("o")
        }
        $items += @{
            relativePath = $rel
            exists = $exists
            mtime = $mtime
            summary = $summary
        }
    }
    return $items
}

function Get-TaskInfo {
    try {
        return @(Get-ScheduledTask | Where-Object {
            $_.TaskName -match "Fouler|Foul|FoulerPlay"
        } | ForEach-Object {
            @{ taskName = $_.TaskName; taskPath = $_.TaskPath; state = $_.State.ToString() }
        })
    } catch {
        return @()
    }
}

function Start-ObsServer {
    if (Test-LocalPort -Port 8777) {
        return @{ ok = $true; alreadyRunning = $true; port = 8777 }
    }
    $python = Get-PythonPath
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $stdout = Join-Path $LogDir "jigglypuff-obs-server.log"
    $stderr = Join-Path $LogDir "jigglypuff-obs-server.err.log"
    $commandLine = 'cmd.exe /d /c ""{0}" "streaming\serve_obs_page.py" 1>>"{1}" 2>>"{2}""' -f $python, $stdout, $stderr
    $launch = Start-DetachedCommand -CommandLine $commandLine -WorkingDirectory $RepoRoot
    Start-Sleep -Seconds 3
    return @{
        ok = ((Test-LocalPort -Port 8777) -and [bool]$launch.ok)
        pid = $launch.pid
        launch = $launch
        port = 8777
        stdout = $stdout
        stderr = $stderr
    }
}

function Start-BattleSession {
    param([int]$RunCount, [int]$MaxConcurrentBattles, [int]$MaxCycles, [string]$RuntimeLease, [switch]$AutoImprove)
    if (-not (Test-Path (Join-Path $RepoRoot ".env"))) {
        return @{ ok = $false; error = ".env is missing; refusing to queue Showdown battles" }
    }
    if (-not (Test-Path $LogDir)) { New-Item -ItemType Directory -Path $LogDir -Force | Out-Null }
    $stdout = Join-Path $LogDir "jigglypuff-battle-supervisor.log"
    $stderr = Join-Path $LogDir "jigglypuff-battle-supervisor.err.log"
    Rotate-LogFileIfLarge -Path $stdout
    Rotate-LogFileIfLarge -Path $stderr
    $python = Get-PythonPath
    $supervisorArgs = @(
        $python,
        "scripts\devstream_session.py",
        "supervise",
        "--run-count", "$RunCount",
        "--max-concurrent-battles", "$MaxConcurrentBattles",
        "--max-cycles", "$MaxCycles",
        "--queue-timeout-seconds", "180",
        "--sleep-seconds", "15"
    )
    if (-not [string]::IsNullOrWhiteSpace($RuntimeLease)) {
        $supervisorArgs += @("--runtime-lease", $RuntimeLease)
    }
    if ($AutoImprove) {
        $supervisorArgs += "--enable-auto-improve"
    }
    $command = ($supervisorArgs | ForEach-Object { ConvertTo-CommandLineArgument $_ }) -join " "
    $autoImproveEnv = if ($AutoImprove) { "1" } else { "0" }
    $envAssignments = @(
        "set PYTHONUTF8=1",
        "set PYTHONIOENCODING=utf-8",
        "set BOT_LOG_TO_FILE=1",
        "set AUTO_START_OBS_SERVER=0",
        "set LOSS_TRIGGERED_DRAIN=0",
        "set FOULER_PLAY_ENABLE_AUTO_IMPROVE=$autoImproveEnv",
        "set BATTLE_STATS_MAX_ENTRIES=5000",
        "set FOULER_DEVSTREAM_STATUS_URL=http://ubunztu.tail4859dd.ts.net:8799/deku-metrics.json"
    )
    $leaseAccount = Get-RuntimeLeaseAccount -RuntimeLease $RuntimeLease
    if (-not [string]::IsNullOrWhiteSpace($leaseAccount)) {
        foreach ($envName in @("PS_USERNAME", "SHOWDOWN_USER_ID", "SHOWDOWN_ACCOUNTS", "FOULER_ACTIVE_ACCOUNT")) {
            $assignment = ConvertTo-CmdSetAssignment -Name $envName -Value $leaseAccount
            if (-not [string]::IsNullOrWhiteSpace($assignment)) {
                $envAssignments += $assignment
            }
        }
    }
    $envPrefix = ($envAssignments -join "&& ") + "&& "
    $commandLine = 'cmd.exe /d /c "{0}{1} 1>>"{2}" 2>>"{3}""' -f $envPrefix, $command, $stdout, $stderr
    $launch = Start-DetachedCommand -CommandLine $commandLine -WorkingDirectory $RepoRoot
    if (-not (Test-Path $PidDir)) { New-Item -ItemType Directory -Path $PidDir -Force | Out-Null }
    Write-JsonFile -Path (Join-Path $PidDir "jigglypuff-battle-session.json") -Payload @{
        pid = $launch.pid
        role = "battleSupervisor"
        runCount = $RunCount
        maxConcurrentBattles = $MaxConcurrentBattles
        maxCycles = $MaxCycles
        runtimeLease = $RuntimeLease
        autoImprove = [bool]$AutoImprove
        startedAt = Get-IsoNow
        stdout = $stdout
        stderr = $stderr
        launch = $launch
    }
    return @{ ok = [bool]$launch.ok; pid = $launch.pid; role = "battleSupervisor"; launch = $launch; stdout = $stdout; stderr = $stderr; autoImprove = [bool]$AutoImprove; maxCycles = $MaxCycles; runtimeLease = $RuntimeLease }
}

function Install-Runtime {
    $steps = @()
    $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        $steps += @{ name = "create-venv"; result = Invoke-Checked -FilePath "python" -ArgumentList @("-m", "venv", ".venv") -TimeoutSeconds 180 }
    }
    $python = Get-PythonPath
    $steps += @{ name = "pip-upgrade"; result = Invoke-Checked -FilePath $python -ArgumentList @("-m", "pip", "install", "--upgrade", "pip") -TimeoutSeconds 240 }
    $baseDeps = @(
        "aiohttp==3.10.11",
        "requests==2.32.4",
        "websockets==14.1",
        "python-dotenv==1.0.1",
        "python-dateutil==2.8.0",
        "psutil==6.1.1"
    )
    $steps += @{ name = "pip-base-dependencies"; result = Invoke-Checked -FilePath $python -ArgumentList (@("-m", "pip", "install") + $baseDeps) -TimeoutSeconds 300 }
    # Windows builds of poke-engine can hang when pip applies the Linux-oriented
    # config-settings line from requirements.txt. Install the pinned package
    # directly so the local MSVC/Rust toolchain can produce the wheel normally.
    $steps += @{ name = "pip-poke-engine"; result = Invoke-Checked -FilePath $python -ArgumentList @("-m", "pip", "install", "poke-engine==0.0.46") -TimeoutSeconds 600 }
    return @{ ok = -not (@($steps | Where-Object { -not $_.result.ok }).Count); steps = $steps }
}

function Invoke-LoginProof {
    $python = Get-PythonPath
    $result = Invoke-Checked -FilePath $python -ArgumentList @("scripts\showdown_login_check.py", "--execute", "--write", "--timeout-seconds", "25") -TimeoutSeconds 45
    $parsed = $null
    if ($result.stdout) {
        try { $parsed = $result.stdout | ConvertFrom-Json } catch {}
    }
    return @{ ok = [bool]$result.ok; result = $result; payload = $parsed }
}

function Get-Status {
    param([switch]$NoWrite)
    $repoExists = Test-Path $RepoRoot
    $envPresent = Test-Path (Join-Path $RepoRoot ".env")
    $venvPresent = Test-Path (Join-Path $RepoRoot ".venv\Scripts\python.exe")
    $scriptsPresent = @{
        devstreamHealth = Test-Path (Join-Path $RepoRoot "scripts\devstream_health.py")
        showdownLoginCheck = Test-Path (Join-Path $RepoRoot "scripts\showdown_login_check.py")
        startOneTouch = Test-Path (Join-Path $RepoRoot "start_one_touch.bat")
        obsServer = Test-Path (Join-Path $RepoRoot "streaming\serve_obs_page.py")
    }
    $processes = @(Get-ProcessInfo)
    $logicalProcesses = Get-LogicalProcessSummary -Processes $processes
    $processCount = @($processes).Count
    $obsOpen = Test-LocalPort -Port 8777
    $truth = Get-TruthStatus
    $producer = Get-ProducerEvidence
    $blockers = @()
    $warnings = @()
    if (-not [bool]$producer.expectedHostMatched) {
        $blockers += "JIGGLYPUFF runtime status was produced on unexpected host: $($producer.host)"
    }
    if (-not $repoExists) { $blockers += "repo root is missing: $RepoRoot" }
    if (-not $envPresent) { $blockers += ".env is missing on JIGGLYPUFF" }
    if (-not $scriptsPresent.devstreamHealth) { $blockers += "scripts/devstream_health.py is missing; checkout is stale" }
    if (-not $scriptsPresent.startOneTouch) { $blockers += "start_one_touch.bat is missing" }
    if (-not $scriptsPresent.obsServer) { $blockers += "streaming/serve_obs_page.py is missing" }
    if (-not $venvPresent) { $warnings += ".venv is missing; using global python until bootstrap completes" }
    if ([int]$logicalProcesses.obsServer.leafCount -gt 1) {
        $blockers += "multiple Fouler OBS HTTP servers are running: $($logicalProcesses.obsServer.pids -join ', ')"
    }
    if ([int]$logicalProcesses.battleSession.leafCount -gt 1) {
        $blockers += "multiple Fouler battle sessions are running: $($logicalProcesses.battleSession.pids -join ', ')"
    }
    if ([int]$logicalProcesses.battleSupervisor.leafCount -gt 1) {
        $blockers += "multiple Fouler battle supervisors are running: $($logicalProcesses.battleSupervisor.pids -join ', ')"
    }
    if ($obsOpen -and [int]$logicalProcesses.obsServer.leafCount -eq 0) {
        $warnings += "OBS HTTP port is open but no canonical Fouler OBS server process was identified"
    }
    $streamStatus = ($truth | Where-Object { $_.relativePath -eq "stream_status.json" } | Select-Object -First 1).summary
    if ($streamStatus -and $streamStatus.runtimeBlocked) {
        $warnings += "stream_status.json still records runtime_blocked: $($streamStatus.blockerCode)"
    }
    $running = ($processCount -gt 0) -or $obsOpen
    $deployReady = $repoExists -and $envPresent -and $scriptsPresent.devstreamHealth -and $scriptsPresent.startOneTouch -and $scriptsPresent.obsServer
    $healthy = $deployReady -and ($blockers.Count -eq 0)
    $status = "idle"
    if ($blockers.Count -gt 0) {
        $status = "blocked"
    } elseif ($running) {
        $status = "running"
    } elseif ($deployReady) {
        $status = "ready-idle"
    }
    $payload = @{
        schemaVersion = "fouler-play-jigglypuff-runtime/v1"
        checkedAt = Get-IsoNow
        machine = "JIGGLYPUFF"
        repoRoot = $RepoRoot
        repoExists = $repoExists
        ok = $healthy
        healthy = $healthy
        running = $running
        status = $status
        deployReady = $deployReady
        blockers = @($blockers)
        warnings = @($warnings)
        producer = $producer
        proofArtifact = @{
            path = $RemoteTruthPath
            written = -not [bool]$NoWrite
            noWrite = [bool]$NoWrite
        }
        git = Get-GitInfo
        envPresent = $envPresent
        venvPresent = $venvPresent
        python = @{ path = (Get-PythonPath) }
        scriptsPresent = $scriptsPresent
        processes = @($processes)
        processCount = $processCount
        logicalProcesses = $logicalProcesses
        scheduledTasks = @(Get-TaskInfo)
        ports = @{ obsHttp = @{ host = "127.0.0.1"; port = 8777; open = $obsOpen } }
        endpoints = @{
            health = Get-Endpoint -Path "/health"
            state = Get-Endpoint -Path "/state"
            slot1 = Get-Endpoint -Path "/slot/1"
            dashboard = Get-Endpoint -Path "/dashboard/hybrid"
        }
        truth = @($truth)
    }
    if (-not $NoWrite) {
        Write-JsonFile -Path $RemoteTruthPath -Payload $payload
    }
    return $payload
}

$actions = @()
if ($Command -eq "bootstrap") {
    if (-not $Execute) {
        $payload = @{
            schemaVersion = "fouler-play-jigglypuff-action/v1"
            checkedAt = Get-IsoNow
            action = "bootstrap"
            execute = $false
            planned = $true
            message = "Pass -Execute to create .venv and install requirements."
            postStatus = Get-Status -NoWrite:$NoWrite
        }
        $payload | ConvertTo-Json -Depth 10
        exit 0
    }
    $actions += @{ name = "bootstrap"; result = Install-Runtime }
} elseif ($Command -eq "stop") {
    if ($Execute) {
        $actions += @{ name = "stop-processes"; result = Stop-FoulerProcesses }
    } else {
        $actions += @{ name = "stop-processes"; planned = $true }
    }
} elseif ($Command -eq "start") {
    if ($Execute) {
        $lease = Test-RuntimeLease -RunCount $RunCount -MaxConcurrentBattles $MaxConcurrentBattles -MaxCycles $MaxCycles -RuntimeLease $RuntimeLease
        $actions += @{ name = "runtime-lease"; result = $lease }
        if ($lease.ok) {
            $actions += @{ name = "stop-stale-processes"; result = Stop-FoulerProcesses }
            $actions += @{ name = "start-obs-server"; result = Start-ObsServer }
            if (-not $ObsOnly) {
                $actions += @{ name = "start-battle-supervisor"; result = Start-BattleSession -RunCount $RunCount -MaxConcurrentBattles $MaxConcurrentBattles -MaxCycles $MaxCycles -RuntimeLease $RuntimeLease -AutoImprove:$AutoImprove }
            }
        }
    } else {
        $actions += @{ name = "start-obs-server"; planned = $true }
        if (-not $ObsOnly) {
            $actions += @{ name = "start-battle-supervisor"; planned = $true; runCount = $RunCount; maxConcurrentBattles = $MaxConcurrentBattles; maxCycles = $MaxCycles; runtimeLease = $RuntimeLease; autoImprove = [bool]$AutoImprove }
        }
    }
} elseif ($Command -eq "login-proof") {
    if ($Execute) {
        $actions += @{ name = "showdown-login-proof"; result = Invoke-LoginProof }
    } else {
        $actions += @{ name = "showdown-login-proof"; planned = $true }
    }
}

$final = @{
    schemaVersion = "fouler-play-jigglypuff-control/v1"
    checkedAt = Get-IsoNow
    action = $Command
    execute = [bool]$Execute
    obsOnly = [bool]$ObsOnly
    autoImprove = [bool]$AutoImprove
    maxCycles = $MaxCycles
    runtimeLease = $RuntimeLease
    actions = @($actions)
    postStatus = Get-Status -NoWrite:$NoWrite
}

if ($Command -eq "status") {
    $final = Get-Status -NoWrite:$NoWrite
}

$final | ConvertTo-Json -Depth 12
