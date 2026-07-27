[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9a-fA-F]{40}$')]
    [string]$SourceCommit,
    [string]$RemoteUrl = "https://github.com/RJW34/fouler-play.git",
    [string]$ReleaseRoot = "D:\Releases\fouler-play",
    [string]$StagingRoot = "D:\Releases\fouler-play-staging",
    [string]$ManifestRoot = "C:\ProgramData\HERMES\staging\fouler\manifests",
    [Parameter(Mandatory = $true)]
    [string]$BootstrapPython,
    [string]$GitExecutable = "",
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$SourceCommit = $SourceCommit.ToLowerInvariant()

function Resolve-ExactExecutable {
    param([Parameter(Mandatory = $true)][string]$Value, [Parameter(Mandatory = $true)][string]$Label)
    $candidate = $Value
    if (-not [System.IO.Path]::IsPathRooted($candidate)) {
        $command = Get-Command $candidate -CommandType Application -ErrorAction Stop | Select-Object -First 1
        $candidate = $command.Source
    }
    $resolved = [System.IO.Path]::GetFullPath($candidate)
    if (-not (Test-Path -LiteralPath $resolved -PathType Leaf)) {
        throw "$Label is missing: $resolved"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
        throw "$Label must not be a reparse point: $resolved"
    }
    return $resolved
}

function Assert-NoReparsePathChain {
    param([Parameter(Mandatory = $true)][string]$Path)
    $cursor = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    $root = [System.IO.Path]::GetPathRoot($cursor).TrimEnd("\")
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "path ancestry contains a reparse point: $cursor"
            }
        }
        if ([string]::Equals($cursor, $root, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $parent = [System.IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $next = $parent.FullName.TrimEnd("\")
        if ([string]::Equals($next, $cursor, [System.StringComparison]::OrdinalIgnoreCase)) { break }
        $cursor = $next
    }
}

function Assert-NoPathOverlap {
    param(
        [Parameter(Mandatory = $true)][string]$First,
        [Parameter(Mandatory = $true)][string]$Second,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $left = [System.IO.Path]::GetFullPath($First).TrimEnd("\")
    $right = [System.IO.Path]::GetFullPath($Second).TrimEnd("\")
    if (
        [string]::Equals($left, $right, [System.StringComparison]::OrdinalIgnoreCase) -or
        $left.StartsWith($right + "\", [System.StringComparison]::OrdinalIgnoreCase) -or
        $right.StartsWith($left + "\", [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw $Label
    }
}

function Invoke-Checked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Label,
        [int]$TimeoutSeconds = 3600
    )
    $stdout = [System.IO.Path]::GetTempFileName()
    $stderr = [System.IO.Path]::GetTempFileName()
    try {
        $process = Start-Process -FilePath $FilePath -ArgumentList $Arguments `
            -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput $stdout -RedirectStandardError $stderr
        # Cache the process handle immediately so ExitCode is retained after the
        # process exits. Without this, Start-Process -PassThru can report a null
        # ExitCode in non-interactive hosts (e.g. an SSH session), which the
        # check below would misread as a nonzero failure.
        $null = $process.Handle
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            try { $process.Kill() } catch {}
            throw "$Label timed out after $TimeoutSeconds seconds"
        }
        $outText = [System.IO.File]::ReadAllText($stdout)
        $errText = [System.IO.File]::ReadAllText($stderr)
        if ($process.ExitCode -ne 0) {
            $tail = (($outText + [Environment]::NewLine + $errText) -split "`r?`n" | Select-Object -Last 80) -join [Environment]::NewLine
            throw "$Label failed with exit code $($process.ExitCode):`n$tail"
        }
        return [pscustomobject]@{
            ExitCode = $process.ExitCode
            Stdout = $outText
            Stderr = $errText
        }
    }
    finally {
        Remove-Item -LiteralPath $stdout, $stderr -Force -ErrorAction SilentlyContinue
    }
}

function Get-ReleaseFileInventory {
    param([Parameter(Mandatory = $true)][string]$Root)
    $rootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd("\")
    $items = @(Get-ChildItem -LiteralPath $rootPath -Recurse -Force -ErrorAction Stop)
    $reparse = @($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($reparse.Count -gt 0) {
        throw "release contains a reparse point: $($reparse[0].FullName)"
    }
    $sourceBytecode = @($items | Where-Object {
        -not $_.PSIsContainer -and
        -not $_.FullName.StartsWith((Join-Path $rootPath ".venv").TrimEnd("\") + "\", [System.StringComparison]::OrdinalIgnoreCase) -and
        $_.Extension -in @(".pyc", ".pyo")
    })
    if ($sourceBytecode.Count -gt 0) {
        throw "release contains source bytecode: $($sourceBytecode[0].FullName)"
    }
    $files = [ordered]@{}
    foreach ($file in @($items | Where-Object { -not $_.PSIsContainer } | Sort-Object FullName)) {
        $relative = $file.FullName.Substring($rootPath.Length + 1).Replace("\", "/")
        $files[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    return $files
}

$git = Resolve-ExactExecutable -Value ($(if ($GitExecutable) { $GitExecutable } else { "git.exe" })) -Label "git executable"
$python = Resolve-ExactExecutable -Value $BootstrapPython -Label "bootstrap Python"
$releaseRootPath = [System.IO.Path]::GetFullPath($ReleaseRoot).TrimEnd("\")
$stagingRootPath = [System.IO.Path]::GetFullPath($StagingRoot).TrimEnd("\")
$manifestRootPath = [System.IO.Path]::GetFullPath($ManifestRoot).TrimEnd("\")
$destination = Join-Path $releaseRootPath $SourceCommit
$manifestPath = Join-Path $manifestRootPath "$SourceCommit.json"

Assert-NoPathOverlap -First $releaseRootPath -Second $stagingRootPath -Label "release and staging roots must not overlap"
Assert-NoPathOverlap -First $releaseRootPath -Second $manifestRootPath -Label "manifest root must be external to releases"
Assert-NoPathOverlap -First $stagingRootPath -Second $manifestRootPath -Label "manifest root must be external to staging"
foreach ($path in @($releaseRootPath, $stagingRootPath, $manifestRootPath)) {
    Assert-NoReparsePathChain -Path $path
}
if (Test-Path -LiteralPath $destination) {
    throw "immutable release destination already exists: $destination"
}
if (Test-Path -LiteralPath $manifestPath) {
    throw "immutable release manifest already exists: $manifestPath"
}

$plan = [ordered]@{
    schemaVersion = "fouler-release-stage-plan/v1"
    ok = $true
    apply = [bool]$Apply
    sourceCommit = $SourceCommit
    remoteUrl = $RemoteUrl
    destination = $destination
    manifestPath = $manifestPath
    bootstrapPython = $python
    gitExecutable = $git
    startsRuntime = $false
    overwritesExistingRelease = $false
}
if (-not $Apply) {
    $plan | ConvertTo-Json -Depth 8
    exit 0
}

New-Item -ItemType Directory -Path $releaseRootPath, $stagingRootPath, $manifestRootPath -Force | Out-Null
foreach ($path in @($releaseRootPath, $stagingRootPath, $manifestRootPath)) {
    Assert-NoReparsePathChain -Path $path
}

$lockPath = Join-Path $stagingRootPath "stage-release.lock"
$lockStream = $null
$staging = Join-Path $stagingRootPath ("." + $SourceCommit + "." + [guid]::NewGuid().ToString("N") + ".staging")
$testTemp = Join-Path $stagingRootPath ("pytest-" + [guid]::NewGuid().ToString("N"))
try {
    $lockStream = [System.IO.File]::Open(
        $lockPath,
        [System.IO.FileMode]::OpenOrCreate,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    New-Item -ItemType Directory -Path $staging -ErrorAction Stop | Out-Null
    Invoke-Checked -FilePath $git -Arguments @("init", "--quiet") -WorkingDirectory $staging -Label "git init" | Out-Null
    Invoke-Checked -FilePath $git -Arguments @("remote", "add", "origin", $RemoteUrl) -WorkingDirectory $staging -Label "git remote add" | Out-Null
    Invoke-Checked -FilePath $git -Arguments @("-c", "core.hooksPath=NUL", "fetch", "--no-tags", "--depth=1", "origin", $SourceCommit) -WorkingDirectory $staging -Label "fetch exact pushed commit" -TimeoutSeconds 900 | Out-Null
    $fetched = (Invoke-Checked -FilePath $git -Arguments @("rev-parse", "FETCH_HEAD") -WorkingDirectory $staging -Label "resolve fetched commit").Stdout.Trim().ToLowerInvariant()
    if ($fetched -ne $SourceCommit) { throw "fetched commit does not match SourceCommit" }
    Invoke-Checked -FilePath $git -Arguments @("-c", "core.hooksPath=NUL", "checkout", "--detach", "--force", $SourceCommit) -WorkingDirectory $staging -Label "checkout exact pushed commit" | Out-Null
    if (Test-Path -LiteralPath (Join-Path $staging ".gitmodules")) {
        throw "release staging does not permit git submodules"
    }

    Invoke-Checked -FilePath $python -Arguments @("-I", "-m", "venv", (Join-Path $staging ".venv")) -WorkingDirectory $staging -Label "create release virtual environment" -TimeoutSeconds 900 | Out-Null
    $venvPython = Resolve-ExactExecutable -Value (Join-Path $staging ".venv\Scripts\python.exe") -Label "release Python"
    Invoke-Checked -FilePath $venvPython -Arguments @("-I", "-m", "pip", "install", "--disable-pip-version-check", "--no-input", "--no-cache-dir", "-r", "requirements-dev.txt") -WorkingDirectory $staging -Label "install pinned release requirements" -TimeoutSeconds 3600 | Out-Null
    Invoke-Checked -FilePath $venvPython -Arguments @("-I", "-m", "pip", "check") -WorkingDirectory $staging -Label "pip dependency check" | Out-Null
    # The legacy repository is not globally style-clean. Fail closed on parser,
    # undefined-name, and other fatal errors everywhere, then apply the full
    # Ruff rule set to the production authority/runtime surface. The excluded
    # file is documentation pseudocode and is never imported or launched.
    Invoke-Checked -FilePath $venvPython -Arguments @(
        "-I", "-m", "ruff", "check", ".",
        "--select", "E9,F63,F7,F82",
        "--exclude", "launch_integration_example.py"
    ) -WorkingDirectory $staging -Label "repository fatal lint gate" -TimeoutSeconds 900 | Out-Null
    $strictRuffPaths = @(
        "run.py",
        "config.py",
        "process_lock.py",
        "fp/devstream_chat.py",
        "infrastructure/deployment_lineage.py",
        "infrastructure/deployment_state.py",
        "infrastructure/elo_watchdog.py",
        "infrastructure/head_to_head_authority.py",
        "infrastructure/head_to_head_eval.py",
        "infrastructure/improve_agent.py",
        "infrastructure/runtime_authorization.py",
        "infrastructure/season_runtime_authority.py",
        "infrastructure/runtime_lease_client.py",
        "infrastructure/windows/fouler_lease_broker.py",
        "scripts/devstream_runtime_lease.py",
        "scripts/devstream_session.py",
        "scripts/run_bounded_battle_session.py",
        "scripts/fouler_deployment_receipt.py",
        "scripts/fouler_deployment_state.py",
        "scripts/fouler_runtime_authority.py",
        "scripts/season_ladder_supervisor.py",
        "streaming/run_obs_server_service.py"
    )
    Invoke-Checked -FilePath $venvPython -Arguments (@("-I", "-m", "ruff", "check") + $strictRuffPaths) -WorkingDirectory $staging -Label "strict runtime lint gate" -TimeoutSeconds 900 | Out-Null
    New-Item -ItemType Directory -Path $testTemp -ErrorAction Stop | Out-Null
    Invoke-Checked -FilePath $venvPython -Arguments @("-I", "-m", "pytest", "-q", "--basetemp", $testTemp) -WorkingDirectory $staging -Label "full release test gate" -TimeoutSeconds 7200 | Out-Null

    Invoke-Checked -FilePath $git -Arguments @("clean", "-ffdx", "-e", ".venv/") -WorkingDirectory $staging -Label "clean generated release artifacts" | Out-Null
    $head = (Invoke-Checked -FilePath $git -Arguments @("rev-parse", "HEAD") -WorkingDirectory $staging -Label "verify staged HEAD").Stdout.Trim().ToLowerInvariant()
    if ($head -ne $SourceCommit) { throw "staged HEAD changed during verification" }
    $trackedStatus = (Invoke-Checked -FilePath $git -Arguments @("status", "--porcelain=v1", "--untracked-files=no") -WorkingDirectory $staging -Label "verify clean tracked release").Stdout.Trim()
    if ($trackedStatus) { throw "staged release has tracked modifications: $trackedStatus" }

    $files = Get-ReleaseFileInventory -Root $staging
    foreach ($required in @(
        ".venv/Scripts/python.exe",
        "scripts/devstream_runtime_lease.py",
        "scripts/devstream_session.py",
        "scripts/run_bounded_battle_session.py",
        "scripts/season_ladder_supervisor.py",
        "scripts/install_season_supervisor_task.ps1",
        "run.py",
        "infrastructure/season_runtime_authority.py",
        "infrastructure/deployment_lineage.py",
        "infrastructure/head_to_head_authority.py",
        "infrastructure/head_to_head_eval.py",
        "infrastructure/runtime_authorization.py",
        "infrastructure/windows/fouler_lease_broker.py",
        "scripts/install_obs_server_service.ps1",
        "streaming/run_obs_server_service.py",
        "streaming/serve_obs_page.py"
    )) {
        if (-not $files.Contains($required)) { throw "release inventory omits required file: $required" }
    }
    $manifest = [ordered]@{
        schemaVersion = "fouler-bootstrap-manifest/v1"
        projectId = "fouler-play"
        sourceCommit = $SourceCommit
        createdAt = [DateTimeOffset]::UtcNow.ToString("o")
        remoteUrl = $RemoteUrl
        files = $files
    }
    $manifestJson = ($manifest | ConvertTo-Json -Depth 8) + [Environment]::NewLine
    $utf8 = New-Object System.Text.UTF8Encoding($false)
    $manifestTemp = Join-Path $manifestRootPath ("." + $SourceCommit + "." + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [System.IO.File]::WriteAllText($manifestTemp, $manifestJson, $utf8)
        if (Test-Path -LiteralPath $manifestPath) { throw "manifest destination appeared during staging" }
        Move-Item -LiteralPath $manifestTemp -Destination $manifestPath -ErrorAction Stop
    }
    finally {
        Remove-Item -LiteralPath $manifestTemp -Force -ErrorAction SilentlyContinue
    }

    if (Test-Path -LiteralPath $destination) { throw "release destination appeared during staging" }
    Move-Item -LiteralPath $staging -Destination $destination -ErrorAction Stop
    $result = [ordered]@{
        schemaVersion = "fouler-release-stage-result/v1"
        ok = $true
        sourceCommit = $SourceCommit
        destination = $destination
        manifestPath = $manifestPath
        manifestSha256 = (Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant()
        fileCount = $files.Count
        gitHead = $head
        testsPassed = $true
        repositoryFatalLintPassed = $true
        strictRuntimeLintPassed = $true
        pipCheckPassed = $true
        runtimeStarted = $false
    }
    $result | ConvertTo-Json -Depth 8
}
catch {
    $failure = [ordered]@{
        schemaVersion = "fouler-release-stage-result/v1"
        ok = $false
        sourceCommit = $SourceCommit
        destination = $destination
        stagingPath = $staging
        runtimeStarted = $false
        error = $_.Exception.Message
    }
    $failure | ConvertTo-Json -Depth 8
    exit 2
}
finally {
    if ($null -ne $lockStream) {
        $lockStream.Dispose()
    }
    if (Test-Path -LiteralPath $testTemp) {
        Remove-Item -LiteralPath $testTemp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
