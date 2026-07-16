[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectDir,
    [Parameter(Mandatory = $true)]
    [string]$KeyringSource,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedKeyringSha256,
    [Parameter(Mandatory = $true)]
    [string]$ReleaseManifestSource,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedReleaseManifestSha256,
    [Parameter(Mandatory = $true)]
    [string]$AccountSeasonSource,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedAccountSeasonSha256,
    [Parameter(Mandatory = $true)]
    [string]$LeaseSource,
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 1000)]
    [int]$RunCount,
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 100)]
    [int]$MaxCycles,
    [Parameter(Mandatory = $true)]
    [ValidateRange(1, 3)]
    [int]$MaxConcurrentBattles,
    [Parameter(Mandatory = $true)]
    [string]$Account,
    [ValidateSet("always", "never")]
    [string]$ReplayBehavior = "always",
    [string]$RuntimeAccount = "JIGGLYPUFF\devstream-live",
    [string]$BrokerAccount = "NT SERVICE\HERMES-FoulerLeaseBroker",
    [string]$ObserverAccount = "NT SERVICE\HERMES-FoulerObsServer",
    [string]$AuthorityRoot = "C:\ProgramData\HERMES\authority\fouler",
    [string]$BackupRoot = "C:\ProgramData\HERMES\backups\fouler-authority",
    [string]$BrokerRoot = "C:\ProgramData\HERMES-LeaseBroker\fouler",
    [string]$BrokerServiceName = "HERMES-FoulerLeaseBroker",
    [switch]$OwnerRotateAccountSeason,
    [switch]$ImproveAuthorized,
    [switch]$Apply
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $sha256 = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($sha256.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $sha256.Dispose()
    }
}

function Get-RegularFileSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label,
        [int64]$MaxBytes = 1048576
    )
    $resolved = [System.IO.Path]::GetFullPath($Path)
    Assert-NoReparsePathChain -Path $resolved
    $item = Get-Item -LiteralPath $resolved -Force
    Assert-RegularSingleLinkFile -Path $resolved -Label $Label
    if ($item.Length -gt $MaxBytes) {
        throw "$Label exceeds the permitted size"
    }
    # This is the only source-file read. Every later stage uses the captured bytes.
    [byte[]]$bytes = [System.IO.File]::ReadAllBytes($resolved)
    if ($bytes.LongLength -gt $MaxBytes) {
        throw "$Label exceeds the permitted size"
    }
    return [pscustomobject]@{
        Path = $resolved
        Bytes = $bytes
        Sha256 = Get-BytesSha256 -Bytes $bytes
    }
}

function Normalize-FoulerAccount {
    param([Parameter(Mandatory = $true)][string]$Value)
    return [regex]::Replace($Value.Trim().ToLowerInvariant(), '[^a-z0-9]', '')
}

function ConvertFrom-StrictAccountSeasonSnapshot {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$ExpectedAccount
    )
    if ($Bytes.LongLength -gt 65536) {
        throw "account-season authority exceeds 64 KiB"
    }
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $text = $utf8.GetString($Bytes)
    $jsonString = '"(?:\\["\\/bfnrt]|\\u[0-9A-Fa-f]{4}|[^"\\\x00-\x1F])*"'
    $pattern = '\A\s*\{\s*(?:(?<key>' + $jsonString + ')\s*:\s*(?<value>' + $jsonString + ')(?:\s*,\s*(?<key>' + $jsonString + ')\s*:\s*(?<value>' + $jsonString + '))*)?\s*\}\s*\z'
    $match = [regex]::Match($text, $pattern, [System.Text.RegularExpressions.RegexOptions]::CultureInvariant)
    if (-not $match.Success) {
        throw "account-season authority must be a JSON object containing only string properties"
    }
    $properties = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::Ordinal)
    for ($index = 0; $index -lt $match.Groups["key"].Captures.Count; $index++) {
        $key = [string]($match.Groups["key"].Captures[$index].Value | ConvertFrom-Json)
        $value = [string]($match.Groups["value"].Captures[$index].Value | ConvertFrom-Json)
        if ($properties.ContainsKey($key)) {
            throw "account-season authority contains a duplicate JSON key: $key"
        }
        $properties.Add($key, $value)
    }
    $requiredKeys = @("schemaVersion", "account", "seasonId")
    if ($properties.Count -ne $requiredKeys.Count -or @($requiredKeys | Where-Object { -not $properties.ContainsKey($_) }).Count -ne 0) {
        throw "account-season authority must contain exactly schemaVersion, account, and seasonId"
    }
    if ($properties["schemaVersion"] -ne "fouler-play-account-season/v1") {
        throw "account-season authority schemaVersion must equal fouler-play-account-season/v1"
    }
    $normalizedExpected = Normalize-FoulerAccount -Value $ExpectedAccount
    $normalizedAccount = Normalize-FoulerAccount -Value $properties["account"]
    if ([string]::IsNullOrWhiteSpace($normalizedExpected) -or [string]::IsNullOrWhiteSpace($normalizedAccount) -or $normalizedAccount -ne $normalizedExpected) {
        throw "account-season authority account does not exactly match the normalized lease/account parameter"
    }
    if ([string]::IsNullOrWhiteSpace($properties["seasonId"])) {
        throw "account-season authority seasonId must be nonempty"
    }
    return [pscustomobject]@{
        schemaVersion = $properties["schemaVersion"]
        account = $properties["account"].Trim()
        normalizedAccount = $normalizedAccount
        seasonId = $properties["seasonId"].Trim()
    }
}

if (-not ("FoulerAuthority.NativeFileIdentity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace FoulerAuthority {
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
        public static extern bool GetFileInformationByHandle(
            SafeFileHandle handle,
            out ByHandleFileInformation information
        );
    }
}
'@
}

function Get-Sid {
    param([Parameter(Mandatory = $true)][string]$Account)
    return (New-Object System.Security.Principal.NTAccount($Account)).Translate(
        [System.Security.Principal.SecurityIdentifier]
    )
}

function Assert-RegularSingleLinkFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "$Label must be a regular, non-reparse-point file"
    }
    $stream = [System.IO.File]::Open(
        $item.FullName,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
    try {
        $information = New-Object FoulerAuthority.ByHandleFileInformation
        if (-not [FoulerAuthority.NativeFileIdentity]::GetFileInformationByHandle($stream.SafeFileHandle, [ref]$information)) {
            throw "$Label file identity could not be read"
        }
        if ($information.NumberOfLinks -ne 1) {
            throw "$Label must have exactly one filesystem link"
        }
    }
    finally {
        $stream.Dispose()
    }
}

function Assert-NoReparsePathChain {
    param([Parameter(Mandatory = $true)][string]$Path)
    $cursor = [System.IO.Path]::GetFullPath($Path).TrimEnd("\")
    while (-not [string]::IsNullOrWhiteSpace($cursor)) {
        if (Test-Path -LiteralPath $cursor) {
            $item = Get-Item -LiteralPath $cursor -Force -ErrorAction Stop
            if (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw "path ancestry contains a reparse point: $cursor"
            }
        }
        $parent = [System.IO.Directory]::GetParent($cursor)
        if ($null -eq $parent) { break }
        $cursor = $parent.FullName.TrimEnd("\")
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
        throw "$Label must not equal, contain, or be contained by ProjectDir"
    }
}

function New-ExactSecurity {
    param(
        [Parameter(Mandatory = $true)][bool]$Directory,
        [Parameter(Mandatory = $true)][hashtable]$RightsBySid
    )
    $security = if ($Directory) {
        New-Object System.Security.AccessControl.DirectorySecurity
    } else {
        New-Object System.Security.AccessControl.FileSecurity
    }
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($adminSid)
    $inheritance = if ($Directory) {
        [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    } else {
        [System.Security.AccessControl.InheritanceFlags]::None
    }
    foreach ($entry in $RightsBySid.GetEnumerator()) {
        $sid = New-Object System.Security.Principal.SecurityIdentifier([string]$entry.Key)
        $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]$entry.Value,
            $inheritance,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )
        $security.AddAccessRule($rule)
    }
    return $security
}

function Set-ExactDirectoryDacl {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$RightsBySid
    )
    Assert-NoReparsePathChain -Path $Path
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Assert-NoReparsePathChain -Path $Path
    [System.IO.Directory]::SetAccessControl($Path, (New-ExactSecurity -Directory $true -RightsBySid $RightsBySid))
}

function Set-ExactFileDacl {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][hashtable]$RightsBySid
    )
    Assert-RegularSingleLinkFile -Path $Path -Label "protected file"
    [System.IO.File]::SetAccessControl($Path, (New-ExactSecurity -Directory $false -RightsBySid $RightsBySid))
}

$script:SystemSid = "S-1-5-18"
$script:AdminSid = "S-1-5-32-544"
$script:RuntimeSid = (Get-Sid -Account $RuntimeAccount).Value
$script:BrokerSid = (Get-Sid -Account $BrokerAccount).Value
$script:ObserverSid = (Get-Sid -Account $ObserverAccount).Value
$script:AdminRights = @{
    $script:SystemSid = [System.Security.AccessControl.FileSystemRights]::FullControl
    $script:AdminSid = [System.Security.AccessControl.FileSystemRights]::FullControl
}
$script:AuthorityReadRights = @{
    $script:SystemSid = [System.Security.AccessControl.FileSystemRights]::FullControl
    $script:AdminSid = [System.Security.AccessControl.FileSystemRights]::FullControl
    $script:RuntimeSid = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
    $script:BrokerSid = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
    $script:ObserverSid = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
}
$script:AccountSeasonRights = @{
    $script:SystemSid = [System.Security.AccessControl.FileSystemRights]::FullControl
    $script:AdminSid = [System.Security.AccessControl.FileSystemRights]::FullControl
    $script:RuntimeSid = [System.Security.AccessControl.FileSystemRights]::Read
    $script:ObserverSid = [System.Security.AccessControl.FileSystemRights]::Read
}

function Protect-Directory {
    param([Parameter(Mandatory = $true)][string]$Path, [switch]$RuntimeRead)
    $rights = if ($RuntimeRead) { $script:AuthorityReadRights } else { $script:AdminRights }
    Set-ExactDirectoryDacl -Path $Path -RightsBySid $rights
    $items = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)
    $reparse = @($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($reparse.Count -gt 0) { throw "protected directory tree contains a reparse point: $($reparse[0].FullName)" }
    foreach ($file in @($items | Where-Object { -not $_.PSIsContainer })) { Set-ExactFileDacl -Path $file.FullName -RightsBySid $rights }
    foreach ($directory in @($items | Where-Object { $_.PSIsContainer } | Sort-Object { $_.FullName.Length } -Descending)) { Set-ExactDirectoryDacl -Path $directory.FullName -RightsBySid $rights }
    Set-ExactDirectoryDacl -Path $Path -RightsBySid $rights
}

function Protect-File {
    param([Parameter(Mandatory = $true)][string]$Path)
    Set-ExactFileDacl -Path $Path -RightsBySid $script:AuthorityReadRights
}

function Protect-AdminFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    Set-ExactFileDacl -Path $Path -RightsBySid $script:AdminRights
}

function Protect-AccountSeasonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    Set-ExactFileDacl -Path $Path -RightsBySid $script:AccountSeasonRights
    $attributes = [System.IO.File]::GetAttributes($Path)
    [System.IO.File]::SetAttributes($Path, ($attributes -bor [System.IO.FileAttributes]::ReadOnly))
}

function Clear-AccountSeasonReadOnlyAttribute {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Assert-RegularSingleLinkFile -Path $Path -Label "installed account-season authority"
        $attributes = [System.IO.File]::GetAttributes($Path)
        [System.IO.File]::SetAttributes($Path, ($attributes -band (-bnot [System.IO.FileAttributes]::ReadOnly)))
    }
}

function Write-ExactAtomic {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Content,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    if (Test-Path -LiteralPath $Destination) {
        throw "refusing to overwrite immutable authority file: $Destination"
    }
    $temporary = Join-Path (Split-Path -Parent $Destination) ("." + [System.IO.Path]::GetFileName($Destination) + "." + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [System.IO.File]::WriteAllBytes($temporary, $Content)
        Move-Item -LiteralPath $temporary -Destination $Destination
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Write-ReplaceAtomic {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Content,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $parent = Split-Path -Parent $Destination
    $name = [System.IO.Path]::GetFileName($Destination)
    $temporary = Join-Path $parent ("." + $name + "." + [guid]::NewGuid().ToString("N") + ".tmp")
    $replacementBackup = Join-Path $parent ("." + $name + "." + [guid]::NewGuid().ToString("N") + ".replace.bak")
    try {
        [System.IO.File]::WriteAllBytes($temporary, $Content)
        if (Test-Path -LiteralPath $Destination -PathType Leaf) {
            [System.IO.File]::Replace($temporary, $Destination, $replacementBackup, $true)
        }
        else {
            Move-Item -LiteralPath $temporary -Destination $Destination
        }
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $replacementBackup -Force -ErrorAction SilentlyContinue
    }
}

function New-ReleaseSecurity {
    param(
        [Parameter(Mandatory = $true)][bool]$Directory,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$SystemSid,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$AdminSid,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$RuntimeSid,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$BrokerSid,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$ObserverSid
    )
    if ($Directory) {
        $security = New-Object System.Security.AccessControl.DirectorySecurity
        $inheritance = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    }
    else {
        $security = New-Object System.Security.AccessControl.FileSecurity
        $inheritance = [System.Security.AccessControl.InheritanceFlags]::None
    }
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($AdminSid)
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $full = [System.Security.AccessControl.FileSystemRights]::FullControl
    $read = [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($SystemSid, $full, $inheritance, $none, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($AdminSid, $full, $inheritance, $none, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($RuntimeSid, $read, $inheritance, $none, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($BrokerSid, $read, $inheritance, $none, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($ObserverSid, $read, $inheritance, $none, $allow)))
    return $security
}

function Protect-ImmutableReleaseTree {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-NoReparsePathChain -Path $Path
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $runtimeSid = (New-Object System.Security.Principal.NTAccount($RuntimeAccount)).Translate([System.Security.Principal.SecurityIdentifier])
    $brokerSid = (New-Object System.Security.Principal.NTAccount($BrokerAccount)).Translate([System.Security.Principal.SecurityIdentifier])
    $observerSid = (New-Object System.Security.Principal.NTAccount($ObserverAccount)).Translate([System.Security.Principal.SecurityIdentifier])
    # Close the root to stale writers before reading or traversing release files.
    $rootSecurity = New-ReleaseSecurity -Directory $true -SystemSid $systemSid -AdminSid $adminSid -RuntimeSid $runtimeSid -BrokerSid $brokerSid -ObserverSid $observerSid
    [System.IO.Directory]::SetAccessControl($Path, $rootSecurity)
    $items = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)
    $reparse = @($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($reparse.Count -ne 0) {
        throw "immutable release contains a reparse point: $($reparse[0].FullName)"
    }
    $venvRoot = (Join-Path $Path ".venv").TrimEnd("\") + "\"
    $sourceBytecode = @($items | Where-Object {
        -not $_.FullName.StartsWith($venvRoot, [System.StringComparison]::OrdinalIgnoreCase) -and
        ($_.Name -eq "__pycache__" -or $_.Extension -in @(".pyc", ".pyo"))
    })
    if ($sourceBytecode.Count -ne 0) {
        throw "immutable release contains generated source bytecode: $($sourceBytecode[0].FullName)"
    }
    foreach ($file in @($items | Where-Object { -not $_.PSIsContainer })) {
        Assert-RegularSingleLinkFile -Path $file.FullName -Label "immutable release file"
        $security = New-ReleaseSecurity -Directory $false -SystemSid $systemSid -AdminSid $adminSid -RuntimeSid $runtimeSid -BrokerSid $brokerSid -ObserverSid $observerSid
        [System.IO.File]::SetAccessControl($file.FullName, $security)
    }
    foreach ($directory in @($items | Where-Object { $_.PSIsContainer } | Sort-Object { $_.FullName.Length } -Descending)) {
        $security = New-ReleaseSecurity -Directory $true -SystemSid $systemSid -AdminSid $adminSid -RuntimeSid $runtimeSid -BrokerSid $brokerSid -ObserverSid $observerSid
        [System.IO.Directory]::SetAccessControl($directory.FullName, $security)
    }
    [System.IO.Directory]::SetAccessControl($Path, $rootSecurity)
    Assert-NoReparsePathChain -Path $Path
}

function Assert-ReleaseMatchesBootstrapManifest {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][byte[]]$ManifestBytes
    )
    $utf8 = New-Object System.Text.UTF8Encoding($false, $true)
    $manifest = $utf8.GetString($ManifestBytes) | ConvertFrom-Json
    if ([string]$manifest.schemaVersion -ne "fouler-bootstrap-manifest/v1") {
        throw "release bootstrap manifest schema is unsupported"
    }
    $releaseLeaf = Split-Path -Leaf $Path
    if ([string]$manifest.projectId -ne "fouler-play" -or [string]$manifest.sourceCommit -ne $releaseLeaf) {
        throw "release bootstrap manifest does not bind this Fouler release"
    }
    if (-not $manifest.files) {
        throw "release bootstrap manifest has no file inventory"
    }
    $expected = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($property in $manifest.files.PSObject.Properties) {
        $relative = ([string]$property.Name).Replace("\", "/")
        $digest = ([string]$property.Value).ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($relative) -or [System.IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)\.\.?(?:/|$)' -or $digest -notmatch '^[0-9a-f]{64}$') {
            throw "release bootstrap manifest contains an unsafe file entry"
        }
        if ($expected.ContainsKey($relative)) {
            throw "release bootstrap manifest contains a duplicate file entry: $relative"
        }
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $Path ($relative.Replace("/", "\"))))
        if (-not $candidate.StartsWith($Path + "\", [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "release bootstrap manifest entry escapes ProjectDir"
        }
        $expected.Add($relative, $digest)
    }
    foreach ($required in @(
        ".venv/Scripts/python.exe",
        "scripts/devstream_runtime_lease.py",
        "infrastructure/deployment_lineage.py",
        "infrastructure/runtime_authorization.py",
        "infrastructure/windows/fouler_lease_broker.py"
    )) {
        if (-not $expected.ContainsKey($required)) {
            throw "release bootstrap manifest omits required verifier file: $required"
        }
    }
    $actualFiles = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -File -ErrorAction Stop)
    if ($actualFiles.Count -ne $expected.Count) {
        throw "release file inventory does not exactly match the bootstrap manifest"
    }
    foreach ($file in $actualFiles) {
        Assert-RegularSingleLinkFile -Path $file.FullName -Label "manifested release file"
        $relative = $file.FullName.Substring($Path.Length + 1).Replace("\", "/")
        if (-not $expected.ContainsKey($relative)) {
            throw "release contains a file absent from the bootstrap manifest: $relative"
        }
        $actualHash = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($actualHash -ne $expected[$relative]) {
            throw "release file SHA-256 differs from the bootstrap manifest: $relative"
        }
    }
    return $manifest
}

function Invoke-AuthorityValidation {
    param(
        [Parameter(Mandatory = $true)][string]$Python,
        [Parameter(Mandatory = $true)][string]$Validator,
        [Parameter(Mandatory = $true)][string]$LeasePath,
        [Parameter(Mandatory = $true)][string]$TrustStorePath,
        [string]$Purpose = "jigglypuff-runtime-start"
    )
    $validatorArguments = @(
        $Validator,
        "--purpose", $Purpose,
        "--runtime-lease", $LeasePath,
        "--run-count", [string]$RunCount,
        "--max-cycles", [string]$MaxCycles,
        "--max-concurrent-battles", [string]$MaxConcurrentBattles,
        "--account", $Account,
        "--replay-behavior", $ReplayBehavior,
        "--require-run-count",
        "--require-max-cycles",
        "--require-max-concurrent-battles",
        "--require-replay-behavior",
        "--require-deployment-receipt",
        "--verify-deployment-checkout"
    )
    $previousTrustStore = $env:FOULER_CONTROLLER_TRUST_STORE_PATH
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $previousOptionalLocks = $env:GIT_OPTIONAL_LOCKS
    try {
        $env:FOULER_CONTROLLER_TRUST_STORE_PATH = $TrustStorePath
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $env:GIT_OPTIONAL_LOCKS = "0"
        $validationText = (& $Python -I -B @validatorArguments 2>&1 | Out-String).Trim()
    }
    finally {
        $env:FOULER_CONTROLLER_TRUST_STORE_PATH = $previousTrustStore
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
        $env:GIT_OPTIONAL_LOCKS = $previousOptionalLocks
    }
    if ($LASTEXITCODE -ne 0) {
        throw "staged runtime authority failed exact-release validation"
    }
    $validation = $validationText | ConvertFrom-Json
    if (-not $validation.ok) {
        throw "staged runtime authority validator returned ok=false"
    }
    return $validation
}

function Register-BrokerLease {
    param(
        [Parameter(Mandatory = $true)]$Validation,
        [Parameter(Mandatory = $true)][string]$BackupDirectory
    )
    $summary = $Validation.lease
    $registration = [ordered]@{
        schemaVersion = "fouler-lease-broker-registration/v1"
        leaseId = [string]$summary.id
        authorizationDigest = [string]$summary.authorizationSha256
        sourceCommit = [string]$summary.sourceCommit
        sourceTree = [string]$summary.sourceTree
        changeId = [string]$summary.changeId
        deploymentId = [string]$summary.deploymentId
        sessionId = [string]$summary.sessionId
        runtimeManifestDigest = [string]$summary.runtimeManifestDigest
        deploymentReceiptSha256 = [string]$summary.deploymentReceiptSha256
        account = [string]$summary.account
        hostName = [string]$summary.hostName
        hostIdSha256 = [string]$summary.hostIdSha256
        proofStartsAt = [string]$summary.proofWindow.startsAt
        proofExpiresAt = [string]$summary.proofWindow.expiresAt
        maxRunCount = [int]$summary.maxRunCount
        maxCycles = [int]$summary.maxCycles
        maxConcurrentBattles = [int]$summary.maxConcurrentBattles
        improveAuthorized = [bool]$ImproveAuthorized
    }
    $registrationPath = Join-Path $BackupDirectory "lease-broker-registration.json"
    $registrationText = $registration | ConvertTo-Json -Depth 8 -Compress
    [byte[]]$registrationBytes = [System.Text.UTF8Encoding]::new($false).GetBytes($registrationText)
    [System.IO.File]::WriteAllBytes($registrationPath, $registrationBytes)
    Protect-AdminFile -Path $registrationPath
    $registrationHash = Get-BytesSha256 -Bytes $registrationBytes
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $registerText = (& $python -I -B $brokerEntrypoint --store-path $brokerStorePath --marker-path $brokerMarkerPath register-lease --registration $registrationPath --expected-registration-sha256 $registrationHash 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "lease broker registration failed closed: $registerText"
        }
    }
    finally {
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
    }
    return [pscustomobject]@{
        result = ($registerText | ConvertFrom-Json)
        registrationPath = $registrationPath
        registrationSha256 = $registrationHash
    }
}

function Initialize-BrokerStore {
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    try {
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $output = (& $python -I -B $brokerEntrypoint --store-path $brokerStorePath --marker-path $brokerMarkerPath initialize-store 2>&1 | Out-String).Trim()
        if ($LASTEXITCODE -ne 0) {
            throw "lease broker store initialization failed closed: $output"
        }
        return $output | ConvertFrom-Json
    }
    finally {
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
    }
}

function Assert-NoCompetingBrokerProcess {
    $storeNeedle = [System.IO.Path]::GetFullPath($brokerStorePath)
    $processes = @(Get-CimInstance Win32_Process -Filter "name like 'python%'" -ErrorAction SilentlyContinue | Where-Object {
        $commandLine = [string]$_.CommandLine
        -not [string]::IsNullOrWhiteSpace($commandLine) -and
        $commandLine -match '(?i)fouler_lease_broker\.py' -and
        $commandLine -match '(?i)(?:^|\s)serve(?:\s|$)' -and
        $commandLine.IndexOf($storeNeedle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
    })
    if ($processes.Count -gt 0) {
        throw "stale or competing Fouler lease broker process is active during authority registration: $(@($processes.ProcessId) -join ', ')"
    }
}

function Assert-NoCompetingObsProcess {
    $processes = @(Get-CimInstance Win32_Process -Filter "name like 'python%'" -ErrorAction SilentlyContinue | Where-Object {
        $commandLine = [string]$_.CommandLine
        -not [string]::IsNullOrWhiteSpace($commandLine) -and
        $commandLine -match '(?i)(?:run_obs_server_service|serve_obs_page)\.py'
    })
    if ($processes.Count -gt 0) {
        throw "stale or competing Fouler OBS Python process is active before release trust: $(@($processes.ProcessId) -join ', ')"
    }
}

$resolvedProject = [System.IO.Path]::GetFullPath($ProjectDir).TrimEnd("\")
if ($resolvedProject -notmatch '^D:\\Releases\\fouler-play\\[0-9a-f]{40}$') {
    throw "ProjectDir must be an immutable D:\Releases\fouler-play\<commit> release"
}
if ($MaxConcurrentBattles -ne 3) {
    throw "owner-locked live pilot MaxConcurrentBattles must equal 3"
}
$pilotSearchParallelism = 2
$canonicalAuthorityRoot = "C:\ProgramData\HERMES\authority\fouler"
$canonicalBackupRoot = "C:\ProgramData\HERMES\backups\fouler-authority"
$canonicalBrokerRoot = "C:\ProgramData\HERMES-LeaseBroker\fouler"
$canonicalRuntimeAccount = "JIGGLYPUFF\devstream-live"
$canonicalBrokerAccount = "NT SERVICE\HERMES-FoulerLeaseBroker"
$canonicalObserverAccount = "NT SERVICE\HERMES-FoulerObsServer"
$canonicalBrokerServiceName = "HERMES-FoulerLeaseBroker"
if (-not [string]::Equals([System.IO.Path]::GetFullPath($AuthorityRoot).TrimEnd("\"), $canonicalAuthorityRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "AuthorityRoot must equal the fixed Fouler authority root: $canonicalAuthorityRoot"
}
foreach ($canonicalPath in @(
    [pscustomobject]@{ Supplied = $BackupRoot; Expected = $canonicalBackupRoot; Label = "BackupRoot" },
    [pscustomobject]@{ Supplied = $BrokerRoot; Expected = $canonicalBrokerRoot; Label = "BrokerRoot" }
)) {
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$canonicalPath.Supplied).TrimEnd("\"), [string]$canonicalPath.Expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$($canonicalPath.Label) must equal the canonical Fouler path: $($canonicalPath.Expected)"
    }
}
foreach ($canonicalIdentity in @(
    [pscustomobject]@{ Supplied = $RuntimeAccount; Expected = $canonicalRuntimeAccount; Label = "RuntimeAccount" },
    [pscustomobject]@{ Supplied = $BrokerAccount; Expected = $canonicalBrokerAccount; Label = "BrokerAccount" },
    [pscustomobject]@{ Supplied = $ObserverAccount; Expected = $canonicalObserverAccount; Label = "ObserverAccount" },
    [pscustomobject]@{ Supplied = $BrokerServiceName; Expected = $canonicalBrokerServiceName; Label = "BrokerServiceName" }
)) {
    if (-not [string]::Equals([string]$canonicalIdentity.Supplied, [string]$canonicalIdentity.Expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$($canonicalIdentity.Label) must equal the canonical Fouler identity: $($canonicalIdentity.Expected)"
    }
}
$AuthorityRoot = $canonicalAuthorityRoot
$BackupRoot = $canonicalBackupRoot
$BrokerRoot = $canonicalBrokerRoot
Assert-NoReparsePathChain -Path $resolvedProject
$installerPath = [System.IO.Path]::GetFullPath([string]$MyInvocation.MyCommand.Path)
if ($installerPath -match '^D:\\Releases\\fouler-play\\') { throw "authority installer must execute from the trusted control plane, never a release tree" }
Assert-NoReparsePathChain -Path $installerPath
Assert-RegularSingleLinkFile -Path $installerPath -Label "authority installer"
Assert-NoPathOverlap -First $resolvedProject -Second $installerPath -Label "authority installer path"
$projectItem = Get-Item -LiteralPath $resolvedProject -Force -ErrorAction Stop
if (-not $projectItem.PSIsContainer) {
    throw "ProjectDir must be a directory"
}
foreach ($externalPath in @($AuthorityRoot, $BackupRoot, $BrokerRoot, $KeyringSource, $LeaseSource, $ReleaseManifestSource, $AccountSeasonSource)) {
    Assert-NoPathOverlap -First $resolvedProject -Second $externalPath -Label "authority/bootstrap path"
}
foreach ($protectedRoot in @($AuthorityRoot, $BackupRoot, $BrokerRoot)) {
    Assert-NoPathOverlap -First $protectedRoot -Second $AccountSeasonSource -Label "account-season source/protected root"
}
$python = Join-Path $resolvedProject ".venv\Scripts\python.exe"
$validator = Join-Path $resolvedProject "scripts\devstream_runtime_lease.py"
$brokerEntrypoint = Join-Path $resolvedProject "infrastructure\windows\fouler_lease_broker.py"
$brokerStorePath = Join-Path $BrokerRoot "consumption.sqlite3"
$brokerMarkerPath = Join-Path $BrokerRoot "consumption.sqlite3.initialized"
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $validator -PathType Leaf)) {
    throw "immutable release Python or runtime lease validator is missing"
}
if (-not (Test-Path -LiteralPath $brokerEntrypoint -PathType Leaf)) {
    throw "immutable release lease broker entrypoint is missing"
}

$expectedKeyringHash = $ExpectedKeyringSha256.ToLowerInvariant()
$expectedReleaseManifestHash = $ExpectedReleaseManifestSha256.ToLowerInvariant()
$expectedAccountSeasonHash = $ExpectedAccountSeasonSha256.ToLowerInvariant()
$keyringSnapshot = Get-RegularFileSnapshot -Path $KeyringSource -Label "keyring source"
if ($keyringSnapshot.Sha256 -ne $expectedKeyringHash) {
    throw "keyring source SHA-256 does not match the trusted bootstrap digest"
}
$leaseSnapshot = Get-RegularFileSnapshot -Path $LeaseSource -Label "lease source"
$releaseManifestSnapshot = Get-RegularFileSnapshot -Path $ReleaseManifestSource -Label "release bootstrap manifest" -MaxBytes 67108864
if ($releaseManifestSnapshot.Sha256 -ne $expectedReleaseManifestHash) {
    throw "release bootstrap manifest SHA-256 does not match the external trusted digest"
}
$accountSeasonSnapshot = Get-RegularFileSnapshot -Path $AccountSeasonSource -Label "account-season source" -MaxBytes 65536
if ($accountSeasonSnapshot.Sha256 -ne $expectedAccountSeasonHash) {
    throw "account-season source SHA-256 does not match the owner-pinned digest"
}
$accountSeasonIdentity = ConvertFrom-StrictAccountSeasonSnapshot -Bytes $accountSeasonSnapshot.Bytes -ExpectedAccount $Account
$leaseText = [System.Text.Encoding]::UTF8.GetString($leaseSnapshot.Bytes)
$leasePayload = $leaseText | ConvertFrom-Json
$leaseId = [string]$leasePayload.leaseId
if ($leaseId -notmatch '^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$') {
    throw "lease source has a missing or malformed leaseId"
}

$keyringDestination = Join-Path $AuthorityRoot "controller-keys.json"
$leaseDirectory = Join-Path $AuthorityRoot "leases"
$leaseDestination = Join-Path $leaseDirectory ($leaseId + ".json")
$currentLeaseDestination = Join-Path $AuthorityRoot "runtime-lease.json"
$accountSeasonDestination = "C:\ProgramData\HERMES\authority\fouler\account-season.json"
$releaseAuthorityDirectory = Join-Path $AuthorityRoot ("releases\" + (Split-Path -Leaf $resolvedProject))
$releaseManifestDestination = Join-Path $releaseAuthorityDirectory "bootstrap-manifest.json"
$activationDirectory = Join-Path $AuthorityRoot "broker-activations"
$brokerActivationDestination = Join-Path $activationDirectory ((Split-Path -Leaf $resolvedProject) + ".json")
$installedAccountSeasonSnapshot = $null
$accountSeasonReplacementRequired = $false
if (Test-Path -LiteralPath $accountSeasonDestination) {
    $installedAccountSeasonSnapshot = Get-RegularFileSnapshot -Path $accountSeasonDestination -Label "installed account-season authority" -MaxBytes 65536
    if ($installedAccountSeasonSnapshot.Sha256 -ne $accountSeasonSnapshot.Sha256) {
        if (-not $OwnerRotateAccountSeason) {
            throw "installed account-season authority differs from the pinned source; explicit -OwnerRotateAccountSeason approval is required"
        }
        $accountSeasonReplacementRequired = $true
    }
}
$plan = [ordered]@{
    schemaVersion = "fouler-runtime-authority-install/v2"
    apply = [bool]$Apply
    projectDir = $resolvedProject
    keyringDestination = $keyringDestination
    expectedKeyringSha256 = $expectedKeyringHash
    sourceKeyringSha256 = $keyringSnapshot.Sha256
    sourceLeaseSha256 = $leaseSnapshot.Sha256
    sourceReleaseManifestSha256 = $releaseManifestSnapshot.Sha256
    sourceAccountSeasonSha256 = $accountSeasonSnapshot.Sha256
    accountSeasonDestination = $accountSeasonDestination
    accountSeasonAccount = $accountSeasonIdentity.account
    accountSeasonSeasonId = $accountSeasonIdentity.seasonId
    ownerAccountSeasonRotationRequested = [bool]$OwnerRotateAccountSeason
    accountSeasonReplacementRequired = $accountSeasonReplacementRequired
    releaseManifestDestination = $releaseManifestDestination
    leaseDestination = $leaseDestination
    currentLeaseDestination = $currentLeaseDestination
    leaseId = $leaseId
    runCount = $RunCount
    maxCycles = $MaxCycles
    maxConcurrentBattles = $MaxConcurrentBattles
    searchParallelism = $pilotSearchParallelism
    replayBehavior = $ReplayBehavior
    account = $Account
    observerAccount = $ObserverAccount
    brokerRoot = $BrokerRoot
    brokerServiceName = $BrokerServiceName
    brokerActivationDestination = $brokerActivationDestination
    improveAuthorized = [bool]$ImproveAuthorized
    keyRotationAllowed = $false
    releaseDaclEnforced = [bool]$Apply
    startsProcesses = $false
    mutatesScheduledTasks = $false
}
if (-not $Apply) {
    $plan.status = "dry-run"
    $plan | ConvertTo-Json -Depth 6
    exit 0
}
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Apply requires an elevated administrator PowerShell session"
}

$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ") + "-" + [guid]::NewGuid().ToString("N")
$backupDirectory = Join-Path $BackupRoot $stamp
$releaseAclBackup = Join-Path $backupDirectory "release-acl.txt"
$keyringInstalled = $false
$leaseCreated = $false
$releaseManifestInstalled = $false
$brokerActivationCreated = $false
$currentLeaseWritten = $false
$accountSeasonInstalled = $false
$accountSeasonRotated = $false
$accountSeasonRotationAttempted = $false
$accountSeasonRotationEvidence = $null
$previousCurrentLease = $null
try {
    $brokerService = Get-Service -Name $BrokerServiceName -ErrorAction SilentlyContinue
    if (-not $brokerService -or [string]$brokerService.StartType -ne "Disabled" -or [string]$brokerService.Status -ne "Stopped") {
        throw "lease broker service must exist, be stopped, and remain Disabled until authority registration completes"
    }
    Assert-NoCompetingBrokerProcess
    if (-not $ObserverAccount.StartsWith("NT SERVICE\", [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "ObserverAccount must be a virtual service identity"
    }
    $observerServiceName = $ObserverAccount.Substring("NT SERVICE\".Length)
    $observerService = Get-Service -Name $observerServiceName -ErrorAction SilentlyContinue
    if (-not $observerService -or [string]$observerService.StartType -ne "Disabled" -or [string]$observerService.Status -ne "Stopped") {
        throw "OBS service identity must be provisioned, stopped, and Disabled before release authority is installed"
    }
    Assert-NoCompetingObsProcess

    Protect-Directory -Path $AuthorityRoot -RuntimeRead
    Protect-Directory -Path $leaseDirectory -RuntimeRead
    Protect-Directory -Path $releaseAuthorityDirectory -RuntimeRead
    Protect-Directory -Path $activationDirectory -RuntimeRead
    Protect-Directory -Path $BackupRoot
    Protect-Directory -Path $backupDirectory

    if ($null -eq $installedAccountSeasonSnapshot) {
        Write-ExactAtomic -Content $accountSeasonSnapshot.Bytes -Destination $accountSeasonDestination
        $accountSeasonInstalled = $true
    }
    elseif ($accountSeasonReplacementRequired) {
        $accountSeasonBackup = Join-Path $backupDirectory "account-season.previous.json"
        Write-ExactAtomic -Content $installedAccountSeasonSnapshot.Bytes -Destination $accountSeasonBackup
        Protect-AdminFile -Path $accountSeasonBackup
        if ((Get-FileHash -LiteralPath $accountSeasonBackup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $installedAccountSeasonSnapshot.Sha256) {
            throw "account-season rollback backup failed its pre-mutation SHA-256 verification"
        }
        $rotationEvidencePayload = [ordered]@{
            schemaVersion = "fouler-account-season-rotation-evidence/v1"
            capturedAt = [DateTime]::UtcNow.ToString("o")
            ownerRotationApproved = [bool]$OwnerRotateAccountSeason
            authorityPath = $accountSeasonDestination
            rollbackBackupPath = $accountSeasonBackup
            previousSha256 = $installedAccountSeasonSnapshot.Sha256
            replacementSha256 = $accountSeasonSnapshot.Sha256
            replacementAccount = $accountSeasonIdentity.account
            replacementSeasonId = $accountSeasonIdentity.seasonId
        }
        $accountSeasonRotationEvidence = Join-Path $backupDirectory "account-season-rotation-evidence.json"
        [byte[]]$rotationEvidenceBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(($rotationEvidencePayload | ConvertTo-Json -Depth 6 -Compress))
        Write-ExactAtomic -Content $rotationEvidenceBytes -Destination $accountSeasonRotationEvidence
        Protect-AdminFile -Path $accountSeasonRotationEvidence
        $accountSeasonRotationAttempted = $true
        Clear-AccountSeasonReadOnlyAttribute -Path $accountSeasonDestination
        Write-ReplaceAtomic -Content $accountSeasonSnapshot.Bytes -Destination $accountSeasonDestination
        $accountSeasonRotated = $true
    }
    Protect-AccountSeasonFile -Path $accountSeasonDestination
    Assert-RegularSingleLinkFile -Path $accountSeasonDestination -Label "installed account-season authority"
    if ((Get-FileHash -LiteralPath $accountSeasonDestination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $accountSeasonSnapshot.Sha256) {
        throw "installed account-season authority differs from the pinned source snapshot"
    }

    if (Test-Path -LiteralPath $keyringDestination) {
        $existingKeyring = Get-RegularFileSnapshot -Path $keyringDestination -Label "installed keyring"
        if ($existingKeyring.Sha256 -ne $expectedKeyringHash -or $existingKeyring.Sha256 -ne $keyringSnapshot.Sha256) {
            throw "installed keyring differs from the pinned keyring; in-place key rotation is forbidden"
        }
    }
    else {
        Write-ExactAtomic -Content $keyringSnapshot.Bytes -Destination $keyringDestination
        $keyringInstalled = $true
    }
    Protect-File -Path $keyringDestination

    Write-ExactAtomic -Content $leaseSnapshot.Bytes -Destination $leaseDestination
    $leaseCreated = $true
    Protect-File -Path $leaseDestination

    if (Test-Path -LiteralPath $releaseManifestDestination) {
        $installedManifest = Get-RegularFileSnapshot -Path $releaseManifestDestination -Label "installed release bootstrap manifest" -MaxBytes 67108864
        if ($installedManifest.Sha256 -ne $releaseManifestSnapshot.Sha256) {
            throw "installed release bootstrap manifest differs from the externally pinned manifest"
        }
    }
    else {
        Write-ExactAtomic -Content $releaseManifestSnapshot.Bytes -Destination $releaseManifestDestination
        $releaseManifestInstalled = $true
    }
    Protect-File -Path $releaseManifestDestination

    if (Test-Path -LiteralPath $currentLeaseDestination) {
        $previousCurrentLease = Get-RegularFileSnapshot -Path $currentLeaseDestination -Label "current runtime lease"
        $currentLeaseBackup = Join-Path $backupDirectory "runtime-lease.json"
        Write-ExactAtomic -Content $previousCurrentLease.Bytes -Destination $currentLeaseBackup
        Protect-AdminFile -Path $currentLeaseBackup
    }
    & "$env:SystemRoot\System32\icacls.exe" $resolvedProject /save $releaseAclBackup /T /C /Q | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "failed to back up immutable release ACLs"
    }
    Protect-AdminFile -Path $releaseAclBackup
    Protect-ImmutableReleaseTree -Path $resolvedProject
    $manifest = Assert-ReleaseMatchesBootstrapManifest -Path $resolvedProject -ManifestBytes $releaseManifestSnapshot.Bytes

    # No release code is executed before the exact DACL and externally pinned
    # full-file manifest have both been verified.
    $leaseValidation = Invoke-AuthorityValidation -Python $python -Validator $validator -LeasePath $leaseDestination -TrustStorePath $keyringDestination
    if ($ImproveAuthorized) {
        $null = Invoke-AuthorityValidation -Python $python -Validator $validator -LeasePath $leaseDestination -TrustStorePath $keyringDestination -Purpose "improve-agent"
    }

    Write-ReplaceAtomic -Content $leaseSnapshot.Bytes -Destination $currentLeaseDestination
    $currentLeaseWritten = $true
    Protect-File -Path $currentLeaseDestination
    $currentLeaseValidation = Invoke-AuthorityValidation -Python $python -Validator $validator -LeasePath $currentLeaseDestination -TrustStorePath $keyringDestination

    $brokerInitialization = Initialize-BrokerStore
    $brokerRegistration = Register-BrokerLease -Validation $leaseValidation -BackupDirectory $backupDirectory
    $activation = [ordered]@{
        schemaVersion = "fouler-lease-broker-activation/v1"
        serviceName = $BrokerServiceName
        projectDir = $resolvedProject
        sourceCommit = [string]$manifest.sourceCommit
        releaseManifestPath = $releaseManifestDestination
        releaseManifestSha256 = $releaseManifestSnapshot.Sha256
        leaseId = [string]$leaseValidation.lease.id
        authorizationDigest = [string]$leaseValidation.lease.authorizationSha256
        registrationPath = [string]$brokerRegistration.registrationPath
        registrationSha256 = [string]$brokerRegistration.registrationSha256
        storePath = $brokerStorePath
        markerPath = $brokerMarkerPath
        registered = $true
    }
    [byte[]]$activationBytes = [System.Text.UTF8Encoding]::new($false).GetBytes(($activation | ConvertTo-Json -Depth 8 -Compress))
    Write-ExactAtomic -Content $activationBytes -Destination $brokerActivationDestination
    $brokerActivationCreated = $true
    Protect-File -Path $brokerActivationDestination

    $plan.status = "installed-and-validated"
    $plan.keyringSha256 = $expectedKeyringHash
    $plan.leaseSha256 = $leaseSnapshot.Sha256
    $plan.currentLeaseSha256 = $leaseSnapshot.Sha256
    $plan.validationOk = $true
    $plan.currentLeaseValidationOk = [bool]$currentLeaseValidation.ok
    $plan.releaseManifestVerified = $true
    $plan.brokerInitialization = $brokerInitialization
    $plan.brokerRegistration = $brokerRegistration
    $plan.brokerActivation = $brokerActivationDestination
    $plan.backupDirectory = $backupDirectory
    $plan.releaseAclBackup = $releaseAclBackup
    $plan | ConvertTo-Json -Depth 6
    exit 0
}
catch {
    $installFailure = $_
    $rollbackFailures = New-Object 'System.Collections.Generic.List[string]'

    if ($accountSeasonRotationAttempted -and $null -ne $installedAccountSeasonSnapshot) {
        try {
            Clear-AccountSeasonReadOnlyAttribute -Path $accountSeasonDestination
            Write-ReplaceAtomic -Content $installedAccountSeasonSnapshot.Bytes -Destination $accountSeasonDestination
            Protect-AccountSeasonFile -Path $accountSeasonDestination
            Assert-RegularSingleLinkFile -Path $accountSeasonDestination -Label "rolled-back account-season authority"
            if ((Get-FileHash -LiteralPath $accountSeasonDestination -Algorithm SHA256).Hash.ToLowerInvariant() -ne $installedAccountSeasonSnapshot.Sha256) {
                throw "rolled-back account-season authority differs from its verified backup"
            }
        }
        catch {
            $rollbackFailures.Add("account-season rotation rollback failed: $($_.Exception.Message)")
        }
    }
    elseif ($accountSeasonInstalled) {
        try {
            Clear-AccountSeasonReadOnlyAttribute -Path $accountSeasonDestination
            Remove-Item -LiteralPath $accountSeasonDestination -Force -ErrorAction Stop
        }
        catch {
            $rollbackFailures.Add("new account-season authority rollback failed: $($_.Exception.Message)")
        }
    }

    try {
        if ($brokerActivationCreated) {
            Remove-Item -LiteralPath $brokerActivationDestination -Force -ErrorAction Stop
        }
    }
    catch { $rollbackFailures.Add("broker activation rollback failed: $($_.Exception.Message)") }

    try {
        if ($currentLeaseWritten) {
            if ($null -ne $previousCurrentLease) {
                Write-ReplaceAtomic -Content $previousCurrentLease.Bytes -Destination $currentLeaseDestination
                Protect-File -Path $currentLeaseDestination
            }
            else {
                Remove-Item -LiteralPath $currentLeaseDestination -Force -ErrorAction Stop
            }
        }
    }
    catch { $rollbackFailures.Add("current lease rollback failed: $($_.Exception.Message)") }

    foreach ($createdAuthorityFile in @(
        [pscustomobject]@{ Created = $leaseCreated; Path = $leaseDestination; Label = "lease" },
        [pscustomobject]@{ Created = $releaseManifestInstalled; Path = $releaseManifestDestination; Label = "release manifest" },
        [pscustomobject]@{ Created = $keyringInstalled; Path = $keyringDestination; Label = "keyring" }
    )) {
        if (-not $createdAuthorityFile.Created) { continue }
        try {
            Remove-Item -LiteralPath $createdAuthorityFile.Path -Force -ErrorAction Stop
        }
        catch { $rollbackFailures.Add("$($createdAuthorityFile.Label) rollback failed: $($_.Exception.Message)") }
    }

    if ($rollbackFailures.Count -gt 0) {
        throw [System.InvalidOperationException]::new(
            "runtime authority installation failed and rollback was incomplete: $($rollbackFailures -join '; ')",
            $installFailure.Exception
        )
    }
    throw $installFailure
}
