param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Uninstall,
    [switch]$DisableLegacyTasks,
    [switch]$ProvisionIdentityOnly,
    [switch]$SkipHttpProbe,
    [string]$ProjectDir = "",
    [string]$ServiceName = "HERMES-FoulerObsServer",
    [string]$NssmSource = "",
    [string]$ExpectedNssmSha256 = "",
    [string]$RuntimeAccount = "JIGGLYPUFF\devstream-live",
    [string]$ServiceAccount = "NT SERVICE\HERMES-FoulerObsServer",
    [string]$AuthorityRoot = "C:\ProgramData\HERMES\authority\fouler",
    [string]$RuntimeStateRoot = "C:\ProgramData\HERMES\state\fouler",
    [string]$RuntimeLogRoot = "C:\ProgramData\HERMES\logs\fouler",
    [string]$RuntimeCacheRoot = "C:\ProgramData\HERMES\cache\fouler"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$CanonicalServiceName = "HERMES-FoulerObsServer"
$CanonicalRuntimeAccount = "JIGGLYPUFF\devstream-live"
$CanonicalServiceAccount = "NT SERVICE\HERMES-FoulerObsServer"
$CanonicalAuthorityRoot = "C:\ProgramData\HERMES\authority\fouler"
$CanonicalRuntimeStateRoot = "C:\ProgramData\HERMES\state\fouler"
$CanonicalRuntimeLogRoot = "C:\ProgramData\HERMES\logs\fouler"
$CanonicalRuntimeCacheRoot = "C:\ProgramData\HERMES\cache\fouler"
if ($Start) {
    throw "-Start is retired: OBS installation must leave HERMES-FoulerObsServer stopped for explicit activation"
}
foreach ($canonicalIdentity in @(
    [pscustomobject]@{ Supplied = $ServiceName; Expected = $CanonicalServiceName; Label = "ServiceName"; Comparison = [System.StringComparison]::Ordinal },
    [pscustomobject]@{ Supplied = $RuntimeAccount; Expected = $CanonicalRuntimeAccount; Label = "RuntimeAccount"; Comparison = [System.StringComparison]::OrdinalIgnoreCase },
    [pscustomobject]@{ Supplied = $ServiceAccount; Expected = $CanonicalServiceAccount; Label = "ServiceAccount"; Comparison = [System.StringComparison]::OrdinalIgnoreCase }
)) {
    if (-not [string]::Equals([string]$canonicalIdentity.Supplied, [string]$canonicalIdentity.Expected, $canonicalIdentity.Comparison)) {
        throw "$($canonicalIdentity.Label) must equal the canonical Fouler OBS identity: $($canonicalIdentity.Expected)"
    }
}
foreach ($canonicalPath in @(
    [pscustomobject]@{ Supplied = $AuthorityRoot; Expected = $CanonicalAuthorityRoot; Label = "AuthorityRoot" },
    [pscustomobject]@{ Supplied = $RuntimeStateRoot; Expected = $CanonicalRuntimeStateRoot; Label = "RuntimeStateRoot" },
    [pscustomobject]@{ Supplied = $RuntimeLogRoot; Expected = $CanonicalRuntimeLogRoot; Label = "RuntimeLogRoot" },
    [pscustomobject]@{ Supplied = $RuntimeCacheRoot; Expected = $CanonicalRuntimeCacheRoot; Label = "RuntimeCacheRoot" }
)) {
    $suppliedPath = [System.IO.Path]::GetFullPath([string]$canonicalPath.Supplied).TrimEnd("\")
    if (-not [string]::Equals($suppliedPath, [string]$canonicalPath.Expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$($canonicalPath.Label) must equal the canonical external Fouler path: $($canonicalPath.Expected)"
    }
}
$ServiceName = $CanonicalServiceName
$RuntimeAccount = $CanonicalRuntimeAccount
$ServiceAccount = $CanonicalServiceAccount
$AuthorityRoot = $CanonicalAuthorityRoot
$RuntimeStateRoot = $CanonicalRuntimeStateRoot
$RuntimeLogRoot = $CanonicalRuntimeLogRoot
$RuntimeCacheRoot = $CanonicalRuntimeCacheRoot
$ProjectDirWasExplicit = -not [string]::IsNullOrWhiteSpace($ProjectDir)
$ProjectDir = if ($ProjectDirWasExplicit) {
    [System.IO.Path]::GetFullPath($ProjectDir).TrimEnd("\")
} else {
    (Resolve-Path "$PSScriptRoot\..").Path.TrimEnd("\")
}
$BackupRoot = "C:\ProgramData\HERMES\backups\fouler-obs-server-service"
$StableNssm = "C:\ProgramData\HERMES-ObsServer\bin\nssm.exe"
$Entrypoint = Join-Path $ProjectDir "streaming\run_obs_server_service.py"
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
$RuntimeLeasePath = Join-Path $AuthorityRoot "runtime-lease.json"
$TrustStorePath = Join-Path $AuthorityRoot "controller-keys.json"
$PidFile = Join-Path $RuntimeStateRoot "pids\obs_server.pid"
$StdoutLog = Join-Path $RuntimeLogRoot "jigglypuff-obs-server.log"
$StderrLog = Join-Path $RuntimeLogRoot "jigglypuff-obs-server.err.log"
$RuntimeTempRoot = Join-Path $RuntimeStateRoot "tmp"
$DecisionTraceRoot = Join-Path $RuntimeLogRoot "decision_traces"
$ReleaseCommit = Split-Path -Leaf $ProjectDir
$ReleaseManifestPath = Join-Path $AuthorityRoot ("releases\$ReleaseCommit\bootstrap-manifest.json")
$BrokerActivationPath = Join-Path $AuthorityRoot ("broker-activations\$ReleaseCommit.json")
$LegacyTasks = @("HERMES-FoulerObsKeepAlive", "HERMES-FoulerObsServer")
$ObsServiceArguments = "-I -B -u `"$Entrypoint`""
$ObsServiceEnvironment = @(
    "FOULER_OBS_LIFECYCLE_OWNER=windows-service",
    "OBS_SERVER_HOST=127.0.0.1",
    "OBS_SERVER_PORT=8777",
    "PYTHONDONTWRITEBYTECODE=1",
    "PYTHONUTF8=1",
    "GIT_OPTIONAL_LOCKS=0",
    "FOULER_RUNTIME_LEASE_PATH=$RuntimeLeasePath",
    "FOULER_CONTROLLER_TRUST_STORE_PATH=$TrustStorePath",
    "FOULER_RUNTIME_STATE_ROOT=$RuntimeStateRoot",
    "FOULER_RUNTIME_LOG_ROOT=$RuntimeLogRoot",
    "FOULER_RUNTIME_CACHE_ROOT=$RuntimeCacheRoot",
    "FOULER_RUNTIME_TEMP_ROOT=$RuntimeTempRoot",
    "FOULER_LOG_DIR=$RuntimeLogRoot",
    "DECISION_TRACE_DIR=$DecisionTraceRoot",
    "TEMP=$RuntimeTempRoot",
    "TMP=$RuntimeTempRoot"
)

function Resolve-NssmSource {
    if ([string]::IsNullOrWhiteSpace($NssmSource) -or -not (Test-Path -LiteralPath $NssmSource -PathType Leaf)) {
        throw "Apply requires an explicit external -NssmSource; existing/PATH NSSM discovery is forbidden"
    }
    return [System.IO.Path]::GetFullPath($NssmSource)
}

function Invoke-Nssm {
    param([string[]]$Arguments)
    Assert-RegularSingleLinkFile -Path $StableNssm -Label "published NSSM"
    if ((Get-FileHash -LiteralPath $StableNssm -Algorithm SHA256).Hash.ToLowerInvariant() -ne $script:ExpectedNssmHash) {
        throw "published NSSM hash changed immediately before execution"
    }
    & $StableNssm @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "nssm.exe failed with exit code ${LASTEXITCODE}: $($Arguments -join ' ')"
    }
}

function Get-RegularFileSnapshot {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-NoReparsePathChain -Path $Path
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    Assert-RegularSingleLinkFile -Path $item.FullName -Label "snapshot source"
    $bytes = [IO.File]::ReadAllBytes($item.FullName)
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $digest = ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace("-", "").ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
    return @{ Path = $item.FullName; Bytes = $bytes; Sha256 = $digest }
}

function Write-AtomicBytes {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes, [Parameter(Mandatory = $true)][string]$Destination)
    $parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) { throw "atomic publication parent must already exist" }
    Assert-NoReparsePathChain -Path $parent
    $temporary = Join-Path $parent ("." + [IO.Path]::GetFileName($Destination) + "." + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllBytes($temporary, $Bytes)
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            [IO.File]::Replace($temporary, $Destination, $null, $true)
        }
        else {
            Move-Item -LiteralPath $temporary -Destination $Destination
        }
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

if (-not ("FoulerObsInstaller.NativeFileIdentity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace FoulerObsInstaller {
    [StructLayout(LayoutKind.Sequential)]
    public struct ByHandleFileInformation {
        public uint FileAttributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME CreationTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastAccessTime;
        public System.Runtime.InteropServices.ComTypes.FILETIME LastWriteTime;
        public uint VolumeSerialNumber;
        public uint FileSizeHigh;
        public uint FileSizeLow;
        public uint NumberOfLinks;
        public uint FileIndexHigh;
        public uint FileIndexLow;
    }
    public static class NativeFileIdentity {
        [DllImport("kernel32.dll", SetLastError = true)]
        public static extern bool GetFileInformationByHandle(SafeFileHandle handle, out ByHandleFileInformation information);
    }
}
'@
}

function Get-Sid {
    param([Parameter(Mandatory = $true)][string]$Account)
    return (New-Object System.Security.Principal.NTAccount($Account)).Translate([System.Security.Principal.SecurityIdentifier])
}

function Assert-RegularSingleLinkFile {
    param([string]$Path, [string]$Label)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "$Label must be a regular non-reparse-point file"
    }
    $stream = [System.IO.File]::Open($item.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
    try {
        $information = New-Object FoulerObsInstaller.ByHandleFileInformation
        if (-not [FoulerObsInstaller.NativeFileIdentity]::GetFileInformationByHandle($stream.SafeFileHandle, [ref]$information)) {
            throw "$Label file identity could not be read"
        }
        if ($information.NumberOfLinks -ne 1) { throw "$Label must have exactly one filesystem link" }
    }
    finally { $stream.Dispose() }
}

function Assert-NoReparsePathChain {
    param([string]$Path)
    $cursor = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) { throw "path ancestry contains a reparse point: $cursor" }
        }
        $parent = [System.IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName.TrimEnd("\")
    }
}

function Assert-NoPathOverlap {
    param([string]$First, [string]$Second, [string]$Label)
    $left = [System.IO.Path]::GetFullPath($First).TrimEnd("\")
    $right = [System.IO.Path]::GetFullPath($Second).TrimEnd("\")
    if ([string]::Equals($left, $right, [System.StringComparison]::OrdinalIgnoreCase) -or $left.StartsWith($right + "\", [System.StringComparison]::OrdinalIgnoreCase) -or $right.StartsWith($left + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Label must not equal, contain, or be contained by ProjectDir"
    }
}

function New-ExactSecurity {
    param([bool]$Directory, [hashtable]$RightsBySid)
    $security = if ($Directory) { New-Object System.Security.AccessControl.DirectorySecurity } else { New-Object System.Security.AccessControl.FileSecurity }
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($adminSid)
    $inheritance = if ($Directory) { [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit } else { [System.Security.AccessControl.InheritanceFlags]::None }
    foreach ($entry in $RightsBySid.GetEnumerator()) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier([string]$entry.Key)
        $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($sid, [System.Security.AccessControl.FileSystemRights]$entry.Value, $inheritance, [System.Security.AccessControl.PropagationFlags]::None, [System.Security.AccessControl.AccessControlType]::Allow)))
    }
    return $security
}

function Set-ExactDirectoryDacl {
    param([string]$Path, [hashtable]$RightsBySid)
    Assert-NoReparsePathChain -Path $Path
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Assert-NoReparsePathChain -Path $Path
    [System.IO.Directory]::SetAccessControl($Path, (New-ExactSecurity -Directory $true -RightsBySid $RightsBySid))
}

function Set-ExactFileDacl {
    param([string]$Path, [hashtable]$RightsBySid)
    Assert-RegularSingleLinkFile -Path $Path -Label "protected file"
    [System.IO.File]::SetAccessControl($Path, (New-ExactSecurity -Directory $false -RightsBySid $RightsBySid))
}

function Set-ExactDirectoryTreeDacl {
    param([string]$Path, [hashtable]$RightsBySid)
    Set-ExactDirectoryDacl -Path $Path -RightsBySid $RightsBySid
    $items = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)
    $reparse = @($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($reparse.Count -gt 0) { throw "protected directory tree contains a reparse point: $($reparse[0].FullName)" }
    foreach ($file in @($items | Where-Object { -not $_.PSIsContainer })) { Set-ExactFileDacl -Path $file.FullName -RightsBySid $RightsBySid }
    foreach ($directory in @($items | Where-Object { $_.PSIsContainer } | Sort-Object { $_.FullName.Length } -Descending)) { Set-ExactDirectoryDacl -Path $directory.FullName -RightsBySid $RightsBySid }
    Set-ExactDirectoryDacl -Path $Path -RightsBySid $RightsBySid
}

function Protect-AdminDirectory {
    param([Parameter(Mandatory = $true)][string]$Path, [string]$ReadAccount = "")
    $rights = @{
        "S-1-5-18" = [System.Security.AccessControl.FileSystemRights]::FullControl
        "S-1-5-32-544" = [System.Security.AccessControl.FileSystemRights]::FullControl
    }
    if (-not [string]::IsNullOrWhiteSpace($ReadAccount)) { $rights[(Get-Sid -Account $ReadAccount).Value] = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute }
    Set-ExactDirectoryTreeDacl -Path $Path -RightsBySid $rights
}

function Protect-AdminFile {
    param([Parameter(Mandatory = $true)][string]$Path, [string]$ReadAccount = "")
    $rights = @{
        "S-1-5-18" = [System.Security.AccessControl.FileSystemRights]::FullControl
        "S-1-5-32-544" = [System.Security.AccessControl.FileSystemRights]::FullControl
    }
    if (-not [string]::IsNullOrWhiteSpace($ReadAccount)) { $rights[(Get-Sid -Account $ReadAccount).Value] = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute }
    Set-ExactFileDacl -Path $Path -RightsBySid $rights
}

function Protect-RuntimeWriteDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $rights = @{
        "S-1-5-18" = [System.Security.AccessControl.FileSystemRights]::FullControl
        "S-1-5-32-544" = [System.Security.AccessControl.FileSystemRights]::FullControl
    }
    $rights[(Get-Sid -Account $RuntimeAccount).Value] = [System.Security.AccessControl.FileSystemRights]::Modify
    $rights[(Get-Sid -Account $ServiceAccount).Value] = [System.Security.AccessControl.FileSystemRights]::Modify
    Set-ExactDirectoryTreeDacl -Path $Path -RightsBySid $rights
}

function Assert-ManifestedImmutableRelease {
    if ($ProjectDir -notmatch '^D:\\Releases\\fouler-play\\[0-9a-f]{40}$') {
        throw "ProjectDir must be an immutable D:\Releases\fouler-play\<commit> release"
    }
    Assert-NoReparsePathChain -Path $ProjectDir
    Assert-RegularSingleLinkFile -Path $BrokerActivationPath -Label "broker activation receipt"
    $activation = Get-Content -LiteralPath $BrokerActivationPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$activation.schemaVersion -ne "fouler-lease-broker-activation/v1" -or $activation.registered -ne $true -or [string]$activation.sourceCommit -ne $ReleaseCommit) {
        throw "authority activation does not bind this immutable release"
    }
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$activation.projectDir).TrimEnd("\"), $ProjectDir.TrimEnd("\"), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "authority activation ProjectDir differs from this release"
    }
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$activation.releaseManifestPath), [System.IO.Path]::GetFullPath($ReleaseManifestPath), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "authority activation names a different release manifest"
    }
    Assert-RegularSingleLinkFile -Path $ReleaseManifestPath -Label "release bootstrap manifest"
    if ((Get-FileHash -LiteralPath $ReleaseManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$activation.releaseManifestSha256) {
        throw "release bootstrap manifest hash differs from authority activation"
    }
    $manifest = Get-Content -LiteralPath $ReleaseManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$manifest.schemaVersion -ne "fouler-bootstrap-manifest/v1" -or [string]$manifest.projectId -ne "fouler-play" -or [string]$manifest.sourceCommit -ne $ReleaseCommit -or -not $manifest.files) {
        throw "release bootstrap manifest identity is invalid"
    }
    $expected = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($property in $manifest.files.PSObject.Properties) {
        $relative = ([string]$property.Name).Replace("\", "/")
        $digest = ([string]$property.Value).ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($relative) -or [System.IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)\.\.?(?:/|$)' -or $digest -notmatch '^[0-9a-f]{64}$' -or $expected.ContainsKey($relative)) {
            throw "release bootstrap manifest contains an unsafe or duplicate file entry"
        }
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir ($relative.Replace("/", "\"))))
        if (-not $candidate.StartsWith($ProjectDir.TrimEnd("\") + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "release manifest entry escapes ProjectDir" }
        $expected.Add($relative, $digest)
    }
    foreach ($required in @(".venv/Scripts/python.exe", "streaming/run_obs_server_service.py")) {
        if (-not $expected.ContainsKey($required)) { throw "release bootstrap manifest omits required OBS file: $required" }
    }
    $releaseItems = @(Get-ChildItem -LiteralPath $ProjectDir -Recurse -Force -ErrorAction Stop)
    $releaseReparse = @($releaseItems | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($releaseReparse.Count -gt 0) { throw "release contains a reparse point: $($releaseReparse[0].FullName)" }
    $actualFiles = @($releaseItems | Where-Object { -not $_.PSIsContainer })
    if ($actualFiles.Count -ne $expected.Count) { throw "release file inventory no longer matches the bootstrap manifest" }
    foreach ($file in $actualFiles) {
        Assert-RegularSingleLinkFile -Path $file.FullName -Label "manifested release file"
        $relative = $file.FullName.Substring($ProjectDir.TrimEnd("\").Length + 1).Replace("\", "/")
        if (-not $expected.ContainsKey($relative)) { throw "release contains an unmanifested file: $relative" }
        if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected[$relative]) { throw "manifested release file hash changed: $relative" }
    }
    return $activation
}

function Get-ServiceRecord {
    Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
}

function Get-ServiceProcessId {
    $lines = @(& "$env:SystemRoot\System32\sc.exe" queryex $ServiceName 2>$null)
    $line = $lines | Where-Object { $_ -match "^\s*PID\s*:" } | Select-Object -First 1
    if ($line -and $line -match ":\s*(\d+)") { return [int]$matches[1] }
    return $null
}

function Split-WindowsCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return @() }
    $tokens = @()
    foreach ($match in [regex]::Matches($CommandLine, '(?:^|\s)(?:"(?<quoted>(?:[^"\\]|\\.)*)"|(?<bare>\S+))')) {
        $tokens += if ($match.Groups["quoted"].Success) { $match.Groups["quoted"].Value } else { $match.Groups["bare"].Value }
    }
    return $tokens
}

function Get-ProcessRecordById {
    param([int64]$ProcessId)
    if ($ProcessId -le 0) { return $null }
    return Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Test-ExactPath {
    param([string]$Actual, [string]$Expected)
    if ([string]::IsNullOrWhiteSpace($Actual)) { return $false }
    try {
        return [string]::Equals([System.IO.Path]::GetFullPath($Actual.Trim('"')), [System.IO.Path]::GetFullPath($Expected), [System.StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Test-ExactObsChildCommand {
    param($Process)
    if (-not $Process -or -not (Test-ExactPath -Actual ([string]$Process.ExecutablePath) -Expected $Python)) { return $false }
    $tokens = @(Split-WindowsCommandLine -CommandLine ([string]$Process.CommandLine))
    if ($tokens.Count -ne 5) { return $false }
    return (
        (Test-ExactPath -Actual $tokens[0] -Expected $Python) -and
        $tokens[1] -ceq "-I" -and
        $tokens[2] -ceq "-B" -and
        $tokens[3] -ceq "-u" -and
        (Test-ExactPath -Actual $tokens[4] -Expected $Entrypoint)
    )
}

function Assert-ObsBaseServiceIdentity {
    param([Parameter(Mandatory = $true)][ValidateSet("Disabled", "Manual", "Automatic")][string]$ExpectedStartMode)
    $serviceRecord = Get-CimInstance Win32_Service -Filter "Name = '$($ServiceName.Replace("'", "''"))'" -ErrorAction Stop | Select-Object -First 1
    $service = Get-Service -Name $ServiceName -ErrorAction Stop
    if (-not $serviceRecord) { throw "canonical OBS service is missing after publication" }
    if (-not (Test-ExactPath -Actual ([string]$serviceRecord.PathName) -Expected $StableNssm)) { throw "canonical OBS service ImagePath is not the pinned NSSM" }
    if (-not [string]::Equals([string]$serviceRecord.StartName, $ServiceAccount, [System.StringComparison]::OrdinalIgnoreCase)) { throw "canonical OBS service account identity changed" }
    if (-not [string]::Equals([string]$service.Status, "Stopped", [System.StringComparison]::OrdinalIgnoreCase)) { throw "canonical OBS service must remain stopped after installation" }
    if (-not [string]::Equals([string]$service.StartType, $ExpectedStartMode, [System.StringComparison]::OrdinalIgnoreCase)) { throw "canonical OBS service start mode differs from $ExpectedStartMode" }
    return $serviceRecord
}

function Assert-InstalledObsServiceIdentity {
    $null = Assert-ObsBaseServiceIdentity -ExpectedStartMode "Automatic"
    $parametersPath = "HKLM:\SYSTEM\CurrentControlSet\Services\$ServiceName\Parameters"
    $parameters = Get-ItemProperty -LiteralPath $parametersPath -ErrorAction Stop
    if (-not (Test-ExactPath -Actual ([string]$parameters.Application) -Expected $Python)) { throw "canonical OBS service Application is not the pinned release venv Python" }
    if ([string]$parameters.AppParameters -cne $ObsServiceArguments) { throw "canonical OBS service AppParameters changed" }
    if (-not (Test-ExactPath -Actual ([string]$parameters.AppDirectory) -Expected $ProjectDir)) { throw "canonical OBS service AppDirectory is not the immutable release" }
    if (-not (Test-ExactPath -Actual ([string]$parameters.AppStdout) -Expected $StdoutLog)) { throw "canonical OBS service stdout path changed" }
    if (-not (Test-ExactPath -Actual ([string]$parameters.AppStderr) -Expected $StderrLog)) { throw "canonical OBS service stderr path changed" }
    $actualEnvironment = @($parameters.AppEnvironmentExtra)
    if ($actualEnvironment.Count -ne $ObsServiceEnvironment.Count) { throw "canonical OBS service environment count changed" }
    for ($index = 0; $index -lt $ObsServiceEnvironment.Count; $index++) {
        if ([string]$actualEnvironment[$index] -cne [string]$ObsServiceEnvironment[$index]) { throw "canonical OBS service environment changed" }
    }
}

function Get-ObsProcessChain {
    $reasons = @()
    $serviceRecord = Get-CimInstance Win32_Service -Filter "Name = '$($ServiceName.Replace("'", "''"))'" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $serviceRecord) {
        return [pscustomobject]@{ verified = $false; reasons = @("service is missing"); servicePid = $null; childPid = $null; serviceCreation = $null; childCreation = $null }
    }
    if (-not (Test-ExactPath -Actual ([string]$serviceRecord.PathName) -Expected $StableNssm)) { $reasons += "SCM ImagePath is not the pinned NSSM" }
    if (-not [string]::Equals([string]$serviceRecord.StartName, $ServiceAccount, [System.StringComparison]::OrdinalIgnoreCase)) { $reasons += "SCM service account differs from the dedicated OBS identity" }
    $servicePid = [int64]$serviceRecord.ProcessId
    $serviceProcess = Get-ProcessRecordById -ProcessId $servicePid
    if (-not $serviceProcess -or -not (Test-ExactPath -Actual ([string]$serviceProcess.ExecutablePath) -Expected $StableNssm)) { $reasons += "SCM PID is not the pinned NSSM process" }
    $allChildren = if ($servicePid -gt 0) { @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $servicePid" -ErrorAction SilentlyContinue) } else { @() }
    $children = @($allChildren | Where-Object { Test-ExactObsChildCommand -Process $_ })
    if ($allChildren.Count -ne 1 -or $children.Count -ne 1) { $reasons += "SCM/NSSM does not own exactly one exact OBS Python child" }
    $child = if ($children.Count -eq 1) { $children[0] } else { $null }
    $pidPayload = $null
    if (Test-Path -LiteralPath $PidFile -PathType Leaf) {
        try { $pidPayload = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json } catch { $reasons += "OBS PID file is malformed" }
    } else { $reasons += "OBS PID file is missing" }
    if ($child -and $pidPayload) {
        if ([int64]$pidPayload.pid -ne [int64]$child.ProcessId) { $reasons += "OBS PID file does not name the exact SCM child" }
        try {
            $created = [DateTimeOffset]([DateTime]$child.CreationDate)
            $creationUnix = $created.ToUnixTimeMilliseconds() / 1000.0
            if ([Math]::Abs($creationUnix - [double]$pidPayload.started_at) -gt 5.0) { $reasons += "OBS PID creation time does not match the exact child" }
        } catch { $reasons += "OBS child creation time could not be reconciled" }
    }
    return [pscustomobject]@{
        verified = [bool]($reasons.Count -eq 0)
        reasons = $reasons
        servicePid = if ($serviceProcess) { [int64]$serviceProcess.ProcessId } else { $null }
        childPid = if ($child) { [int64]$child.ProcessId } else { $null }
        serviceCreation = if ($serviceProcess) { [string]$serviceProcess.CreationDate } else { $null }
        childCreation = if ($child) { [string]$child.CreationDate } else { $null }
        childCommand = if ($child) { [string]$child.CommandLine } else { $null }
    }
}

function Assert-NoAlternateObsProcesses {
    $alternate = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $commandLine = [string]$_.CommandLine
        -not [string]::IsNullOrWhiteSpace($commandLine) -and
        ($commandLine -match '(?i)(?:run_obs_server_service|serve_obs_page)\.py(?:"|\s|$)')
    })
    if ($alternate.Count -gt 0) {
        throw "mutable or alternate Fouler OBS process blocks canonical service mutation: $(@($alternate.ProcessId) -join ', ')"
    }
    $listeners = @(Get-LocalPortPids -Port 8777)
    if ($listeners.Count -gt 0) {
        throw "port 8777 is owned outside the stopped canonical OBS service: $($listeners -join ', ')"
    }
}

function Get-LocalPortPids {
    param([int]$Port = 8777)
    $pids = @()
    foreach ($line in @(& "$env:SystemRoot\System32\netstat.exe" -ano -p tcp 2>$null)) {
        if ($line -match "^\s*TCP\s+\S+:$Port\s+\S+\s+LISTENING\s+(\d+)\s*$") { $pids += [int64]$matches[1] }
    }
    return @($pids | Select-Object -Unique)
}

function Test-LocalPort {
    param([int]$Port = 8777)
    return @(Get-LocalPortPids -Port $Port).Count -gt 0
}

function Test-HealthEndpoint {
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 5 "http://127.0.0.1:8777/health"
        return [bool]($response.StatusCode -eq 200)
    } catch {
        return $false
    }
}

function Get-LegacyTaskStatus {
    param([string]$Name)
    $lines = @(& "$env:SystemRoot\System32\schtasks.exe" /Query /TN $Name /FO CSV /NH 2>$null)
    if ($LASTEXITCODE -ne 0) { return @{ name = $Name; present = $false; state = "missing" } }
    $line = $lines | Where-Object { $_ } | Select-Object -First 1
    $row = $line | ConvertFrom-Csv -Header TaskName,NextRunTime,Status
    return @{ name = $Name; present = $true; state = [string]$row.Status }
}

function Disable-LegacyObsTasks {
    foreach ($name in $LegacyTasks) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if (-not $task) { continue }
        Disable-ScheduledTask -TaskName $name -ErrorAction Stop | Out-Null
        Stop-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    }
}

function Assert-LegacyObsTasksDisabled {
    foreach ($name in $LegacyTasks) {
        $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
        if ($task -and [string]$task.State -ne "Disabled") {
            throw "retired alternate OBS task remains enabled: $name"
        }
    }
}

function Get-ObsServiceStatus {
    $service = Get-ServiceRecord
    $chain = if ($service -and [string]$service.Status -eq "Running") { Get-ObsProcessChain } else { [pscustomobject]@{ verified = $false; reasons = @("service is not running"); servicePid = $null; childPid = $null; serviceCreation = $null; childCreation = $null; childCommand = $null } }
    $portPids = @(Get-LocalPortPids)
    $portOpen = [bool]($portPids.Count -eq 1 -and $chain.childPid -and [int64]$portPids[0] -eq [int64]$chain.childPid)
    $healthOk = if ($SkipHttpProbe) { $null } elseif ($portOpen) { Test-HealthEndpoint } else { $false }
    $serviceRunning = [bool]($service -and [string]$service.Status -eq "Running")
    [pscustomobject]@{
        lifecycleOwner = "windows-service"
        serviceName = $ServiceName
        serviceAccount = $ServiceAccount
        servicePresent = [bool]$service
        serviceState = if ($service) { [string]$service.Status } else { "missing" }
        serviceStartType = if ($service) { [string]$service.StartType } else { $null }
        serviceProcessId = if ($service) { Get-ServiceProcessId } else { $null }
        nssm = $StableNssm
        projectDir = $ProjectDir
        python = $Python
        entrypoint = $Entrypoint
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        runtimeLease = $RuntimeLeasePath
        controllerTrustStore = $TrustStorePath
        runtimeStateRoot = $RuntimeStateRoot
        managedPid = $chain.childPid
        processCount = if ($chain.childPid) { 1 } else { 0 }
        processChainVerified = [bool]$chain.verified
        processChainBlockers = @($chain.reasons)
        serviceCreation = $chain.serviceCreation
        childCreation = $chain.childCreation
        childCommand = $chain.childCommand
        port8777ListenerPids = $portPids
        port8777Listening = [bool]$portOpen
        healthEndpointOk = if ($SkipHttpProbe) { $null } else { [bool]$healthOk }
        healthProbeSkipped = [bool]$SkipHttpProbe
        lifecycleHealthy = [bool]($serviceRunning -and $chain.verified -and $portOpen -and ($SkipHttpProbe -or $healthOk))
        legacyTaskProbeSkipped = [bool]$SkipHttpProbe
        legacyTasks = if ($SkipHttpProbe) { @() } else { @($LegacyTasks | ForEach-Object { Get-LegacyTaskStatus -Name $_ }) }
        rollback = "Stop only $ServiceName and restore the protected service backup if required; retired OBS tasks remain disabled"
    }
}

function Invoke-TrustedNssmCapture {
    param([string]$Path, [string[]]$Arguments, [string]$Destination)
    Assert-RegularSingleLinkFile -Path $Path -Label "rollback NSSM tool"
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $script:ExpectedNssmHash) {
        throw "rollback NSSM tool failed its hash pin immediately before execution"
    }
    @(& $Path @Arguments 2>&1) | Set-Content -LiteralPath $Destination -Encoding UTF8
    return $LASTEXITCODE
}

function Save-RollbackBackup {
    param([Parameter(Mandatory = $true)][string]$TrustedNssm)
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ") + "-" + [guid]::NewGuid().ToString("N")
    Protect-AdminDirectory -Path $BackupRoot
    $path = Join-Path $BackupRoot $stamp
    Protect-AdminDirectory -Path $path
    if (Test-Path -LiteralPath $StableNssm -PathType Leaf) {
        $stableSnapshot = Get-RegularFileSnapshot -Path $StableNssm
        [IO.File]::WriteAllBytes((Join-Path $path "nssm.exe"), $stableSnapshot.Bytes)
        Protect-AdminFile -Path (Join-Path $path "nssm.exe")
        if ((Get-FileHash -LiteralPath (Join-Path $path "nssm.exe") -Algorithm SHA256).Hash.ToLowerInvariant() -ne $stableSnapshot.Sha256) {
            throw "NSSM rollback backup differs from its pre-mutation snapshot"
        }
        $stableSnapshot.Sha256 | Set-Content -LiteralPath (Join-Path $path "nssm.exe.sha256") -Encoding ASCII
        & "$env:SystemRoot\System32\icacls.exe" $StableNssm /save (Join-Path $path "nssm-acl.txt") /c | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to back up NSSM ACL" }
    }
    $service = Get-ServiceRecord
    if ($service) {
        @(& "$env:SystemRoot\System32\sc.exe" qc $ServiceName) | Set-Content -LiteralPath (Join-Path $path "$ServiceName-sc-qc.txt") -Encoding UTF8
        $dumpPath = Join-Path $path "$ServiceName-nssm-dump.txt"
        $dumpCode = Invoke-TrustedNssmCapture -Path $TrustedNssm -Arguments @("dump", $ServiceName) -Destination $dumpPath
        if ($dumpCode -ne 0) {
            "Trusted NSSM could not dump this service; registry and SCM backups remain authoritative." | Set-Content -LiteralPath (Join-Path $path "$ServiceName-nssm-dump.failed.txt") -Encoding UTF8
        }
        & "$env:SystemRoot\System32\reg.exe" export "HKLM\SYSTEM\CurrentControlSet\Services\$ServiceName" (Join-Path $path "$ServiceName.reg") /y | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to export the canonical OBS service registry backup" }
    } else {
        "No existing service named $ServiceName." | Set-Content -LiteralPath (Join-Path $path "$ServiceName.none.txt") -Encoding UTF8
    }
    foreach ($name in $LegacyTasks) {
        $xml = @(& "$env:SystemRoot\System32\schtasks.exe" /Query /TN $name /XML 2>$null)
        if ($LASTEXITCODE -eq 0) {
            [IO.File]::WriteAllLines((Join-Path $path "$name.xml"), [string[]]$xml, [Text.UTF8Encoding]::new($false))
        }
    }
    return $path
}

function Stop-ManagedProcess {
    $service = Get-ServiceRecord
    $chain = if ($service -and [string]$service.Status -eq "Running") { Get-ObsProcessChain } else { $null }
    if ($service -and [string]$service.Status -ne "Stopped") {
        Stop-Service -Name $ServiceName -Force
        (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 30))
    }
    if ($chain -and $chain.verified -and $chain.childPid) {
        $survivor = Get-ProcessRecordById -ProcessId ([int64]$chain.childPid)
        if ($survivor -and [string]$survivor.CreationDate -eq [string]$chain.childCreation -and (Test-ExactObsChildCommand -Process $survivor)) {
            & "$env:SystemRoot\System32\taskkill.exe" /PID ([int64]$chain.childPid) /T /F 2>$null | Out-Null
            Start-Sleep -Seconds 2
        }
    }
    if ((Get-ServiceRecord) -and [string](Get-ServiceRecord).Status -ne "Stopped") { throw "OBS service did not stop" }
    if (@(Get-LocalPortPids).Count -gt 0) { throw "port 8777 still has a listener after stopping the OBS service" }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

if ($Status) {
    Get-ObsServiceStatus | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $Apply) {
    [pscustomobject]@{
        dryRun = $true
        serviceName = $ServiceName
        application = $Python
        arguments = $ObsServiceArguments
        workingDirectory = $ProjectDir
        stableNssm = $StableNssm
        serviceAccount = $ServiceAccount
        expectedNssmSha256RequiredForApply = $true
        provisionIdentityOnly = [bool]$ProvisionIdentityOnly
        disableLegacyTasks = $true
        legacyTaskRetirementMandatory = $true
        startsProcesses = $false
        startTypeAfterInstall = "Automatic"
        rollback = "Restore only protected canonical service/NSSM state; retired task XML remains backup evidence and is never re-enabled."
    } | ConvertTo-Json -Depth 4
    exit 0
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Apply requires an elevated administrator PowerShell session"
}

$script:ExpectedNssmHash = $ExpectedNssmSha256.Trim().ToLowerInvariant()
if ($script:ExpectedNssmHash -notmatch '^[0-9a-f]{64}$') {
    throw "-ExpectedNssmSha256 must be the independently verified 64-character SHA-256 of nssm.exe"
}
if (-not [string]::Equals($ServiceAccount, "NT SERVICE\$ServiceName", [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "ServiceAccount must be the virtual service identity for ServiceName"
}
if (-not $ProjectDirWasExplicit) {
    throw "Apply requires an explicit target -ProjectDir from a trusted installer copy"
}
$installerPath = [System.IO.Path]::GetFullPath([string]$MyInvocation.MyCommand.Path)
if ($installerPath -match '^D:\\Releases\\fouler-play\\') { throw "OBS installer must execute from the trusted control plane, never a release tree" }
Assert-NoReparsePathChain -Path $installerPath
Assert-RegularSingleLinkFile -Path $installerPath -Label "OBS installer"
Assert-NoPathOverlap -First $ProjectDir -Second $installerPath -Label "OBS installer path"
foreach ($externalPath in @($AuthorityRoot, $RuntimeStateRoot, $RuntimeLogRoot, $RuntimeCacheRoot, $RuntimeTempRoot, $BackupRoot, (Split-Path -Parent $StableNssm), $NssmSource)) {
    Assert-NoPathOverlap -First $ProjectDir -Second $externalPath -Label "OBS runtime/publication path"
}
if ($ProjectDir -notmatch '^D:\\Releases\\fouler-play\\[0-9a-f]{40}$') {
    throw "ProjectDir must be an immutable D:\Releases\fouler-play\<commit> release"
}
Assert-NoReparsePathChain -Path $ProjectDir
$authorityActivation = $null
if (-not $ProvisionIdentityOnly -and -not $Stop -and -not $Uninstall) {
    $authorityActivation = Assert-ManifestedImmutableRelease
}
$sourceNssm = Resolve-NssmSource
$sourceSnapshot = Get-RegularFileSnapshot -Path $sourceNssm
if ($sourceSnapshot.Sha256 -ne $script:ExpectedNssmHash) {
    throw "nssm.exe does not match the independently verified SHA-256"
}
Protect-AdminDirectory -Path $BackupRoot
$trustedToolRoot = Join-Path $BackupRoot ("bootstrap-" + [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ"))
Protect-AdminDirectory -Path $trustedToolRoot
$trustedRollbackNssm = Join-Path $trustedToolRoot "nssm.rollback-tool.exe"
Write-AtomicBytes -Bytes $sourceSnapshot.Bytes -Destination $trustedRollbackNssm
Protect-AdminFile -Path $trustedRollbackNssm
if ((Get-FileHash -LiteralPath $trustedRollbackNssm -Algorithm SHA256).Hash.ToLowerInvariant() -ne $script:ExpectedNssmHash) { throw "trusted rollback NSSM publication failed its hash pin" }
$serviceBeforeMutation = Get-ServiceRecord
$serviceExistedBefore = [bool]$serviceBeforeMutation
$serviceWasRunningBefore = [bool]($serviceBeforeMutation -and [string]$serviceBeforeMutation.Status -eq "Running")
$stableNssmExistedBefore = Test-Path -LiteralPath $StableNssm -PathType Leaf
$backup = Save-RollbackBackup -TrustedNssm $trustedRollbackNssm
$rollbackManifestPath = Join-Path $backup "rollback.json"
$rollbackManifest = [ordered]@{
    schemaVersion = "fouler-obs-service-rollback/v1"
    serviceName = $ServiceName
    serviceExisted = $serviceExistedBefore
    serviceWasRunning = $serviceWasRunningBefore
    stableNssmExisted = [bool]$stableNssmExistedBefore
    serviceRegistryBackup = if ($serviceExistedBefore) { Join-Path $backup "$ServiceName.reg" } else { $null }
    nssmBackup = if ($stableNssmExistedBefore) { Join-Path $backup "nssm.exe" } else { $null }
    legacyTaskBackups = @($LegacyTasks | ForEach-Object { Join-Path $backup "$_.xml" })
    legacyTasksRemainRetired = $true
    startsProcesses = $false
}
$rollbackManifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $rollbackManifestPath -Encoding UTF8
Protect-AdminFile -Path $rollbackManifestPath

$service = Get-ServiceRecord
$wasRunning = [bool]($service -and [string]$service.Status -eq "Running")
if ($service -and [string]$service.Status -ne "Stopped") { Stop-ManagedProcess }
Assert-NoAlternateObsProcesses
Disable-LegacyObsTasks
Assert-LegacyObsTasksDisabled
if ($Stop -and -not $Uninstall) {
    Get-ObsServiceStatus | Add-Member -NotePropertyName backup -NotePropertyValue $backup -PassThru | ConvertTo-Json -Depth 8
    exit 0
}
if ($Uninstall) {
    if (Get-ServiceRecord) {
        & "$env:SystemRoot\System32\sc.exe" delete $ServiceName | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to delete the stopped OBS service" }
    }
    Get-ObsServiceStatus | Add-Member -NotePropertyName backup -NotePropertyValue $backup -PassThru | ConvertTo-Json -Depth 8
    exit 0
}

try {
    Protect-AdminDirectory -Path (Split-Path -Parent $StableNssm)
    Write-AtomicBytes -Bytes $sourceSnapshot.Bytes -Destination $StableNssm
    Protect-AdminFile -Path $StableNssm
    if ((Get-FileHash -LiteralPath $StableNssm -Algorithm SHA256).Hash.ToLowerInvariant() -ne $script:ExpectedNssmHash) { throw "stable nssm.exe failed post-publication SHA-256 verification" }

    $sc = "$env:SystemRoot\System32\sc.exe"
    if (-not (Get-ServiceRecord)) {
        & $sc create $ServiceName "binPath=" "`"$StableNssm`"" "start=" "disabled" "DisplayName=" "HERMES Fouler OBS Server" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to create the OBS service in Disabled state" }
    } else {
        & $sc config $ServiceName "binPath=" "`"$StableNssm`"" "start=" "disabled" | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to force the OBS service into Disabled state before reconfiguration" }
    }
    & $sc sidtype $ServiceName unrestricted | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to enable the OBS service SID" }
    & $sc config $ServiceName "obj=" $ServiceAccount "password=" "" | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to configure the OBS virtual service account" }

    Protect-AdminDirectory -Path (Split-Path -Parent $StableNssm) -ReadAccount $ServiceAccount
    Protect-AdminFile -Path $StableNssm -ReadAccount $ServiceAccount
    $null = Assert-ObsBaseServiceIdentity -ExpectedStartMode "Disabled"
    if ($ProvisionIdentityOnly) {
        $identityService = Get-Service -Name $ServiceName
        if ([string]$identityService.Status -ne "Stopped" -or [string]$identityService.StartType -ne "Disabled") {
            throw "OBS identity provisioning must leave the service stopped and Disabled"
        }
        [pscustomobject]@{
            schemaVersion = "fouler-obs-service-identity/v1"
            status = "identity-provisioned-disabled"
            serviceName = $ServiceName
            serviceAccount = $ServiceAccount
            serviceState = [string]$identityService.Status
            serviceStartType = [string]$identityService.StartType
            nssmSha256 = (Get-FileHash -LiteralPath $StableNssm -Algorithm SHA256).Hash.ToLowerInvariant()
            executesReleaseCode = $false
            authorityRequiredForFullInstall = $true
            backup = $backup
        } | ConvertTo-Json -Depth 6
        exit 0
    }
    Protect-RuntimeWriteDirectory -Path $RuntimeStateRoot
    Protect-RuntimeWriteDirectory -Path $RuntimeLogRoot
    Protect-RuntimeWriteDirectory -Path $RuntimeCacheRoot
    Protect-RuntimeWriteDirectory -Path $RuntimeTempRoot
    Protect-RuntimeWriteDirectory -Path (Split-Path -Parent $PidFile)
    Protect-RuntimeWriteDirectory -Path $DecisionTraceRoot

    Invoke-Nssm -Arguments @("set", $ServiceName, "Application", $Python)
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppParameters", $ObsServiceArguments)
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppDirectory", $ProjectDir)
    Invoke-Nssm -Arguments @("set", $ServiceName, "DisplayName", "HERMES Fouler OBS Server")
    Invoke-Nssm -Arguments @("set", $ServiceName, "Description", "HERMES-managed Fouler Play OBS surface on loopback 127.0.0.1:8777")
    Invoke-Nssm -Arguments @("set", $ServiceName, "ObjectName", $ServiceAccount)
    Invoke-Nssm -Arguments @("set", $ServiceName, "Start", "SERVICE_DISABLED")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppExit", "Default", "Restart")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppExit", "2", "Exit")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppRestartDelay", "5000")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppThrottle", "1500")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppNoConsole", "1")
    Invoke-Nssm -Arguments (@("set", $ServiceName, "AppEnvironmentExtra") + $ObsServiceEnvironment)
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppStdout", $StdoutLog)
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppStderr", $StderrLog)
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateFiles", "1")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateOnline", "1")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateSeconds", "86400")
    Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateBytes", "10485760")

    if (-not (Test-Path -LiteralPath $RuntimeLeasePath -PathType Leaf) -or -not (Test-Path -LiteralPath $TrustStorePath -PathType Leaf)) { throw "protected runtime lease and controller trust store are required before publishing OBS" }
    Invoke-Nssm -Arguments @("set", $ServiceName, "Start", "SERVICE_AUTO_START")
    $publishedService = Get-Service -Name $ServiceName
    if ([string]$publishedService.Status -ne "Stopped" -or [string]$publishedService.StartType -ne "Automatic") {
        throw "OBS installation must leave the canonical service stopped and Automatic without activating it"
    }
    Assert-InstalledObsServiceIdentity
}
catch {
    $transactionFailure = $_
    $rollbackFailures = New-Object 'System.Collections.Generic.List[string]'
    try { Stop-ManagedProcess } catch { $rollbackFailures.Add("service stop failed: $($_.Exception.Message)") }
    if (-not $serviceExistedBefore -and (Get-ServiceRecord)) {
        try {
            & "$env:SystemRoot\System32\sc.exe" delete $ServiceName | Out-Null
            if ($LASTEXITCODE -ne 0) { throw "sc.exe delete returned $LASTEXITCODE" }
        }
        catch { $rollbackFailures.Add("new service rollback failed: $($_.Exception.Message)") }
    }
    try {
        $previousNssmPath = Join-Path $backup "nssm.exe"
        if ($stableNssmExistedBefore) {
            $previousNssm = Get-RegularFileSnapshot -Path $previousNssmPath
            Write-AtomicBytes -Bytes $previousNssm.Bytes -Destination $StableNssm
            Protect-AdminFile -Path $StableNssm -ReadAccount $ServiceAccount
            if ((Get-FileHash -LiteralPath $StableNssm -Algorithm SHA256).Hash.ToLowerInvariant() -ne $previousNssm.Sha256) { throw "restored NSSM hash differs from backup" }
        }
        elseif (Test-Path -LiteralPath $StableNssm -PathType Leaf) {
            Remove-Item -LiteralPath $StableNssm -Force -ErrorAction Stop
        }
    }
    catch { $rollbackFailures.Add("NSSM rollback failed: $($_.Exception.Message)") }
    $rollbackSuffix = if ($rollbackFailures.Count -gt 0) { " Automatic rollback errors: $($rollbackFailures -join '; ')." } else { " Automatic file/new-service rollback completed." }
    throw "OBS service transaction failed closed; protected rollback backup: $backup.$rollbackSuffix $($transactionFailure.Exception.Message)"
}

$statusPayload = Get-ObsServiceStatus
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
$statusPayload | Add-Member -NotePropertyName authorityActivation -NotePropertyValue $BrokerActivationPath
$statusPayload | Add-Member -NotePropertyName nssmSha256 -NotePropertyValue (Get-FileHash -Algorithm SHA256 -LiteralPath $StableNssm).Hash
$statusPayload | Add-Member -NotePropertyName previouslyRunning -NotePropertyValue $wasRunning
$statusPayload | Add-Member -NotePropertyName startsProcesses -NotePropertyValue $false
$statusPayload | ConvertTo-Json -Depth 8
