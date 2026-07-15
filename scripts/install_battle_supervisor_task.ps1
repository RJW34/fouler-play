# SAFETY: This installer is not a default launcher. Register or start this persistent
# task only with a current Fouler proof window and runtime lease; normal onboarding
# must leave scheduled tasks disabled and use status/dry-run commands.
param(
    [switch]$Apply,
    [switch]$Start,
    [switch]$Stop,
    [switch]$Status,
    [switch]$Uninstall,
    [string]$ProjectDir = "",
    [string]$TaskName = "HERMES-FoulerBattleSupervisor",
    [int]$RunCount = 0,
    [int]$MaxConcurrentBattles = 3,
    [int]$SearchParallelism = 2,
    [int]$MaxCycles = 0,
    [int]$QueueTimeoutSeconds = 180,
    [int]$SleepSeconds = 15,
    [string]$RuntimeLease = "",
    [string]$RuntimeAccount = "JIGGLYPUFF\devstream-live",
    [string]$ObserverAccount = "NT SERVICE\HERMES-FoulerObsServer",
    [string]$RuntimeStateRoot = "C:\ProgramData\HERMES\state\fouler",
    [string]$RuntimeLogRoot = "C:\ProgramData\HERMES\logs\fouler",
    [string]$RuntimeCacheRoot = "C:\ProgramData\HERMES\cache\fouler",
    [string]$AuthorityRoot = "C:\ProgramData\HERMES\authority\fouler",
    [string]$AccountSeasonPath = "C:\ProgramData\HERMES\authority\fouler\account-season.json",
    [string]$SecretEnvFile = "C:\ProgramData\HERMES\secrets\fouler.env",
    [ValidateSet("0", "1")]
    [string]$LoopBreak = "0",
    [switch]$AutoImprove
)

$ErrorActionPreference = "Stop"
$CanonicalTaskName = "HERMES-FoulerBattleSupervisor"
$CanonicalAuthorityRoot = "C:\ProgramData\HERMES\authority\fouler"
$CanonicalAccountSeasonPath = "C:\ProgramData\HERMES\authority\fouler\account-season.json"
$CanonicalRuntimeAccount = "JIGGLYPUFF\devstream-live"
$CanonicalObserverAccount = "NT SERVICE\HERMES-FoulerObsServer"
$PilotMaxConcurrentBattles = 3
$PilotSearchParallelism = 2
if (-not [string]::Equals($TaskName, $CanonicalTaskName, [System.StringComparison]::Ordinal)) {
    throw "TaskName must equal the exact Fouler supervisor task name: $CanonicalTaskName"
}
if (-not [string]::Equals($RuntimeAccount, $CanonicalRuntimeAccount, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "RuntimeAccount must equal the canonical Fouler runtime identity: $CanonicalRuntimeAccount"
}
if (-not [string]::Equals($ObserverAccount, $CanonicalObserverAccount, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "ObserverAccount must equal the canonical Fouler observer service identity: $CanonicalObserverAccount"
}
if (-not [string]::Equals([System.IO.Path]::GetFullPath($AuthorityRoot).TrimEnd("\"), $CanonicalAuthorityRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "AuthorityRoot must equal the canonical Fouler authority root: $CanonicalAuthorityRoot"
}
if (-not [string]::Equals([System.IO.Path]::GetFullPath($AccountSeasonPath), $CanonicalAccountSeasonPath, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "AccountSeasonPath must equal the canonical protected Fouler account-season authority"
}
if ($MaxConcurrentBattles -ne $PilotMaxConcurrentBattles) {
    throw "owner-locked live pilot MaxConcurrentBattles must equal 3"
}
if ($SearchParallelism -ne $PilotSearchParallelism) {
    throw "owner-locked live pilot SearchParallelism must equal 2"
}
$AuthorityRoot = $CanonicalAuthorityRoot
$AccountSeasonPath = $CanonicalAccountSeasonPath
$env:FOULER_ACCOUNT_SEASON_PATH = $AccountSeasonPath
$env:SEARCH_PARALLELISM = "2"
$ProjectDirWasExplicit = -not [string]::IsNullOrWhiteSpace($ProjectDir)
$ProjectDir = if ($ProjectDirWasExplicit) {
    [System.IO.Path]::GetFullPath($ProjectDir).TrimEnd("\")
} else {
    (Resolve-Path "$PSScriptRoot\..").Path.TrimEnd("\")
}
$BackupRoot = "C:\ProgramData\HERMES\backups\fouler-battle-supervisor-task"
$LogRoot = $RuntimeLogRoot
$PowerShell = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"
if (-not (Test-Path -LiteralPath $PowerShell -PathType Leaf)) {
    throw "exact Windows PowerShell executable is missing: $PowerShell"
}
$Py = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $Py -PathType Leaf)) {
    throw "exact release venv Python is missing: $Py"
}
$TaskWrapper = "scripts\start_battle_supervisor_task.ps1"
$TaskWrapperPath = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir $TaskWrapper))
$StdoutLog = Join-Path $LogRoot "jigglypuff-battle-supervisor.log"
$StderrLog = Join-Path $LogRoot "jigglypuff-battle-supervisor.err.log"
$AutoImproveArg = if ($AutoImprove) { " -AutoImprove" } else { "" }
$ResolvedRuntimeLease = if ([string]::IsNullOrWhiteSpace($RuntimeLease)) {
    "C:\ProgramData\HERMES\authority\fouler\runtime-lease.json"
} elseif ([System.IO.Path]::IsPathRooted($RuntimeLease)) {
    [System.IO.Path]::GetFullPath($RuntimeLease)
} else {
    [System.IO.Path]::GetFullPath((Join-Path $ProjectDir $RuntimeLease))
}
$RuntimeLease = $ResolvedRuntimeLease
$RuntimeLeaseArg = ' -RuntimeLease "{0}"' -f ($ResolvedRuntimeLease -replace '"', '\"')
$AccountSeasonArg = ' -AccountSeasonPath "{0}"' -f ($AccountSeasonPath -replace '"', '\"')
$RuntimeStateArg = ' -RuntimeStateRoot "{0}"' -f ($RuntimeStateRoot -replace '"', '\"')
$RuntimeLogArg = ' -RuntimeLogRoot "{0}"' -f ($RuntimeLogRoot -replace '"', '\"')
$RuntimeCacheArg = ' -RuntimeCacheRoot "{0}"' -f ($RuntimeCacheRoot -replace '"', '\"')
$SecretEnvArg = ' -SecretEnvFile "{0}"' -f ($SecretEnvFile -replace '"', '\"')
$TaskArguments = '/d /c ""{0}" -NoProfile -ExecutionPolicy Bypass -File "{1}" -RunCount {2} -MaxConcurrentBattles {3} -SearchParallelism {4} -MaxCycles {5} -QueueTimeoutSeconds {6} -SleepSeconds {7} -LoopBreak {8}{9}{10}{11}{12}{13}{14}{15} -Foreground"' -f $PowerShell, $TaskWrapperPath, $RunCount, $MaxConcurrentBattles, $SearchParallelism, $MaxCycles, $QueueTimeoutSeconds, $SleepSeconds, $LoopBreak, $AccountSeasonArg, $RuntimeLeaseArg, $RuntimeStateArg, $RuntimeLogArg, $RuntimeCacheArg, $SecretEnvArg, $AutoImproveArg
$PidFile = Join-Path $RuntimeStateRoot "pids\devstream_battle_supervisor.pid"
$StopFile = Join-Path $RuntimeStateRoot "pids\supervisor.stop"
$SupervisorScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir "scripts\devstream_session.py"))
$BoundedSessionScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir "scripts\run_bounded_battle_session.py"))
$LadderScript = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir "run.py"))
$ReleaseCommit = Split-Path -Leaf $ProjectDir
$ReleaseManifestPath = Join-Path $AuthorityRoot ("releases\$ReleaseCommit\bootstrap-manifest.json")
$BrokerActivationPath = Join-Path $AuthorityRoot ("broker-activations\$ReleaseCommit.json")
$TaskKill = Join-Path $env:SystemRoot "System32\taskkill.exe"
if (-not (Test-Path -LiteralPath $TaskKill -PathType Leaf)) {
    throw "exact taskkill.exe is missing: $TaskKill"
}
$TaskExecute = Join-Path $env:SystemRoot "System32\cmd.exe"
if (-not (Test-Path -LiteralPath $TaskExecute -PathType Leaf)) {
    throw "exact cmd.exe is missing: $TaskExecute"
}

if (-not ("FoulerSupervisorInstaller.NativeFileIdentity" -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;
namespace FoulerSupervisorInstaller {
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
    param([string]$Account)
    return (New-Object System.Security.Principal.NTAccount($Account)).Translate([System.Security.Principal.SecurityIdentifier])
}

function Assert-RegularSingleLinkFile {
    param([string]$Path, [string]$Label)
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if ($item.PSIsContainer -or (($item.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0)) { throw "$Label must be a regular non-reparse-point file" }
    $stream = [System.IO.File]::Open($item.FullName, [System.IO.FileMode]::Open, [System.IO.FileAccess]::Read, ([System.IO.FileShare]::ReadWrite -bor [System.IO.FileShare]::Delete))
    try {
        $information = New-Object FoulerSupervisorInstaller.ByHandleFileInformation
        if (-not [FoulerSupervisorInstaller.NativeFileIdentity]::GetFileInformationByHandle($stream.SafeFileHandle, [ref]$information)) { throw "$Label file identity could not be read" }
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
    if ([string]::Equals($left, $right, [System.StringComparison]::OrdinalIgnoreCase) -or $left.StartsWith($right + "\", [System.StringComparison]::OrdinalIgnoreCase) -or $right.StartsWith($left + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "$Label must not equal, contain, or be contained by ProjectDir" }
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
    param([Parameter(Mandatory = $true)][string]$Path)
    Set-ExactDirectoryTreeDacl -Path $Path -RightsBySid @{
        "S-1-5-18" = [System.Security.AccessControl.FileSystemRights]::FullControl
        "S-1-5-32-544" = [System.Security.AccessControl.FileSystemRights]::FullControl
    }
}

function Save-TaskBackup {
    param([string]$Name)
    Protect-AdminDirectory -Path $BackupRoot
    $stamp = [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffZ") + "-" + [guid]::NewGuid().ToString("N")
    $task = Get-ScheduledTask -TaskName $Name -ErrorAction SilentlyContinue
    if ($task) {
        $path = Join-Path $BackupRoot "$Name-$stamp.xml"
        Export-ScheduledTask -TaskName $Name | Set-Content -LiteralPath $path -Encoding UTF8
        return $path
    }
    $path = Join-Path $BackupRoot "$Name-$stamp.none.txt"
    "No existing task named $Name at $((Get-Date).ToUniversalTime().ToString('o'))." | Set-Content -LiteralPath $path -Encoding UTF8
    return $path
}

function Rotate-LogFile {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return $null }
    $archive = Join-Path (Split-Path -Parent $Path) "archive"
    New-Item -ItemType Directory -Force -Path $archive | Out-Null
    $stamp = Get-Date -Format "yyyyMMddTHHmmss"
    $dest = Join-Path $archive "$stamp-$([IO.Path]::GetFileName($Path))"
    Move-Item -LiteralPath $Path -Destination $dest -Force
    return $dest
}

function Split-WindowsCommandLine {
    param([string]$CommandLine)
    if ([string]::IsNullOrWhiteSpace($CommandLine)) { return @() }
    $tokens = @()
    foreach ($match in [regex]::Matches($CommandLine, '(?:"(?<quoted>(?:[^"\\]|\\.)*)"|(?<bare>\S+))')) {
        if ($match.Groups["quoted"].Success) {
            $tokens += $match.Groups["quoted"].Value
        } else {
            $tokens += $match.Groups["bare"].Value
        }
    }
    return $tokens
}

function Protect-RuntimeWriteDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $rights = @{
        "S-1-5-18" = [System.Security.AccessControl.FileSystemRights]::FullControl
        "S-1-5-32-544" = [System.Security.AccessControl.FileSystemRights]::FullControl
    }
    $rights[(Get-Sid -Account $RuntimeAccount).Value] = [System.Security.AccessControl.FileSystemRights]::Modify
    $rights[(Get-Sid -Account $ObserverAccount).Value] = [System.Security.AccessControl.FileSystemRights]::Modify
    Set-ExactDirectoryTreeDacl -Path $Path -RightsBySid $rights
}

function Protect-SecretFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "protected Fouler secret environment file is missing: $Path"
    }
    $rights = @{
        "S-1-5-18" = [System.Security.AccessControl.FileSystemRights]::FullControl
        "S-1-5-32-544" = [System.Security.AccessControl.FileSystemRights]::FullControl
    }
    $rights[(Get-Sid -Account $RuntimeAccount).Value] = [System.Security.AccessControl.FileSystemRights]::Read
    Set-ExactFileDacl -Path $Path -RightsBySid $rights
}

function Assert-ManifestedImmutableRelease {
    if ($ProjectDir -notmatch '^D:\\Releases\\fouler-play\\[0-9a-f]{40}$') { throw "ProjectDir must be an immutable D:\Releases\fouler-play\<commit> release" }
    Assert-NoReparsePathChain -Path $ProjectDir
    Assert-RegularSingleLinkFile -Path $BrokerActivationPath -Label "broker activation receipt"
    $activation = Get-Content -LiteralPath $BrokerActivationPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$activation.schemaVersion -ne "fouler-lease-broker-activation/v1" -or $activation.registered -ne $true -or [string]$activation.sourceCommit -ne $ReleaseCommit) { throw "authority activation does not bind this immutable release" }
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$activation.projectDir).TrimEnd("\"), $ProjectDir, [System.StringComparison]::OrdinalIgnoreCase)) { throw "authority activation ProjectDir differs from this release" }
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$activation.releaseManifestPath), [System.IO.Path]::GetFullPath($ReleaseManifestPath), [System.StringComparison]::OrdinalIgnoreCase)) { throw "authority activation names a different release manifest" }
    Assert-RegularSingleLinkFile -Path $ReleaseManifestPath -Label "release bootstrap manifest"
    if ((Get-FileHash -LiteralPath $ReleaseManifestPath -Algorithm SHA256).Hash.ToLowerInvariant() -ne [string]$activation.releaseManifestSha256) { throw "release bootstrap manifest hash differs from authority activation" }
    $manifest = Get-Content -LiteralPath $ReleaseManifestPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ([string]$manifest.schemaVersion -ne "fouler-bootstrap-manifest/v1" -or [string]$manifest.projectId -ne "fouler-play" -or [string]$manifest.sourceCommit -ne $ReleaseCommit -or -not $manifest.files) { throw "release bootstrap manifest identity is invalid" }
    $expected = New-Object 'System.Collections.Generic.Dictionary[string,string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($property in $manifest.files.PSObject.Properties) {
        $relative = ([string]$property.Name).Replace("\", "/")
        $digest = ([string]$property.Value).ToLowerInvariant()
        if ([string]::IsNullOrWhiteSpace($relative) -or [System.IO.Path]::IsPathRooted($relative) -or $relative -match '(^|/)\.\.?(?:/|$)' -or $digest -notmatch '^[0-9a-f]{64}$' -or $expected.ContainsKey($relative)) { throw "release bootstrap manifest contains an unsafe or duplicate file entry" }
        $candidate = [System.IO.Path]::GetFullPath((Join-Path $ProjectDir ($relative.Replace("/", "\"))))
        if (-not $candidate.StartsWith($ProjectDir + "\", [System.StringComparison]::OrdinalIgnoreCase)) { throw "release manifest entry escapes ProjectDir" }
        $expected.Add($relative, $digest)
    }
    foreach ($required in @(".venv/Scripts/python.exe", "scripts/start_battle_supervisor_task.ps1", "scripts/devstream_runtime_lease.py", "scripts/devstream_session.py", "scripts/run_bounded_battle_session.py", "run.py")) {
        if (-not $expected.ContainsKey($required)) { throw "release bootstrap manifest omits required supervisor file: $required" }
    }
    $releaseItems = @(Get-ChildItem -LiteralPath $ProjectDir -Recurse -Force -ErrorAction Stop)
    $releaseReparse = @($releaseItems | Where-Object { ($_.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0 })
    if ($releaseReparse.Count -gt 0) { throw "release contains a reparse point: $($releaseReparse[0].FullName)" }
    $actualFiles = @($releaseItems | Where-Object { -not $_.PSIsContainer })
    if ($actualFiles.Count -ne $expected.Count) { throw "release file inventory no longer matches the bootstrap manifest" }
    foreach ($file in $actualFiles) {
        Assert-RegularSingleLinkFile -Path $file.FullName -Label "manifested release file"
        $relative = $file.FullName.Substring($ProjectDir.Length + 1).Replace("\", "/")
        if (-not $expected.ContainsKey($relative)) { throw "release contains an unmanifested file: $relative" }
        if ((Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected[$relative]) { throw "manifested release file hash changed: $relative" }
    }
    Assert-NoReparsePathChain -Path $AccountSeasonPath
    Assert-RegularSingleLinkFile -Path $AccountSeasonPath -Label "canonical account-season authority"
    $accountSeasonAttributes = [System.IO.File]::GetAttributes($AccountSeasonPath)
    if (($accountSeasonAttributes -band [System.IO.FileAttributes]::ReadOnly) -eq 0) {
        throw "canonical account-season authority must have the read-only attribute"
    }
    return $activation
}

function Resolve-CommandPathToken {
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

function Test-CommandSwitch {
    param([string[]]$Tokens, [string]$Name)
    return @($Tokens | Where-Object {
        [string]::Equals($_, $Name, [System.StringComparison]::OrdinalIgnoreCase)
    }).Count -eq 1
}

function Test-PositiveCommandInteger {
    param([string[]]$Tokens, [string]$Name)
    $values = @(Get-CommandArgumentValues -Tokens $Tokens -Name $Name)
    if ($values.Count -ne 1) { return $false }
    $parsed = 0
    return [int]::TryParse([string]$values[0], [ref]$parsed) -and $parsed -gt 0
}

function Test-AllowedCommandTail {
    param(
        [string[]]$Tokens,
        [int]$StartIndex,
        [string[]]$ValueNames,
        [string[]]$SwitchNames
    )
    $index = $StartIndex
    while ($index -lt $Tokens.Count) {
        $token = [string]$Tokens[$index]
        if ($SwitchNames -contains $token) {
            $index += 1
            continue
        }
        $inlineName = $ValueNames | Where-Object {
            $token.StartsWith("$_=", [System.StringComparison]::OrdinalIgnoreCase)
        } | Select-Object -First 1
        if ($inlineName) {
            $index += 1
            continue
        }
        if ($ValueNames -contains $token) {
            if ($index + 1 -ge $Tokens.Count) { return $false }
            $index += 2
            continue
        }
        return $false
    }
    return $true
}

function Test-ExactProcessExecutable {
    param($Process, [string]$ExpectedPath)
    try {
        $executable = [System.IO.Path]::GetFullPath("$($Process.ExecutablePath)".Trim())
        return [string]::Equals($executable, [System.IO.Path]::GetFullPath($ExpectedPath), [System.StringComparison]::OrdinalIgnoreCase)
    }
    catch { return $false }
}

function Test-PinnedPythonCommandPrefix {
    param($Process, [string[]]$Tokens, [int]$ScriptIndex, [switch]$AllowDirectScript)
    if (-not (Test-ExactProcessExecutable -Process $Process -ExpectedPath $Py)) { return $false }
    if ($Tokens.Count -le $ScriptIndex -or -not [string]::Equals((Resolve-CommandPathToken -Token $Tokens[0]), $Py, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if ($ScriptIndex -eq 3) {
        return $Tokens[1] -ceq "-I" -and $Tokens[2] -ceq "-B"
    }
    return $AllowDirectScript -and $ScriptIndex -eq 1
}

function Find-ExactScriptTokenIndex {
    param([string[]]$Tokens, [string]$ExpectedPath)
    for ($index = 1; $index -lt $Tokens.Count; $index++) {
        $resolved = Resolve-CommandPathToken -Token $Tokens[$index]
        if ([string]::Equals($resolved, $ExpectedPath, [System.StringComparison]::OrdinalIgnoreCase)) {
            return $index
        }
    }
    return -1
}

function Test-BattleSupervisorProcessOwnership {
    param($Process)
    if (-not $Process -or [int64]$Process.ProcessId -le 0) { return $false }
    if ([int64]$Process.ProcessId -eq [int64]$PID) { return $false }
    if (-not [string]::Equals([string]$Process.Name, "python.exe", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $tokens = @(Split-WindowsCommandLine -CommandLine ([string]$Process.CommandLine))
    $scriptIndex = Find-ExactScriptTokenIndex -Tokens $tokens -ExpectedPath $SupervisorScript
    if ($scriptIndex -ne 3 -or $scriptIndex + 1 -ge $tokens.Count) { return $false }
    if (-not (Test-PinnedPythonCommandPrefix -Process $Process -Tokens $tokens -ScriptIndex $scriptIndex)) { return $false }
    if (-not [string]::Equals($tokens[$scriptIndex + 1], "supervise", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $valueNames = @(
        "--run-count", "--max-concurrent-battles", "--queue-timeout-seconds",
        "--sleep-seconds", "--max-cycles", "--runtime-lease", "--autoresearch-count",
        "--proof-timeout-seconds", "--start-timeout-seconds", "--improve-timeout-seconds"
    )
    $switchNames = @("--enable-auto-improve", "--skip-improve")
    if (-not (Test-AllowedCommandTail -Tokens $tokens -StartIndex ($scriptIndex + 2) -ValueNames $valueNames -SwitchNames $switchNames)) { return $false }
    foreach ($name in @("--run-count", "--max-concurrent-battles", "--queue-timeout-seconds", "--sleep-seconds", "--max-cycles")) {
        if (-not (Test-PositiveCommandInteger -Tokens $tokens -Name $name)) { return $false }
    }
    if ((Get-CommandArgument -Tokens $tokens -Name "--max-concurrent-battles") -ne "3") { return $false }
    $leasePath = Resolve-CommandPathToken -Token (Get-CommandArgument -Tokens $tokens -Name "--runtime-lease")
    if (-not [string]::Equals($leasePath, $ResolvedRuntimeLease, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $improveModeCount = @($tokens | Where-Object {
        [string]::Equals($_, "--enable-auto-improve", [System.StringComparison]::OrdinalIgnoreCase) -or
        [string]::Equals($_, "--skip-improve", [System.StringComparison]::OrdinalIgnoreCase)
    }).Count
    return $improveModeCount -eq 1
}

function Test-BattleSupervisorLauncherOwnership {
    param($Process)
    if (-not $Process -or [int64]$Process.ProcessId -le 0) { return $false }
    if ([int64]$Process.ProcessId -eq [int64]$PID) { return $false }
    if (-not [string]::Equals([string]$Process.Name, "powershell.exe", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not (Test-ExactProcessExecutable -Process $Process -ExpectedPath $PowerShell)) { return $false }
    $tokens = @(Split-WindowsCommandLine -CommandLine ([string]$Process.CommandLine))
    if ($tokens.Count -lt 6 -or -not [string]::Equals((Resolve-CommandPathToken -Token $tokens[0]), $PowerShell, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $fileIndex = -1
    for ($index = 0; $index -lt ($tokens.Count - 1); $index++) {
        if ([string]::Equals($tokens[$index], "-File", [System.StringComparison]::OrdinalIgnoreCase)) {
            $fileIndex = $index
            break
        }
    }
    if ($fileIndex -ne 4) { return $false }
    $wrapperPath = Resolve-CommandPathToken -Token $tokens[$fileIndex + 1]
    if (-not [string]::Equals($wrapperPath, $TaskWrapperPath, [System.StringComparison]::OrdinalIgnoreCase)) {
        return $false
    }
    if (-not (Test-CommandSwitch -Tokens $tokens -Name "-NoProfile")) { return $false }
    if (-not [string]::Equals((Get-CommandArgument -Tokens $tokens -Name "-ExecutionPolicy"), "Bypass", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $valueNames = @(
        "-RunCount", "-MaxConcurrentBattles", "-MaxCycles", "-QueueTimeoutSeconds",
        "-SleepSeconds", "-RuntimeLease", "-RuntimeStateRoot", "-RuntimeLogRoot",
        "-RuntimeCacheRoot", "-SecretEnvFile", "-AccountSeasonPath",
        "-SearchParallelism", "-LoopBreak"
    )
    $switchNames = @("-AutoImprove", "-ClearStopFile", "-ClearDrainRequest", "-Foreground")
    if (-not (Test-AllowedCommandTail -Tokens $tokens -StartIndex ($fileIndex + 2) -ValueNames $valueNames -SwitchNames $switchNames)) { return $false }
    foreach ($name in @("-RunCount", "-MaxConcurrentBattles", "-MaxCycles", "-QueueTimeoutSeconds", "-SleepSeconds")) {
        if (-not (Test-PositiveCommandInteger -Tokens $tokens -Name $name)) { return $false }
    }
    if ((Get-CommandArgument -Tokens $tokens -Name "-MaxConcurrentBattles") -ne "3") { return $false }
    if ((Get-CommandArgument -Tokens $tokens -Name "-SearchParallelism") -ne "2") { return $false }
    $accountSeason = Resolve-CommandPathToken -Token (Get-CommandArgument -Tokens $tokens -Name "-AccountSeasonPath")
    if (-not [string]::Equals($accountSeason, $CanonicalAccountSeasonPath, [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    foreach ($pathBinding in @(
        [pscustomobject]@{ Name = "-RuntimeStateRoot"; Expected = $RuntimeStateRoot },
        [pscustomobject]@{ Name = "-RuntimeLogRoot"; Expected = $RuntimeLogRoot },
        [pscustomobject]@{ Name = "-RuntimeCacheRoot"; Expected = $RuntimeCacheRoot },
        [pscustomobject]@{ Name = "-SecretEnvFile"; Expected = $SecretEnvFile }
    )) {
        $boundPath = Resolve-CommandPathToken -Token (Get-CommandArgument -Tokens $tokens -Name $pathBinding.Name)
        if (-not [string]::Equals($boundPath, [System.IO.Path]::GetFullPath([string]$pathBinding.Expected), [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    }
    $loopBreak = Get-CommandArgument -Tokens $tokens -Name "-LoopBreak"
    if ($loopBreak -notin @("0", "1")) { return $false }
    $leasePath = Resolve-CommandPathToken -Token (Get-CommandArgument -Tokens $tokens -Name "-RuntimeLease")
    return (
        (Test-CommandSwitch -Tokens $tokens -Name "-Foreground") -and
        [string]::Equals($leasePath, $ResolvedRuntimeLease, [System.StringComparison]::OrdinalIgnoreCase)
    )
}

function Test-BattleLadderProcessOwnership {
    param($Process)
    if (-not $Process -or [int64]$Process.ProcessId -le 0) { return $false }
    if ([int64]$Process.ProcessId -eq [int64]$PID) { return $false }
    if (-not [string]::Equals([string]$Process.Name, "python.exe", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    $tokens = @(Split-WindowsCommandLine -CommandLine ([string]$Process.CommandLine))
    $runIndex = Find-ExactScriptTokenIndex -Tokens $tokens -ExpectedPath $LadderScript
    if ($runIndex -lt 1 -or -not (Test-PinnedPythonCommandPrefix -Process $Process -Tokens $tokens -ScriptIndex $runIndex -AllowDirectScript)) { return $false }
    $boundedIndex = Find-ExactScriptTokenIndex -Tokens $tokens -ExpectedPath $BoundedSessionScript
    if ($boundedIndex -ge 0 -and ($boundedIndex -ge $runIndex -or $tokens[$runIndex - 1] -ne "--")) { return $false }
    $valueNames = @(
        "--websocket-uri", "--ps-username", "--bot-mode", "--pokemon-format", "--run-count",
        "--max-concurrent-battles", "--search-parallelism", "--save-replay", "--ps-avatar", "--team-names",
        "--team-list", "--team-name", "--spectator-username"
    )
    if (-not (Test-AllowedCommandTail -Tokens $tokens -StartIndex ($runIndex + 1) -ValueNames $valueNames -SwitchNames @("--log-to-file"))) { return $false }
    foreach ($name in @("--run-count", "--max-concurrent-battles")) {
        if (-not (Test-PositiveCommandInteger -Tokens $tokens -Name $name)) { return $false }
    }
    if ((Get-CommandArgument -Tokens $tokens -Name "--max-concurrent-battles") -ne "3") { return $false }
    if ((Get-CommandArgument -Tokens $tokens -Name "--search-parallelism") -ne "2") { return $false }
    if (-not (Test-CommandSwitch -Tokens $tokens -Name "--log-to-file")) { return $false }
    if (-not [string]::Equals((Get-CommandArgument -Tokens $tokens -Name "--bot-mode"), "search_ladder", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not [string]::Equals((Get-CommandArgument -Tokens $tokens -Name "--pokemon-format"), "gen9ou", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    if (-not [string]::Equals((Get-CommandArgument -Tokens $tokens -Name "--save-replay"), "always", [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    foreach ($name in @("--websocket-uri", "--ps-username")) {
        if ([string]::IsNullOrWhiteSpace((Get-CommandArgument -Tokens $tokens -Name $name))) { return $false }
    }
    return $true
}

function Get-BattleSupervisorProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        Test-BattleSupervisorProcessOwnership -Process $_
    } | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate
}

function Get-BattleSupervisorLauncherProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        Test-BattleSupervisorLauncherOwnership -Process $_
    } | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate
}

function Get-BattleLadderProcesses {
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        Test-BattleLadderProcessOwnership -Process $_
    } | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate
}

function Test-FoulerBattleOwnerCandidate {
    param($Process)
    if (-not $Process -or [int64]$Process.ProcessId -le 0 -or [int64]$Process.ProcessId -eq [int64]$PID) { return $false }
    $name = [string]$Process.Name
    $commandLine = [string]$Process.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) { return $false }
    if ($name -match '^(?i:python(?:\d+(?:\.\d+)*)?|pythonw|py|pyw)\.exe$') {
        return (
            $commandLine -match '(?i)devstream_session\.py(?:"|\s).*?\bsupervise\b' -or
            ($commandLine -match '(?i)(?:^|[\\/])run\.py(?:"|\s)' -and $commandLine -match '(?i)--bot-mode(?:=|\s+)search_ladder\b')
        )
    }
    if ($name -match '^(?i:powershell|pwsh)\.exe$') {
        return $commandLine -match '(?i)start_battle_supervisor_task\.ps1(?:"|\s|$)'
    }
    return $false
}

function Assert-NoAlternateBattleOwnerProcesses {
    $alternate = @()
    foreach ($process in @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)) {
        if (-not (Test-FoulerBattleOwnerCandidate -Process $process)) { continue }
        if (
            (Test-BattleSupervisorProcessOwnership -Process $process) -or
            (Test-BattleSupervisorLauncherOwnership -Process $process) -or
            (Test-BattleLadderProcessOwnership -Process $process)
        ) { continue }
        $alternate += $process
    }
    if ($alternate.Count -gt 0) {
        throw "mutable or alternate Fouler battle owner process blocks supervisor mutation: $(@($alternate.ProcessId) -join ', ')"
    }
}

function Get-ProcessRecordById {
    param([int64]$ProcessId)
    if ($ProcessId -le 0) { return $null }
    return Get-CimInstance Win32_Process -Filter ("ProcessId = {0}" -f $ProcessId) -ErrorAction SilentlyContinue | Select-Object -First 1
}

function Get-ProcessTreeRecords {
    param([int64]$RootProcessId)
    $all = @(Get-CimInstance Win32_Process -ErrorAction Stop)
    $pending = [System.Collections.Generic.Queue[int64]]::new()
    $seen = [System.Collections.Generic.HashSet[int64]]::new()
    $pending.Enqueue($RootProcessId)
    $records = @()
    while ($pending.Count -gt 0) {
        $parentId = $pending.Dequeue()
        if (-not $seen.Add($parentId)) { continue }
        $record = $all | Where-Object { [int64]$_.ProcessId -eq $parentId } | Select-Object -First 1
        if ($record) { $records += $record }
        foreach ($child in @($all | Where-Object { [int64]$_.ParentProcessId -eq $parentId })) {
            $pending.Enqueue([int64]$child.ProcessId)
        }
    }
    return $records | Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate
}

function Test-SameProcessRecord {
    param($Expected, $Current)
    if (-not $Expected -or -not $Current) { return $false }
    if ([int64]$Expected.ProcessId -ne [int64]$Current.ProcessId) { return $false }
    $expectedCreation = "$($Expected.CreationDate)".Trim()
    $currentCreation = "$($Current.CreationDate)".Trim()
    if (-not [string]::IsNullOrWhiteSpace($expectedCreation) -and -not [string]::IsNullOrWhiteSpace($currentCreation)) {
        return [string]::Equals($expectedCreation, $currentCreation, [System.StringComparison]::Ordinal)
    }
    return (
        [string]::Equals([string]$Expected.Name, [string]$Current.Name, [System.StringComparison]::OrdinalIgnoreCase) -and
        [string]::Equals([string]$Expected.CommandLine, [string]$Current.CommandLine, [System.StringComparison]::Ordinal)
    )
}

function Invoke-VerifiedProcessTreeTermination {
    param(
        $Process,
        [ValidateSet("supervisor", "launcher", "ladder")]
        [string]$OwnershipType
    )
    $processId = [int64]$Process.ProcessId
    $current = Get-ProcessRecordById -ProcessId $processId
    $owned = if ($OwnershipType -eq "supervisor") {
        Test-BattleSupervisorProcessOwnership -Process $current
    } elseif ($OwnershipType -eq "launcher") {
        Test-BattleSupervisorLauncherOwnership -Process $current
    } else {
        Test-BattleLadderProcessOwnership -Process $current
    }
    if (-not $owned) {
        return [pscustomobject]@{
            processId = $processId
            ownershipType = $OwnershipType
            terminated = $false
            skipped = $true
            reason = "PID no longer belongs to the exact Fouler release owner"
        }
    }
    try {
        $ownedTree = @(Get-ProcessTreeRecords -RootProcessId $processId)
    } catch {
        return [pscustomobject]@{
            processId = $processId
            ownershipType = $OwnershipType
            terminated = $false
            skipped = $false
            treeVerified = $false
            reason = "owned process descendants could not be enumerated: $($_.Exception.Message)"
        }
    }
    if ($ownedTree.Count -le 0) {
        return [pscustomobject]@{
            processId = $processId
            ownershipType = $OwnershipType
            terminated = $false
            skipped = $false
            treeVerified = $false
            reason = "owned process root vanished before its descendants could be captured"
        }
    }
    $output = (& $TaskKill /PID "$processId" /T /F 2>&1 | Out-String).Trim()
    $code = $LASTEXITCODE
    Start-Sleep -Milliseconds 250
    $survivors = @()
    foreach ($record in $ownedTree) {
        $after = Get-ProcessRecordById -ProcessId ([int64]$record.ProcessId)
        if (Test-SameProcessRecord -Expected $record -Current $after) {
            $survivors += $after
        }
    }
    return [pscustomobject]@{
        processId = $processId
        ownershipType = $OwnershipType
        terminated = [bool]($survivors.Count -eq 0)
        skipped = $false
        treeVerified = $true
        ownedTreePids = @($ownedTree.ProcessId)
        survivorPids = @($survivors.ProcessId)
        taskkillExitCode = $code
        taskkillOutput = $output
    }
}

function Test-RuntimeLeaseForStart {
    $leasePath = if ([string]::IsNullOrWhiteSpace($RuntimeLease)) {
        Join-Path $ProjectDir "devstream\truth\runtime-lease.json"
    } elseif ([System.IO.Path]::IsPathRooted($RuntimeLease)) {
        $RuntimeLease
    } else {
        Join-Path $ProjectDir $RuntimeLease
    }
    $sourceCommit = Split-Path -Leaf $ProjectDir
    if ($sourceCommit -notmatch '^[0-9a-f]{40}$') {
        return [pscustomobject]@{
            ok = $false
            validatorExitCode = 2
            blockers = @("current Fouler source commit is unavailable")
        }
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
    if (-not [string]::IsNullOrWhiteSpace($env:FOULER_DEPLOYMENT_ID)) {
        $validatorArgs += @("--deployment-id", $env:FOULER_DEPLOYMENT_ID)
    }
    $previousNoBytecode = $env:PYTHONDONTWRITEBYTECODE
    $previousOptionalLocks = $env:GIT_OPTIONAL_LOCKS
    try {
        $env:PYTHONDONTWRITEBYTECODE = "1"
        $env:GIT_OPTIONAL_LOCKS = "0"
        $raw = (& $Py -I -B @validatorArgs 2>&1 | Out-String).Trim()
        $code = $LASTEXITCODE
    }
    finally {
        $env:PYTHONDONTWRITEBYTECODE = $previousNoBytecode
        $env:GIT_OPTIONAL_LOCKS = $previousOptionalLocks
    }
    try { $validation = $raw | ConvertFrom-Json } catch { $validation = $null }
    return [pscustomobject]@{
        ok = [bool]($code -eq 0 -and $validation -and $validation.ok -and $validation.environment)
        validatorExitCode = $code
        blockers = if ($validation) { @($validation.blockers) } else { @("runtime lease validator did not return valid JSON") }
    }
}

function Stop-BattleSupervisorProcesses {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $StopFile) | Out-Null
    (Get-Date).ToUniversalTime().ToString("o") | Set-Content -LiteralPath $StopFile -Encoding UTF8
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 2
    $terminations = @()
    foreach ($process in @(Get-BattleSupervisorLauncherProcesses)) {
        $terminations += Invoke-VerifiedProcessTreeTermination -Process $process -OwnershipType "launcher"
    }
    foreach ($process in @(Get-BattleSupervisorProcesses)) {
        $terminations += Invoke-VerifiedProcessTreeTermination -Process $process -OwnershipType "supervisor"
    }
    foreach ($process in @(Get-BattleLadderProcesses)) {
        $terminations += Invoke-VerifiedProcessTreeTermination -Process $process -OwnershipType "ladder"
    }
    Start-Sleep -Seconds 1
    $remainingLaunchers = @(Get-BattleSupervisorLauncherProcesses)
    $remainingSupervisors = @(Get-BattleSupervisorProcesses)
    $remainingLadders = @(Get-BattleLadderProcesses)
    $failedVerification = @($terminations | Where-Object { -not $_.skipped -and -not $_.terminated })
    if ($remainingLaunchers.Count -gt 0 -or $remainingSupervisors.Count -gt 0 -or $remainingLadders.Count -gt 0 -or $failedVerification.Count -gt 0) {
        $remainingPids = @($remainingLaunchers.ProcessId) + @($remainingSupervisors.ProcessId) + @($remainingLadders.ProcessId) + @($failedVerification.processId)
        throw "Exact-release Fouler supervisor or ladder process tree survived or could not be verified after taskkill: $(@($remainingPids | Select-Object -Unique) -join ', ')"
    }
    return $terminations
}

function Get-BattleSupervisorStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName -ErrorAction SilentlyContinue
    $actualAction = if ($task) { $task.Actions | Select-Object -First 1 } else { $null }
    $processes = @(Get-BattleSupervisorProcesses)
    [pscustomobject]@{
        taskName = $TaskName
        taskPresent = [bool]$task
        taskState = if ($task) { [string]$task.State } else { "missing" }
        lastTaskResult = if ($taskInfo) { $taskInfo.LastTaskResult } else { $null }
        lastRunTime = if ($taskInfo) { $taskInfo.LastRunTime.ToUniversalTime().ToString("o") } else { $null }
        projectDir = $ProjectDir
        wrapper = Join-Path $ProjectDir $TaskWrapper
        execute = if ($actualAction) { $actualAction.Execute } else { $TaskExecute }
        arguments = if ($actualAction) { $actualAction.Arguments } else { $TaskArguments }
        workingDirectory = if ($actualAction) { $actualAction.WorkingDirectory } else { $ProjectDir }
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        stderrTail = if (Test-Path -LiteralPath $StderrLog -PathType Leaf) { @(Get-Content -LiteralPath $StderrLog -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { [string]$_ }) } else { @() }
        pidFile = $PidFile
        stopFile = $StopFile
        processCount = $processes.Count
        processes = $processes
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    }
}

function Assert-InstalledTaskIdentity {
    $task = Get-ScheduledTask -TaskName $CanonicalTaskName -ErrorAction Stop
    $actions = @($task.Actions)
    if ($actions.Count -ne 1) { throw "canonical Fouler supervisor task must contain exactly one action" }
    $action = $actions[0]
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$action.Execute), $TaskExecute, [System.StringComparison]::OrdinalIgnoreCase)) { throw "canonical Fouler supervisor task executable identity changed" }
    if (-not [string]::Equals([string]$action.Arguments, $TaskArguments, [System.StringComparison]::Ordinal)) { throw "canonical Fouler supervisor task arguments changed" }
    if (-not [string]::Equals([System.IO.Path]::GetFullPath([string]$action.WorkingDirectory).TrimEnd("\"), $ProjectDir, [System.StringComparison]::OrdinalIgnoreCase)) { throw "canonical Fouler supervisor task working directory changed" }
    if (@($task.Triggers).Count -ne 0) { throw "canonical Fouler supervisor task must not have autonomous triggers" }
    if (-not [string]::Equals([string]$task.Principal.UserId, $RuntimeAccount, [System.StringComparison]::OrdinalIgnoreCase)) { throw "canonical Fouler supervisor task runtime principal changed" }
    if (-not [string]::Equals([string]$task.Principal.LogonType, "S4U", [System.StringComparison]::OrdinalIgnoreCase)) { throw "canonical Fouler supervisor task must use S4U logon" }
    return $task
}

function Wait-BattleSupervisorProcess {
    param(
        [int]$TimeoutSeconds = 45
    )
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    do {
        $processes = @(Get-BattleSupervisorProcesses)
        if ($processes.Count -gt 0) {
            return $processes
        }
        Start-Sleep -Seconds 2
    } while ((Get-Date) -lt $deadline)
    return @()
}

if ($ProjectDir -notmatch '^D:\\Releases\\fouler-play\\[0-9a-f]{40}$') {
    throw "ProjectDir must be an immutable D:\Releases\fouler-play\<commit> release"
}
Assert-NoReparsePathChain -Path $ProjectDir
if (($Apply -or $Stop -or $Uninstall) -and -not $ProjectDirWasExplicit) {
    throw "supervisor mutation requires an explicit target -ProjectDir from a trusted installer copy"
}
if ($Apply -or $Stop -or $Uninstall) {
    $installerPath = [System.IO.Path]::GetFullPath([string]$MyInvocation.MyCommand.Path)
    if ($installerPath -match '^D:\\Releases\\fouler-play\\') { throw "supervisor installer must execute from the trusted control plane, never a release tree" }
    Assert-NoReparsePathChain -Path $installerPath
    Assert-RegularSingleLinkFile -Path $installerPath -Label "supervisor installer"
    Assert-NoPathOverlap -First $ProjectDir -Second $installerPath -Label "supervisor installer path"
}
foreach ($externalPath in @($AuthorityRoot, $AccountSeasonPath, $RuntimeStateRoot, $RuntimeLogRoot, $RuntimeCacheRoot, (Join-Path $RuntimeStateRoot "tmp"), $BackupRoot, $SecretEnvFile, $ResolvedRuntimeLease)) {
    Assert-NoPathOverlap -First $ProjectDir -Second $externalPath -Label "supervisor runtime/publication path"
}
if ($Apply -or $Stop -or $Uninstall) {
    $authorityActivation = Assert-ManifestedImmutableRelease
    Assert-NoAlternateBattleOwnerProcesses
}

if ($Status) {
    Get-BattleSupervisorStatus | ConvertTo-Json -Depth 6
    exit 0
}

if ($Apply -or $Stop -or $Uninstall) {
    $identity = [System.Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object System.Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([System.Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "battle supervisor mutation requires an elevated administrator PowerShell session"
    }
}

if ($Stop -or $Uninstall) {
    Save-TaskBackup -Name $TaskName | Out-Null
    Stop-BattleSupervisorProcesses | Out-Null
    if ($Uninstall -and $Apply) {
        Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    }
    Get-BattleSupervisorStatus | ConvertTo-Json -Depth 6
    exit 0
}

if (-not $Apply) {
    [pscustomobject]@{
        dryRun = $true
        wouldCreateOrUpdateTask = $TaskName
        execute = $TaskExecute
        arguments = $TaskArguments
        workingDirectory = $ProjectDir
        stdoutLog = $StdoutLog
        stderrLog = $StderrLog
        rollback = "Unregister-ScheduledTask -TaskName '$TaskName' -Confirm:`$false"
    } | ConvertTo-Json -Depth 4
    exit 0
}

if ($Start -and ($RunCount -le 0 -or $MaxCycles -le 0)) {
    [pscustomobject]@{
        dryRun = $false
        blocked = $true
        status = "blocked-runtime-bounds"
        blockers = @("starting the persistent battle supervisor requires explicit positive -RunCount and -MaxCycles bounds")
        taskName = $TaskName
    } | ConvertTo-Json -Depth 4
    exit 2
}

if ($Start) {
    $startLeaseCheck = Test-RuntimeLeaseForStart
    if (-not $startLeaseCheck.ok) {
        [pscustomobject]@{
            dryRun = $false
            blocked = $true
            status = "blocked-runtime-lease"
            taskName = $TaskName
            validatorExitCode = $startLeaseCheck.validatorExitCode
            blockers = @($startLeaseCheck.blockers)
        } | ConvertTo-Json -Depth 5
        exit 2
    }
}

$backup = Save-TaskBackup -Name $TaskName
$secretAclBackup = Join-Path (Split-Path -Parent $backup) "fouler-secret-acl.txt"
& "$env:SystemRoot\System32\icacls.exe" $SecretEnvFile /save $secretAclBackup /Q | Out-Null
if ($LASTEXITCODE -ne 0) { throw "failed to back up secret environment file ACL" }
Protect-SecretFile -Path $SecretEnvFile
Protect-RuntimeWriteDirectory -Path $RuntimeStateRoot
Protect-RuntimeWriteDirectory -Path $RuntimeLogRoot
Protect-RuntimeWriteDirectory -Path $RuntimeCacheRoot
Protect-RuntimeWriteDirectory -Path (Join-Path $RuntimeStateRoot "pids")
Protect-RuntimeWriteDirectory -Path (Join-Path $RuntimeStateRoot "tmp")
$startFallback = $null
$action = New-ScheduledTaskAction -Execute $TaskExecute -Argument $TaskArguments -WorkingDirectory $ProjectDir
# This is an explicitly triggered bounded task. Boot/logon triggers let an old
# lease and old release wake up later, violating the single-owner guarantee.
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 30) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId $RuntimeAccount -LogonType S4U -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Settings $settings -Principal $principal -Description "DEKU-authorized bounded Fouler Play Showdown battle supervisor." -Force | Out-Null
$null = Assert-InstalledTaskIdentity

if ($Start) {
    Stop-BattleSupervisorProcesses | Out-Null
    if (Test-Path -LiteralPath $PidFile -PathType Leaf) { Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $StopFile -PathType Leaf) { Remove-Item -LiteralPath $StopFile -Force -ErrorAction SilentlyContinue }
    Rotate-LogFile -Path $StdoutLog | Out-Null
    Rotate-LogFile -Path $StderrLog | Out-Null
    Start-ScheduledTask -TaskName $TaskName
    $scheduledProcesses = @(Wait-BattleSupervisorProcess -TimeoutSeconds 45)
    if ($scheduledProcesses.Count -le 0) {
        $startFallback = [ordered]@{
            used = $false
            prohibited = $true
            reason = "scheduled task did not materialize the explicit runtime-principal supervisor; installer-token fallback is forbidden"
            processCountAfter = 0
            processesAfter = @()
        }
    } else {
        $startFallback = [ordered]@{
            used = $false
            reason = "scheduled task produced a live battle supervisor"
            startedAt = $null
            launcherPid = $null
            error = $null
            processCountAfter = $scheduledProcesses.Count
            processesAfter = $scheduledProcesses
            failedLaunchCleanup = $null
        }
    }
}

$statusPayload = Get-BattleSupervisorStatus
$statusPayload | Add-Member -NotePropertyName backup -NotePropertyValue $backup
$statusPayload | Add-Member -NotePropertyName startFallback -NotePropertyValue $startFallback
if ($Start -and $statusPayload.processCount -le 0) {
    $statusPayload | Add-Member -NotePropertyName blocked -NotePropertyValue $true
    $statusPayload | Add-Member -NotePropertyName status -NotePropertyValue "blocked-supervisor-launch"
    $statusPayload | Add-Member -NotePropertyName blockers -NotePropertyValue @("battle supervisor launch did not produce a live devstream_session.py supervise process")
    $statusPayload | ConvertTo-Json -Depth 8
    exit 2
}
$statusPayload | ConvertTo-Json -Depth 6
