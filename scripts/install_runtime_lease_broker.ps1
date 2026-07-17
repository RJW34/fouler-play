[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectDir,
    [Parameter(Mandatory = $true)]
    [string]$NssmSource,
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[0-9A-Fa-f]{64}$')]
    [string]$ExpectedNssmSha256,
    [Parameter(Mandatory = $true)]
    [string]$RuntimeAccount,
    [string]$ServiceName = "HERMES-FoulerLeaseBroker",
    [string]$BrokerRoot = "C:\ProgramData\HERMES-LeaseBroker\fouler",
    [string]$BackupRoot = "C:\ProgramData\HERMES-LeaseBroker\backups",
    [string]$StableNssm = "C:\ProgramData\HERMES-LeaseBroker\bin\nssm.exe",
    [string]$AuthorityRoot = "C:\ProgramData\HERMES\authority\fouler",
    [switch]$Apply,
    [switch]$StartBroker,
    [switch]$RestartBroker
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$canonicalServiceName = "HERMES-FoulerLeaseBroker"
$canonicalRuntimeAccount = "JIGGLYPUFF\devstream-live"
$canonicalBrokerRoot = "C:\ProgramData\HERMES-LeaseBroker\fouler"
$canonicalBackupRoot = "C:\ProgramData\HERMES-LeaseBroker\backups"
$canonicalStableNssm = "C:\ProgramData\HERMES-LeaseBroker\bin\nssm.exe"
$canonicalAuthorityRoot = "C:\ProgramData\HERMES\authority\fouler"
if (-not [string]::Equals($ServiceName, $canonicalServiceName, [System.StringComparison]::Ordinal)) {
    throw "ServiceName must equal the exact Fouler broker service name: $canonicalServiceName"
}
if (-not [string]::Equals($RuntimeAccount, $canonicalRuntimeAccount, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "RuntimeAccount must equal the canonical Fouler runtime identity: $canonicalRuntimeAccount"
}
foreach ($canonicalPath in @(
    [pscustomobject]@{ Supplied = $BrokerRoot; Expected = $canonicalBrokerRoot; Label = "BrokerRoot" },
    [pscustomobject]@{ Supplied = $BackupRoot; Expected = $canonicalBackupRoot; Label = "BackupRoot" },
    [pscustomobject]@{ Supplied = $StableNssm; Expected = $canonicalStableNssm; Label = "StableNssm" },
    [pscustomobject]@{ Supplied = $AuthorityRoot; Expected = $canonicalAuthorityRoot; Label = "AuthorityRoot" }
)) {
    $suppliedPath = [System.IO.Path]::GetFullPath([string]$canonicalPath.Supplied).TrimEnd("\")
    if (-not [string]::Equals($suppliedPath, [string]$canonicalPath.Expected, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$($canonicalPath.Label) must equal the canonical Fouler path: $($canonicalPath.Expected)"
    }
}

if (-not ("FoulerBroker.NativeFileIdentity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace FoulerBroker {
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

function Assert-RegularSingleLinkFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
        throw "$Label must be a regular non-reparse-point file"
    }
    $stream = [System.IO.File]::Open(
        $item.FullName,
        [System.IO.FileMode]::Open,
        [System.IO.FileAccess]::Read,
        ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete)
    )
    try {
        $information = New-Object FoulerBroker.ByHandleFileInformation
        if (-not [FoulerBroker.NativeFileIdentity]::GetFileInformationByHandle($stream.SafeFileHandle, [ref]$information)) {
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
    param([string]$First, [string]$Second, [string]$Label)
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

function Get-BytesSha256 {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)
    $hasher = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([BitConverter]::ToString($hasher.ComputeHash($Bytes))).Replace("-", "").ToLowerInvariant()
    }
    finally {
        $hasher.Dispose()
    }
}

function Get-RegularFileSnapshot {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label,
        [int64]$MaxBytes = 67108864
    )
    $resolved = [System.IO.Path]::GetFullPath($Path)
    Assert-NoReparsePathChain -Path $resolved
    $item = Get-Item -LiteralPath $resolved -Force
    Assert-RegularSingleLinkFile -Path $resolved -Label $Label
    if ($item.Length -le 0 -or $item.Length -gt $MaxBytes) {
        throw "$Label has an invalid size"
    }
    [byte[]]$bytes = [System.IO.File]::ReadAllBytes($resolved)
    if ($bytes.LongLength -le 0 -or $bytes.LongLength -gt $MaxBytes) {
        throw "$Label has an invalid size"
    }
    return [pscustomobject]@{
        Path = $resolved
        Bytes = $bytes
        Sha256 = Get-BytesSha256 -Bytes $bytes
    }
}

function Write-AtomicBytes {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    $parent = Split-Path -Parent $Destination
    if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
        throw "atomic publication parent must be pre-created and protected"
    }
    Assert-NoReparsePathChain -Path $parent
    $temporary = Join-Path $parent ("." + [IO.Path]::GetFileName($Destination) + "." + [guid]::NewGuid().ToString("N") + ".tmp")
    try {
        [IO.File]::WriteAllBytes($temporary, $Bytes)
        Move-Item -LiteralPath $temporary -Destination $Destination -Force
    }
    finally {
        Remove-Item -LiteralPath $temporary -Force -ErrorAction SilentlyContinue
    }
}

function Get-Sid {
    param([Parameter(Mandatory = $true)][string]$Account)
    return (New-Object System.Security.Principal.NTAccount($Account)).Translate(
        [System.Security.Principal.SecurityIdentifier]
    )
}

function New-ProtectedDirectorySecurity {
    param(
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$ServiceSid,
        [switch]$ServiceReadOnly
    )
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $security = New-Object System.Security.AccessControl.DirectorySecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($adminSid)
    $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    $none = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $full = [System.Security.AccessControl.FileSystemRights]::FullControl
    $serviceRights = if ($ServiceReadOnly) {
        [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
    }
    else {
        [System.Security.AccessControl.FileSystemRights]::FullControl
    }
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, $full, $inherit, $none, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, $full, $inherit, $none, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($ServiceSid, $serviceRights, $inherit, $none, $allow)))
    return $security
}

function New-ProtectedFileSecurity {
    param(
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$ServiceSid,
        [switch]$ServiceReadOnly
    )
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $security = New-Object System.Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($adminSid)
    $noneInheritance = [System.Security.AccessControl.InheritanceFlags]::None
    $nonePropagation = [System.Security.AccessControl.PropagationFlags]::None
    $allow = [System.Security.AccessControl.AccessControlType]::Allow
    $full = [System.Security.AccessControl.FileSystemRights]::FullControl
    $serviceRights = if ($ServiceReadOnly) {
        [System.Security.AccessControl.FileSystemRights]::ReadAndExecute
    }
    else {
        [System.Security.AccessControl.FileSystemRights]::FullControl
    }
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($systemSid, $full, $noneInheritance, $nonePropagation, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($adminSid, $full, $noneInheritance, $nonePropagation, $allow)))
    $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule($ServiceSid, $serviceRights, $noneInheritance, $nonePropagation, $allow)))
    return $security
}

function Protect-DirectoryTree {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$ServiceSid,
        [switch]$ServiceReadOnly
    )
    Assert-NoReparsePathChain -Path $Path
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Assert-NoReparsePathChain -Path $Path
    $items = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)
    $reparse = @($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($reparse.Count -ne 0) {
        throw "protected directory tree contains a reparse point: $($reparse[0].FullName)"
    }
    foreach ($file in @($items | Where-Object { -not $_.PSIsContainer })) {
        Assert-RegularSingleLinkFile -Path $file.FullName -Label "protected broker file"
        $fileSecurity = New-ProtectedFileSecurity -ServiceSid $ServiceSid -ServiceReadOnly:$ServiceReadOnly
        [System.IO.File]::SetAccessControl($file.FullName, $fileSecurity)
    }
    foreach ($directory in @($items | Where-Object { $_.PSIsContainer } | Sort-Object { $_.FullName.Length } -Descending)) {
        $directorySecurity = New-ProtectedDirectorySecurity -ServiceSid $ServiceSid -ServiceReadOnly:$ServiceReadOnly
        [System.IO.Directory]::SetAccessControl($directory.FullName, $directorySecurity)
    }
    $rootSecurity = New-ProtectedDirectorySecurity -ServiceSid $ServiceSid -ServiceReadOnly:$ServiceReadOnly
    [System.IO.Directory]::SetAccessControl($Path, $rootSecurity)
}

function Grant-RuntimeServiceQueryStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][System.Security.Principal.SecurityIdentifier]$RuntimeSid
    )
    $serviceQueryStatus = 0x0004
    $sc = "$env:SystemRoot\System32\sc.exe"
    $beforeOutput = @(& $sc sdshow $Name 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "failed to read broker service DACL" }
    $beforeSddl = @(
        $beforeOutput |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ -match '^[OGDS]:' } |
            Select-Object -Last 1
    )
    if ($beforeSddl.Count -ne 1) { throw "broker service DACL output is malformed" }
    $descriptor = [System.Security.AccessControl.RawSecurityDescriptor]::new([string]$beforeSddl[0])
    $runtimeAces = @(
        $descriptor.DiscretionaryAcl | Where-Object {
            $_ -is [System.Security.AccessControl.KnownAce] -and
            [string]$_.SecurityIdentifier.Value -eq $RuntimeSid.Value
        }
    )
    if ($runtimeAces.Count -gt 0) {
        $exact = @($runtimeAces | Where-Object {
            $_ -is [System.Security.AccessControl.QualifiedAce] -and
            [string]$_.AceQualifier -eq "AccessAllowed" -and
            [int64]$_.AccessMask -eq $serviceQueryStatus
        })
        if ($runtimeAces.Count -ne 1 -or $exact.Count -ne 1) {
            throw "runtime SID has unexpected broker service rights; refusing to broaden or replace them"
        }
    }
    else {
        $ace = [System.Security.AccessControl.CommonAce]::new(
            [System.Security.AccessControl.AceFlags]::None,
            [System.Security.AccessControl.AceQualifier]::AccessAllowed,
            $serviceQueryStatus,
            $RuntimeSid,
            $false,
            $null
        )
        $descriptor.DiscretionaryAcl.InsertAce($descriptor.DiscretionaryAcl.Count, $ace)
        $updatedSddl = $descriptor.GetSddlForm([System.Security.AccessControl.AccessControlSections]::All)
        & $sc sdset $Name $updatedSddl | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "failed to grant runtime SID broker SERVICE_QUERY_STATUS" }
    }

    $afterOutput = @(& $sc sdshow $Name 2>&1)
    if ($LASTEXITCODE -ne 0) { throw "failed to verify broker service DACL" }
    $afterSddl = @(
        $afterOutput |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { $_ -match '^[OGDS]:' } |
            Select-Object -Last 1
    )
    if ($afterSddl.Count -ne 1) { throw "verified broker service DACL output is malformed" }
    $verified = [System.Security.AccessControl.RawSecurityDescriptor]::new([string]$afterSddl[0])
    $verifiedRuntimeAces = @(
        $verified.DiscretionaryAcl | Where-Object {
            $_ -is [System.Security.AccessControl.KnownAce] -and
            [string]$_.SecurityIdentifier.Value -eq $RuntimeSid.Value
        }
    )
    $verifiedExact = @($verifiedRuntimeAces | Where-Object {
        $_ -is [System.Security.AccessControl.QualifiedAce] -and
        [string]$_.AceQualifier -eq "AccessAllowed" -and
        [int64]$_.AccessMask -eq $serviceQueryStatus
    })
    if ($verifiedRuntimeAces.Count -ne 1 -or $verifiedExact.Count -ne 1) {
        throw "broker service DACL did not retain the exact query-status-only runtime ACE"
    }
    return [pscustomobject]@{
        sid = $RuntimeSid.Value
        accessMask = $serviceQueryStatus
        rights = "SERVICE_QUERY_STATUS"
        exactAceCount = $verifiedExact.Count
    }
}

function Protect-AdminDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-NoReparsePathChain -Path $Path
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
    Assert-NoReparsePathChain -Path $Path
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $security = New-Object System.Security.AccessControl.DirectorySecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($adminSid)
    $inherit = [System.Security.AccessControl.InheritanceFlags]::ContainerInherit -bor [System.Security.AccessControl.InheritanceFlags]::ObjectInherit
    foreach ($sid in @($systemSid, $adminSid)) {
        $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            $inherit,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )))
    }
    [System.IO.Directory]::SetAccessControl($Path, $security)
    $items = @(Get-ChildItem -LiteralPath $Path -Recurse -Force -ErrorAction Stop)
    $reparse = @($items | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($reparse.Count -gt 0) { throw "administrator directory tree contains a reparse point: $($reparse[0].FullName)" }
    foreach ($file in @($items | Where-Object { -not $_.PSIsContainer })) { Protect-AdminFile -Path $file.FullName }
    foreach ($directory in @($items | Where-Object { $_.PSIsContainer } | Sort-Object { $_.FullName.Length } -Descending)) {
        [System.IO.Directory]::SetAccessControl($directory.FullName, $security)
    }
    [System.IO.Directory]::SetAccessControl($Path, $security)
}

function Protect-AdminFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    Assert-RegularSingleLinkFile -Path $Path -Label "administrator file"
    $systemSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-18")
    $adminSid = New-Object System.Security.Principal.SecurityIdentifier("S-1-5-32-544")
    $security = New-Object System.Security.AccessControl.FileSecurity
    $security.SetAccessRuleProtection($true, $false)
    $security.SetOwner($adminSid)
    foreach ($sid in @($systemSid, $adminSid)) {
        $security.AddAccessRule((New-Object System.Security.AccessControl.FileSystemAccessRule(
            $sid,
            [System.Security.AccessControl.FileSystemRights]::FullControl,
            [System.Security.AccessControl.InheritanceFlags]::None,
            [System.Security.AccessControl.PropagationFlags]::None,
            [System.Security.AccessControl.AccessControlType]::Allow
        )))
    }
    [System.IO.File]::SetAccessControl($Path, $security)
}

function Invoke-Nssm {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    Assert-RegularSingleLinkFile -Path $StableNssm -Label "published NSSM"
    $executionHash = (Get-FileHash -LiteralPath $StableNssm -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($executionHash -ne $expectedHash) {
        throw "published NSSM hash changed immediately before execution"
    }
    & $StableNssm @Arguments | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "pinned NSSM failed with exit code $LASTEXITCODE"
    }
}

function Invoke-TrustedNssmCapture {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Destination
    )
    Assert-RegularSingleLinkFile -Path $Path -Label "rollback NSSM tool"
    if ((Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw "rollback NSSM tool failed its hash pin immediately before execution"
    }
    @(& $Path @Arguments 2>&1) | Set-Content -LiteralPath $Destination -Encoding UTF8
    return $LASTEXITCODE
}

function Save-ExistingServiceConfiguration {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$PinnedNssm = ""
    )
    $service = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
    if (-not $service) {
        "No existing service named $ServiceName." | Set-Content -LiteralPath (Join-Path $Path "$ServiceName.none.txt") -Encoding UTF8
        return
    }
    @(& "$env:SystemRoot\System32\sc.exe" qc $ServiceName 2>&1) | Set-Content -LiteralPath (Join-Path $Path "$ServiceName.sc-qc.txt") -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "failed to back up sc.exe qc output" }
    @(& "$env:SystemRoot\System32\sc.exe" queryex $ServiceName 2>&1) | Set-Content -LiteralPath (Join-Path $Path "$ServiceName.sc-queryex.txt") -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "failed to back up sc.exe queryex output" }
    @(& "$env:SystemRoot\System32\sc.exe" qsidtype $ServiceName 2>&1) | Set-Content -LiteralPath (Join-Path $Path "$ServiceName.sc-qsidtype.txt") -Encoding UTF8
    if ($LASTEXITCODE -ne 0) { throw "failed to back up sc.exe qsidtype output" }
    if ($PinnedNssm) {
        $dumpPath = Join-Path $Path "$ServiceName.nssm-dump.txt"
        $dumpCode = Invoke-TrustedNssmCapture -Path $PinnedNssm -Arguments @("dump", $ServiceName) -Destination $dumpPath
        if ($dumpCode -ne 0) {
            "Trusted NSSM could not dump this service; registry and SCM backups remain authoritative." |
                Set-Content -LiteralPath (Join-Path $Path "$ServiceName.nssm-dump.failed.txt") -Encoding UTF8
        }
    }
    else {
        "NSSM dump skipped because no already-installed hash-pinned NSSM was available." |
            Set-Content -LiteralPath (Join-Path $Path "$ServiceName.nssm-dump.unavailable.txt") -Encoding UTF8
    }
    & "$env:SystemRoot\System32\reg.exe" export "HKLM\SYSTEM\CurrentControlSet\Services\$ServiceName" (Join-Path $Path "$ServiceName.reg") /y | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "failed to export the existing service registry configuration" }
}

function Assert-AuthorityActivationReceipt {
    Assert-RegularSingleLinkFile -Path $activationReceiptPath -Label "broker authority activation receipt"
    $receipt = Get-Content -LiteralPath $activationReceiptPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if (
        [string]$receipt.schemaVersion -ne "fouler-lease-broker-activation/v1" -or
        $receipt.registered -ne $true -or
        -not [string]::Equals([string]$receipt.serviceName, $ServiceName, [System.StringComparison]::Ordinal) -or
        -not [string]::Equals([System.IO.Path]::GetFullPath([string]$receipt.projectDir).TrimEnd("\"), $resolvedProject, [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([System.IO.Path]::GetFullPath([string]$receipt.storePath), [System.IO.Path]::GetFullPath($storePath), [System.StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([System.IO.Path]::GetFullPath([string]$receipt.markerPath), [System.IO.Path]::GetFullPath($markerPath), [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "broker authority activation receipt does not bind this disabled service and release"
    }
    if ([string]$receipt.sourceCommit -ne (Split-Path -Leaf $resolvedProject)) {
        throw "broker authority activation receipt source commit does not match ProjectDir"
    }
    $manifestPath = [System.IO.Path]::GetFullPath([string]$receipt.releaseManifestPath)
    Assert-RegularSingleLinkFile -Path $manifestPath -Label "installed release bootstrap manifest"
    if ((Get-FileHash -LiteralPath $manifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$receipt.releaseManifestSha256) {
        throw "installed release bootstrap manifest no longer matches authority activation"
    }
    $manifest = Get-Content -LiteralPath $manifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$manifest.schemaVersion -ne "fouler-bootstrap-manifest/v1" -or [string]$manifest.projectId -ne "fouler-play" -or [string]$manifest.sourceCommit -ne (Split-Path -Leaf $resolvedProject) -or -not $manifest.files) {
        throw "installed release bootstrap manifest identity is invalid"
    }
    $expected = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($property in $manifest.files.PSObject.Properties) {
        $relative = ([string]$property.Name).Replace("\", "/")
        $digest = ([string]$property.Value).ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($relative) -or [System.IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)\.\.?(?:/|$)' -or $digest -notmatch '^[0-9a-f]{64}$' -or $expected.ContainsKey($relative)) {
            throw "installed release bootstrap manifest contains an unsafe or duplicate file entry"
        }
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $resolvedProject ($relative.Replace("/", "\"))))
        if (-not $candidate.StartsWith($resolvedProject + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "installed release manifest entry escapes ProjectDir" }
        $expected.Add($relative, $digest)
    }
    foreach ($required in @(".venv/Scripts/python.exe", "infrastructure/windows/fouler_lease_broker.py")) {
        if (-not $expected.ContainsKey($required)) { throw "installed release bootstrap manifest omits required broker file: $required" }
    }
    $releaseItems = @(Get-ChildItem -LiteralPath $resolvedProject -Recurse -Force -ErrorAction Stop)
    $releaseReparse = @($releaseItems | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($releaseReparse.Count -gt 0) { throw "release contains a reparse point: $($releaseReparse[0].FullName)" }
    $actualFiles = @($releaseItems | Where-Object { -not $_.PSIsContainer })
    if ($actualFiles.Count -ne $expected.Count) { throw "release file inventory no longer matches broker authority activation" }
    foreach ($file in $actualFiles) {
        Assert-RegularSingleLinkFile -Path $file.FullName -Label "manifested release file"
        $relative = $file.FullName.Substring($resolvedProject.Length + 1).Replace("\", "/")
        if (-not $expected.ContainsKey($relative)) { throw "release contains an unmanifested file: $relative" }
        if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected[$relative]) { throw "manifested release file hash changed: $relative" }
    }
    foreach ($requiredPath in @($storePath, $markerPath, [string]$receipt.registrationPath)) {
        Assert-RegularSingleLinkFile -Path $requiredPath -Label "registered broker authority artifact"
    }
    if ((Get-FileHash -LiteralPath ([string]$receipt.registrationPath) -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$receipt.registrationSha256) {
        throw "broker registration artifact no longer matches authority activation"
    }
    return $receipt
}

function Get-CompetingBrokerProcesses {
    return @(Get-CimInstance Win32_Process -Filter "name like 'python%'" -ErrorAction SilentlyContinue | Where-Object {
        $commandLine = [string]$_.CommandLine
        -not [string]::IsNullOrWhiteSpace($commandLine) -and
        $commandLine -match '(?i)fouler_lease_broker\.py' -and
        $commandLine -match '(?i)(?:^|\s)serve(?:\s|$)'
    })
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

function Resolve-CommandPathToken {
    param([string]$Token, [string]$BasePath)
    if ([string]::IsNullOrWhiteSpace($Token)) { return "" }
    try {
        $candidate = $Token.Trim().Trim('"')
        if (-not [System.IO.Path]::IsPathRooted($candidate)) { $candidate = Join-Path $BasePath $candidate }
        return [System.IO.Path]::GetFullPath($candidate)
    }
    catch { return "" }
}

function Assert-RunningBrokerProcessIdentity {
    $serviceRecord = Get-CimInstance Win32_Service -Filter "Name = '$ServiceName'" -ErrorAction Stop
    if (-not $serviceRecord -or [string]$serviceRecord.State -ne "Running" -or [int64]$serviceRecord.ProcessId -le 0) {
        throw "broker service did not expose a running SCM process identity"
    }
    $serviceProcess = Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f [int64]$serviceRecord.ProcessId) -ErrorAction Stop
    if (-not $serviceProcess -or -not [string]::Equals([System.IO.Path]::GetFullPath([string]$serviceProcess.ExecutablePath), [System.IO.Path]::GetFullPath($StableNssm), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "broker SCM process is not the canonical hash-pinned NSSM executable"
    }
    Assert-RegularSingleLinkFile -Path $StableNssm -Label "running broker NSSM"
    if ((Get-FileHash -LiteralPath $StableNssm -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
        throw "running broker NSSM failed its SHA-256 identity pin"
    }

    $children = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = {0}" -f [int64]$serviceRecord.ProcessId) -ErrorAction Stop)
    if ($children.Count -ne 1) {
        throw "broker service must own exactly one direct release-venv child process"
    }
    $brokerProcess = $children[0]
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$brokerProcess.ExecutablePath), [System.IO.Path]::GetFullPath($python), [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "broker child process is not the pinned immutable-release venv Python"
    }
    $tokens = @(Split-WindowsCommandLine -CommandLine ([string]$brokerProcess.CommandLine))
    if ($tokens.Count -ne 11) { throw "broker child command line has an unexpected shape" }
    if (-not [string]::Equals((Resolve-CommandPathToken -Token $tokens[0] -BasePath $resolvedProject), $python, [System.StringComparison]::OrdinalIgnoreCase)) { throw "broker child command executable token is not pinned" }
    if ($tokens[1] -cne "-I" -or $tokens[2] -cne "-B") { throw "broker child must use isolated no-bytecode Python mode" }
    if (-not [string]::Equals((Resolve-CommandPathToken -Token $tokens[3] -BasePath $resolvedProject), $entrypoint, [System.StringComparison]::OrdinalIgnoreCase)) { throw "broker child entrypoint is not release-pinned" }
    $expectedTail = @(
        "--store-path", $storePath,
        "--marker-path", $markerPath,
        "serve",
        "--runtime-sid", $runtimeSid.Value
    )
    for ($index = 0; $index -lt $expectedTail.Count; $index++) {
        if (-not [string]::Equals([string]$tokens[$index + 4], [string]$expectedTail[$index], [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "broker child command line differs from the canonical authority binding"
        }
    }
    return [pscustomobject]@{
        serviceProcessId = [int64]$serviceRecord.ProcessId
        brokerProcessId = [int64]$brokerProcess.ProcessId
        python = $python
        projectDir = $resolvedProject
    }
}

$resolvedProject = [System.IO.Path]::GetFullPath($ProjectDir).TrimEnd("\")
if ($resolvedProject -notmatch '^D:\\Releases\\fouler-play\\[0-9a-f]{40}$') {
    throw "ProjectDir must be an immutable D:\Releases\fouler-play\<commit> release"
}
$projectItem = Get-Item -LiteralPath $resolvedProject -Force
if (-not $projectItem.PSIsContainer -or (($projectItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) {
    throw "ProjectDir must be a non-reparse-point directory"
}
Assert-NoReparsePathChain -Path $resolvedProject
$installerPath = [System.IO.Path]::GetFullPath([string]$MyInvocation.MyCommand.Path)
if ($installerPath -match '^D:\\Releases\\fouler-play\\') { throw "broker installer must execute from the trusted control plane, never a release tree" }
Assert-NoReparsePathChain -Path $installerPath
Assert-RegularSingleLinkFile -Path $installerPath -Label "broker installer"
Assert-NoPathOverlap -First $resolvedProject -Second $installerPath -Label "broker installer path"
foreach ($externalRoot in @($BrokerRoot, $BackupRoot, (Split-Path -Parent $StableNssm), $AuthorityRoot, $NssmSource)) {
    Assert-NoPathOverlap -First $resolvedProject -Second $externalRoot -Label "broker publication path"
}

$python = Join-Path $resolvedProject ".venv\Scripts\python.exe"
$entrypoint = Join-Path $resolvedProject "infrastructure\windows\fouler_lease_broker.py"
if (-not (Test-Path -LiteralPath $python -PathType Leaf) -or -not (Test-Path -LiteralPath $entrypoint -PathType Leaf)) {
    throw "immutable release Python or lease broker entrypoint is missing"
}

$expectedHash = $ExpectedNssmSha256.ToLowerInvariant()
$nssmSnapshot = Get-RegularFileSnapshot -Path $NssmSource -Label "NSSM source" -MaxBytes 16777216
if ($nssmSnapshot.Sha256 -ne $expectedHash) {
    throw "NSSM source SHA-256 does not match ExpectedNssmSha256"
}

$runtimeSid = Get-Sid -Account $RuntimeAccount
$storePath = Join-Path $BrokerRoot "consumption.sqlite3"
$markerPath = Join-Path $BrokerRoot "consumption.sqlite3.initialized"
$logRoot = Join-Path $BrokerRoot "logs"
$tempRoot = Join-Path $BrokerRoot "tmp"
$stdoutLog = Join-Path $logRoot "broker.stdout.log"
$stderrLog = Join-Path $logRoot "broker.stderr.log"
$activationReceiptPath = Join-Path $AuthorityRoot ("broker-activations\" + (Split-Path -Leaf $resolvedProject) + ".json")
$plan = [ordered]@{
    schemaVersion = "fouler-lease-broker-install/v1"
    apply = [bool]$Apply
    projectDir = $resolvedProject
    serviceName = $ServiceName
    serviceAccount = "NT AUTHORITY\LocalService"
    serviceSidType = "unrestricted"
    runtimeAccount = $RuntimeAccount
    runtimeSid = $runtimeSid.Value
    runtimeDatabaseAccess = $false
    pipeName = "\\.\pipe\HERMES.FoulerLeaseBroker.v1"
    pipeRuntimeRights = "FILE_READ_DATA|FILE_WRITE_DATA"
    brokerRoot = $BrokerRoot
    storePath = $storePath
    markerPath = $markerPath
    activationReceiptPath = $activationReceiptPath
    expectedNssmSha256 = $expectedHash
    observedNssmSha256 = $nssmSnapshot.Sha256
    startsBroker = [bool]($StartBroker -or $RestartBroker)
    installsDisabled = $true
    startsBattles = $false
    startsStreaming = $false
    mutatesBattleTasks = $false
}
if (-not $Apply) {
    $plan.status = "dry-run"
    $plan | ConvertTo-Json -Depth 6
    exit 0
}

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = New-Object Security.Principal.WindowsPrincipal($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "Apply requires an elevated administrator PowerShell session"
}

$stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ") + "-" + [guid]::NewGuid().ToString("N")
$backupDirectory = Join-Path $BackupRoot $stamp
Protect-AdminDirectory -Path $BackupRoot
Protect-AdminDirectory -Path $backupDirectory
Protect-AdminDirectory -Path (Split-Path -Parent $StableNssm)

$safeBackupNssm = Join-Path $backupDirectory "nssm.rollback-tool.exe"
[IO.File]::WriteAllBytes($safeBackupNssm, $nssmSnapshot.Bytes)
Protect-AdminFile -Path $safeBackupNssm
if ((Get-FileHash -LiteralPath $safeBackupNssm -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expectedHash) {
    throw "trusted rollback NSSM snapshot failed its mandatory SHA-256 pin"
}
if (Test-Path -LiteralPath $StableNssm -PathType Leaf) {
    $stableSnapshot = Get-RegularFileSnapshot -Path $StableNssm -Label "installed NSSM" -MaxBytes 16777216
    $previousNssmBackup = Join-Path $backupDirectory "nssm.previous.exe"
    [IO.File]::WriteAllBytes($previousNssmBackup, $stableSnapshot.Bytes)
    Protect-AdminFile -Path $previousNssmBackup
    if ((Get-FileHash -LiteralPath $previousNssmBackup -Algorithm SHA256).Hash.ToLowerInvariant() -ne $stableSnapshot.Sha256) {
        throw "installed NSSM rollback backup differs from its pre-mutation snapshot"
    }
}

# Never execute the mutable source path or the previously installed binary.
# Rollback capture uses only freshly published bytes from the external hash pin.
Save-ExistingServiceConfiguration -Path $backupDirectory -PinnedNssm $safeBackupNssm
$existingService = Get-Service -Name $ServiceName -ErrorAction SilentlyContinue
$wasRunning = [bool]($existingService -and [string]$existingService.Status -eq "Running")
if ($wasRunning -and -not $RestartBroker) {
    throw "existing broker service is running; use -RestartBroker to authorize a broker-only restart"
}
if ($wasRunning) {
    Stop-Service -Name $ServiceName -Force
    (Get-Service -Name $ServiceName).WaitForStatus("Stopped", (New-TimeSpan -Seconds 30))
}
$competingBrokers = @()
for ($attempt = 0; $attempt -lt 30; $attempt++) {
    $competingBrokers = @(Get-CompetingBrokerProcesses)
    if ($competingBrokers.Count -eq 0) { break }
    Start-Sleep -Seconds 1
}
if ($competingBrokers.Count -gt 0) {
    throw "stale or competing Fouler lease broker process survived outside the stopped service: $(@($competingBrokers.ProcessId) -join ', ')"
}

Write-AtomicBytes -Bytes $nssmSnapshot.Bytes -Destination $StableNssm
Protect-AdminFile -Path $StableNssm
$installedHash = (Get-FileHash -LiteralPath $StableNssm -Algorithm SHA256).Hash.ToLowerInvariant()
if ($installedHash -ne $expectedHash) {
    throw "installed NSSM failed the mandatory SHA-256 pin after publication"
}

$sc = "$env:SystemRoot\System32\sc.exe"
# Rollback-safe existing-service migration. Save-ExistingServiceConfiguration above
# already captured any prior service (sc qc/queryex, reg export, nssm dump) into the
# backup directory, and any running instance was stopped above under the
# -RestartBroker authorization. A legacy broker service created by the retired
# plain-sc.exe path is NOT NSSM-managed, so every subsequent "nssm set" would fail
# were it reused (the original defect). Rather than positively re-proving an opaque
# prior service, always delete it -- only after the backup -- and reinstall cleanly
# through NSSM. Deletion after the backup keeps the operation rollback-safe.
if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
    & $sc delete $ServiceName 2>&1 | Out-Null
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        if (-not (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue)) { break }
        Start-Sleep -Seconds 1
    }
    if (Get-Service -Name $ServiceName -ErrorAction SilentlyContinue) {
        throw "existing broker service '$ServiceName' could not be removed before NSSM reinstallation"
    }
}
# NSSM performs the installation so that the mutable "nssm set" configuration below
# is accepted; a plain sc.exe-created service is rejected by every "nssm set".
Invoke-Nssm -Arguments @("install", $ServiceName, $python)
# Immediately pin least-privilege identity + disabled start BEFORE any mutable NSSM
# application/config setting: Disabled + LocalService, then the unrestricted SID.
& $sc config $ServiceName "start=" "disabled" "obj=" "NT AUTHORITY\LocalService" | Out-Null
if ($LASTEXITCODE -ne 0) { throw "failed to force the broker service into Disabled LocalService state" }
& $sc sidtype $ServiceName unrestricted | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "failed to configure an unrestricted service SID"
}
$serviceSid = Get-Sid -Account "NT SERVICE\$ServiceName"

# Broker storage is protected to SYSTEM, Administrators, and this service SID.
# The runtime SID is deliberately absent and receives only named-pipe data rights.
Protect-DirectoryTree -Path $BrokerRoot -ServiceSid $serviceSid
Protect-DirectoryTree -Path $logRoot -ServiceSid $serviceSid
Protect-DirectoryTree -Path $tempRoot -ServiceSid $serviceSid
Protect-DirectoryTree -Path (Split-Path -Parent $StableNssm) -ServiceSid $serviceSid -ServiceReadOnly
$stableFileSecurity = New-ProtectedFileSecurity -ServiceSid $serviceSid -ServiceReadOnly
[System.IO.File]::SetAccessControl($StableNssm, $stableFileSecurity)

$arguments = "-I -B `"$entrypoint`" --store-path `"$storePath`" --marker-path `"$markerPath`" serve --runtime-sid $($runtimeSid.Value)"
Invoke-Nssm -Arguments @("set", $ServiceName, "Application", $python)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppParameters", $arguments)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppDirectory", $resolvedProject)
Invoke-Nssm -Arguments @("set", $ServiceName, "DisplayName", "HERMES Fouler Lease Broker")
Invoke-Nssm -Arguments @("set", $ServiceName, "Description", "Local named-pipe broker for append-only Fouler runtime lease consumption")
Invoke-Nssm -Arguments @("set", $ServiceName, "ObjectName", "NT AUTHORITY\LocalService")
Invoke-Nssm -Arguments @("set", $ServiceName, "Start", "SERVICE_DISABLED")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppExit", "Default", "Exit")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppNoConsole", "1")
Invoke-Nssm -Arguments @(
    "set", $ServiceName, "AppEnvironmentExtra",
    "PYTHONDONTWRITEBYTECODE=1", "PYTHONUTF8=1", "GIT_OPTIONAL_LOCKS=0", "TEMP=$tempRoot", "TMP=$tempRoot"
)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppStdout", $stdoutLog)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppStderr", $stderrLog)
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateFiles", "1")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateOnline", "1")
Invoke-Nssm -Arguments @("set", $ServiceName, "AppRotateBytes", "10485760")
& $sc sidtype $ServiceName unrestricted | Out-Null
if ($LASTEXITCODE -ne 0) { throw "service SID type verification failed" }
$runtimeServiceAccess = Grant-RuntimeServiceQueryStatus -Name $ServiceName -RuntimeSid $runtimeSid
$preparedService = Get-Service -Name $ServiceName
if ([string]$preparedService.Status -ne "Stopped" -or [string]$preparedService.StartType -ne "Disabled") {
    throw "broker publication must remain stopped and Disabled until authority activation"
}

if ($StartBroker -or $RestartBroker) {
    $authorityActivation = Assert-AuthorityActivationReceipt
    Invoke-Nssm -Arguments @("set", $ServiceName, "Start", "SERVICE_AUTO_START")
    Start-Service -Name $ServiceName
    (Get-Service -Name $ServiceName).WaitForStatus("Running", (New-TimeSpan -Seconds 30))
    try {
        $brokerProcessIdentity = Assert-RunningBrokerProcessIdentity
    }
    catch {
        Stop-Service -Name $ServiceName -Force -ErrorAction SilentlyContinue
        throw
    }
}

$service = Get-Service -Name $ServiceName
$plan.status = "installed"
$plan.backupDirectory = $backupDirectory
$plan.serviceSid = $serviceSid.Value
$plan.runtimeServiceAccess = $runtimeServiceAccess
$plan.nssmSha256 = $installedHash
$plan.serviceState = [string]$service.Status
$plan.storeInitialized = [bool](Test-Path -LiteralPath $storePath -PathType Leaf)
$plan.markerInitialized = [bool](Test-Path -LiteralPath $markerPath -PathType Leaf)
$plan.authorityActivated = [bool]($StartBroker -or $RestartBroker)
if ($StartBroker -or $RestartBroker) {
    $plan.processIdentity = $brokerProcessIdentity
}
$plan.rollback = "Stop only $ServiceName, then restore the exported service registry backup or trusted NSSM dump. The release ACL is owned solely by install_runtime_authority.ps1."
$plan | ConvertTo-Json -Depth 6
