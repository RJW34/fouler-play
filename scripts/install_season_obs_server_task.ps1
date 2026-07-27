[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$SourceCommit,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^season-[0-9a-z][0-9a-z.-]{7,95}$')]
    [string]$SeasonId,
    [string]$TaskName = "DEVSTREAM-JIG-FoulerObsServer",
    [string]$RuntimeAccount = "devstream-live",
    [string]$ReleaseRoot = "E:\Devstream\Releases\fouler-play",
    [string]$ManifestRoot = "C:\ProgramData\Devstream\staging\fouler\manifests",
    [string]$AuthorityRoot = "C:\ProgramData\Devstream\authority\fouler",
    [string]$RuntimeRoot = "E:\DevstreamRuntime\fouler",
    [string]$BackupRoot = "E:\DevstreamRestoration",
    [string]$LegacyServiceName = "HERMES-FoulerObsServer",
    [string[]]$LegacyTaskNames = @("HERMES-FoulerObsKeepAlive", "HERMES-FoulerObsServer"),
    [switch]$Apply,
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$SourceCommit = $SourceCommit.ToLowerInvariant()
$SeasonId = $SeasonId.ToLowerInvariant()
if ($Start -and -not $Apply) { throw "-Start requires -Apply" }

$release = [System.IO.Path]::GetFullPath((Join-Path $ReleaseRoot $SourceCommit)).TrimEnd("\")
$manifest = [System.IO.Path]::GetFullPath((Join-Path $ManifestRoot "$SourceCommit.json"))
$authority = [System.IO.Path]::GetFullPath((Join-Path $AuthorityRoot "$SeasonId.json"))
$python = Join-Path $release ".venv\Scripts\python.exe"
$entrypoint = Join-Path $release "streaming\run_season_obs_server.py"
$stateRoot = Join-Path $RuntimeRoot "state"
$timestamp = [DateTimeOffset]::UtcNow.ToString("yyyyMMddTHHmmssZ")
$backup = Join-Path $BackupRoot "$timestamp\fouler-obs-cutover"
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

function Invoke-IcaclsChecked {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    & "$env:SystemRoot\System32\icacls.exe" @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "icacls failed with exit code $LASTEXITCODE" }
}

function Invoke-Git {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $output = @(& git.exe -C $release @Arguments 2>&1)
    if ($LASTEXITCODE -ne 0) {
        throw "git $($Arguments -join ' ') failed: $($output -join [Environment]::NewLine)"
    }
    return ($output -join [Environment]::NewLine).Trim()
}

function Assert-ManifestedRelease {
    Assert-RegularFile -Path $python -Label "immutable release Python"
    Assert-RegularFile -Path $entrypoint -Label "finite-season OBS entrypoint"
    Assert-RegularFile -Path $manifest -Label "immutable release manifest"
    Assert-RegularFile -Path $authority -Label "finite-season authority"
    foreach ($path in @($release, $manifest, $authority, $RuntimeRoot, $backup)) {
        Assert-NoReparsePathChain -Path $path
    }
    $head = (Invoke-Git -Arguments @("rev-parse", "HEAD")).ToLowerInvariant()
    if ($head -ne $SourceCommit) { throw "immutable release HEAD does not match SourceCommit" }
    if (Invoke-Git -Arguments @("status", "--porcelain=v1", "--untracked-files=no")) {
        throw "immutable release contains tracked modifications"
    }
    $payload = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($payload.schemaVersion -ne "fouler-bootstrap-manifest/v1" -or
        $payload.projectId -ne "fouler-play" -or
        ([string]$payload.sourceCommit).ToLowerInvariant() -ne $SourceCommit) {
        throw "immutable release manifest identity does not match SourceCommit"
    }
    $expected = @{}
    foreach ($property in @($payload.files.PSObject.Properties)) {
        $expected[[string]$property.Name] = ([string]$property.Value).ToLowerInvariant()
    }
    foreach ($required in @(
        ".venv/Scripts/python.exe",
        "streaming/run_season_obs_server.py",
        "streaming/serve_obs_page.py"
    )) {
        if (-not $expected.ContainsKey($required)) {
            throw "release manifest omits required OBS file: $required"
        }
    }
    $items = @(Get-ChildItem -LiteralPath $release -Recurse -Force -ErrorAction Stop)
    $reparse = @($items | Where-Object {
        ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0
    })
    if ($reparse.Count -gt 0) { throw "release contains a reparse point: $($reparse[0].FullName)" }
    $files = @($items | Where-Object { -not $_.PSIsContainer })
    if ($files.Count -ne $expected.Count) {
        throw "release file inventory count no longer matches the bootstrap manifest"
    }
    foreach ($file in $files) {
        $relative = $file.FullName.Substring($release.Length + 1).Replace("\", "/")
        if (-not $expected.ContainsKey($relative)) {
            throw "release contains an unmanifested file: $relative"
        }
        $digest = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($digest -ne $expected[$relative]) {
            throw "manifested release file hash changed: $relative"
        }
    }
}

function Get-TaskSnapshot {
    param([Parameter(Mandatory = $true)][string]$Name)
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($null -eq $task) {
        return [ordered]@{ name = $Name; present = $false; enabled = $false; running = $false }
    }
    $xml = Join-Path $backup ("scheduled-task-" + ($Name -replace '[^A-Za-z0-9_.-]', '_') + ".xml")
    Export-ScheduledTask -TaskName $Name | Set-Content -LiteralPath $xml -Encoding UTF8
    return [ordered]@{
        name = $Name
        present = $true
        enabled = $task.State -ne "Disabled"
        running = $task.State -eq "Running"
        xmlPath = $xml
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

function Get-PortOwner {
    $owners = @(Get-NetTCPConnection -State Listen -LocalPort 8777 -ErrorAction SilentlyContinue |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($owners.Count -gt 1) { throw "multiple processes listen on loopback port 8777" }
    if ($owners.Count -eq 1) { return [int]$owners[0] }
    return 0
}

function Assert-LoopbackListener {
    $listeners = @(Get-NetTCPConnection -State Listen -LocalPort 8777 -ErrorAction SilentlyContinue)
    if ($listeners.Count -ne 1 -or $listeners[0].LocalAddress -ne "127.0.0.1") {
        throw "finite-season OBS must own exactly one IPv4 loopback listener on port 8777"
    }
}

function Test-ProcessDescendsFrom {
    param([Parameter(Mandatory = $true)][int]$ProcessId, [Parameter(Mandatory = $true)][int]$AncestorId)
    $cursor = $ProcessId
    $seen = @{}
    while ($cursor -gt 0 -and -not $seen.ContainsKey($cursor)) {
        if ($cursor -eq $AncestorId) { return $true }
        $seen[$cursor] = $true
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $cursor" -ErrorAction SilentlyContinue
        if ($null -eq $record) { break }
        $cursor = [int]$record.ParentProcessId
    }
    return $false
}

function Get-NewObsProcesses {
    return @(Get-CimInstance Win32_Process | Where-Object {
        $command = [string]$_.CommandLine
        $command -match '(?i)run_season_obs_server\.py' -and
        $command.IndexOf($release, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    })
}

function Stop-NewObsProcesses {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    foreach ($process in @(Get-NewObsProcesses)) {
        Stop-Process -Id ([int]$process.ProcessId) -Force -ErrorAction SilentlyContinue
    }
}

function Wait-PortState {
    param([Parameter(Mandatory = $true)][bool]$Open, [int]$Seconds = 60)
    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($Seconds)
    do {
        $owner = Get-PortOwner
        if (($Open -and $owner -gt 0) -or (-not $Open -and $owner -eq 0)) { return $owner }
        Start-Sleep -Seconds 1
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "loopback port 8777 did not become $(if ($Open) { 'open' } else { 'closed' })"
}

function Test-Health {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:8777/health"
        return [int]$response.StatusCode -eq 200
    }
    catch { return $false }
}

Assert-ManifestedRelease
$authoritySha256 = (Get-FileHash -LiteralPath $authority -Algorithm SHA256).Hash.ToLowerInvariant()
$checkOutput = @(
    & $python -I -B $entrypoint --authority $authority --authority-sha256 $authoritySha256 --check 2>&1
)
if ($LASTEXITCODE -ne 0) {
    throw "finite-season OBS entrypoint admission failed: $($checkOutput -join [Environment]::NewLine)"
}

$legacyService = Get-Service -Name $LegacyServiceName -ErrorAction SilentlyContinue
$legacyServiceRecord = Get-CimInstance Win32_Service -Filter "Name = '$($LegacyServiceName.Replace("'", "''"))'" -ErrorAction SilentlyContinue
$legacyServiceRunning = [bool]($legacyService -and $legacyService.Status -eq "Running")
$legacyServiceStartType = if ($legacyService) { $legacyService.StartType.ToString() } else { "Missing" }
$legacyTaskState = @{}
foreach ($name in $LegacyTaskNames) {
    $legacyTask = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    $legacyTaskState[$name] = if ($legacyTask) { $legacyTask.State.ToString() } else { "missing" }
    if ($legacyTask -and $legacyTask.State -ne "Disabled") {
        throw "legacy OBS scheduled task must be disabled before service cutover: $name"
    }
}
$preOwner = Get-PortOwner
if ($preOwner -gt 0) {
    if (-not $legacyServiceRunning -or $null -eq $legacyServiceRecord -or
        -not (Test-ProcessDescendsFrom -ProcessId $preOwner -AncestorId ([int]$legacyServiceRecord.ProcessId))) {
        throw "port 8777 is owned outside the declared legacy OBS service: $preOwner"
    }
}

$plan = [ordered]@{
    schemaVersion = "fouler-season-obs-install-plan/v1"
    ok = $true
    apply = [bool]$Apply
    start = [bool]$Start
    taskName = $TaskName
    sourceCommit = $SourceCommit
    seasonId = $SeasonId
    release = $release
    manifest = $manifest
    authority = $authority
    authoritySha256 = $authoritySha256
    runtimeRoot = $RuntimeRoot
    legacyService = $LegacyServiceName
    legacyServiceState = if ($legacyService) { $legacyService.Status.ToString() } else { "missing" }
    legacyTasks = $legacyTaskState
    port8777Owner = $preOwner
    publicOutputChanged = $false
    startStreaming = $false
}
if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 8
    exit 0
}

New-Item -ItemType Directory -Path $backup -Force | Out-Null
$taskSnapshots = @{}
foreach ($name in @($TaskName) + $LegacyTaskNames) {
    $taskSnapshots[$name] = Get-TaskSnapshot -Name $name
}
if ($legacyService) {
    @(& "$env:SystemRoot\System32\sc.exe" qc $LegacyServiceName) |
        Set-Content -LiteralPath (Join-Path $backup "$LegacyServiceName-sc-qc.txt") -Encoding UTF8
    & "$env:SystemRoot\System32\reg.exe" export `
        "HKLM\SYSTEM\CurrentControlSet\Services\$LegacyServiceName" `
        (Join-Path $backup "$LegacyServiceName.reg") /y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "legacy OBS service registry backup failed" }
}

$cutoverStarted = $false
try {
    foreach ($path in @(
        (Join-Path $RuntimeRoot "state"),
        (Join-Path $RuntimeRoot "logs"),
        (Join-Path $RuntimeRoot "cache"),
        (Join-Path $RuntimeRoot "temp")
    )) {
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        Assert-NoReparsePathChain -Path $path
        Invoke-IcaclsChecked -Arguments @(
            $path, "/inheritance:r",
            "/grant:r",
            "${systemSid}:(OI)(CI)F",
            "${administratorsSid}:(OI)(CI)F",
            "${runtimeSid}:(OI)(CI)M",
            "/T", "/C"
        )
    }

    $action = New-ScheduledTaskAction `
        -Execute $python `
        -Argument "-I -B -u `"$entrypoint`" --authority `"$authority`" --authority-sha256 $authoritySha256" `
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
        -Description "Immutable finite-season Fouler OBS loopback server" `
        -Force | Out-Null
    Disable-ScheduledTask -TaskName $TaskName | Out-Null

    if ($Start) {
        $cutoverStarted = $true
        if ($legacyServiceRunning) {
            Stop-Service -Name $LegacyServiceName -Force -ErrorAction Stop
            Wait-PortState -Open $false -Seconds 90 | Out-Null
        }
        foreach ($name in $LegacyTaskNames) {
            if (Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue) {
                Disable-ScheduledTask -TaskName $name | Out-Null
            }
        }
        Enable-ScheduledTask -TaskName $TaskName | Out-Null
        Start-ScheduledTask -TaskName $TaskName
        $listener = Wait-PortState -Open $true -Seconds 120
        $record = Get-CimInstance Win32_Process -Filter "ProcessId = $listener" -ErrorAction Stop
        $command = [string]$record.CommandLine
        if ($command -notmatch '(?i)run_season_obs_server\.py' -or
            $command.IndexOf($release, [System.StringComparison]::OrdinalIgnoreCase) -lt 0 -or
            $command.IndexOf($authoritySha256, [System.StringComparison]::OrdinalIgnoreCase) -lt 0) {
            throw "port 8777 is not owned by the exact finite-season OBS entrypoint"
        }
        $deadline = [DateTimeOffset]::UtcNow.AddMinutes(2)
        do {
            if (Test-Health) { break }
            Start-Sleep -Seconds 2
        } while ([DateTimeOffset]::UtcNow -lt $deadline)
        if (-not (Test-Health)) { throw "finite-season OBS /health did not become ready" }
        Assert-LoopbackListener
        if ($legacyService) {
            Set-Service -Name $LegacyServiceName -StartupType Disabled
        }
    }

    $result = [ordered]@{
        schemaVersion = "fouler-season-obs-install-result/v1"
        ok = $true
        taskName = $TaskName
        taskState = (Get-ScheduledTask -TaskName $TaskName).State.ToString()
        taskUser = $runtimePrincipal
        sourceCommit = $SourceCommit
        seasonId = $SeasonId
        authoritySha256 = $authoritySha256
        manifestSha256 = (Get-FileHash -LiteralPath $manifest -Algorithm SHA256).Hash.ToLowerInvariant()
        runtimeRoot = $RuntimeRoot
        port8777Owner = Get-PortOwner
        healthOk = if ($Start) { Test-Health } else { $null }
        legacyServiceState = if ($legacyService) {
            (Get-Service -Name $LegacyServiceName).Status.ToString()
        } else { "missing" }
        legacyServiceStartType = if ($legacyService) {
            (Get-Service -Name $LegacyServiceName).StartType.ToString()
        } else { "missing" }
        legacyTasks = $legacyTaskState
        backup = $backup
        publicOutputChanged = $false
        startStreaming = $false
    }
    $result | ConvertTo-Json -Depth 10
}
catch {
    try {
        Stop-NewObsProcesses
        foreach ($name in @($TaskName) + $LegacyTaskNames) {
            Restore-TaskSnapshot -Snapshot $taskSnapshots[$name]
        }
        if ($cutoverStarted -and $legacyService) {
            Set-Service -Name $LegacyServiceName -StartupType $legacyServiceStartType
            if ($legacyServiceRunning) {
                Start-Service -Name $LegacyServiceName
                Wait-PortState -Open $true -Seconds 90 | Out-Null
            }
        }
    }
    catch {
        [Console]::Error.WriteLine("OBS rollback also failed; inspect backup $backup")
    }
    [ordered]@{
        schemaVersion = "fouler-season-obs-install-result/v1"
        ok = $false
        sourceCommit = $SourceCommit
        seasonId = $SeasonId
        backup = $backup
        error = $_.Exception.Message
        publicOutputChanged = $false
        startStreaming = $false
    } | ConvertTo-Json -Depth 8
    exit 2
}
