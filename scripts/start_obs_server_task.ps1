param(
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path,
    [string]$RuntimeLease = "",
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

function ConvertFrom-HermesEnvValue {
    param([string]$Value)
    if ($null -eq $Value) { return $null }
    $trimmed = $Value.Trim()
    if (($trimmed.StartsWith('"') -and $trimmed.EndsWith('"')) -or ($trimmed.StartsWith("'") -and $trimmed.EndsWith("'"))) {
        return $trimmed.Substring(1, $trimmed.Length - 2)
    }
    return $trimmed
}

function Test-HermesEnvValue {
    param([string]$Value)
    $value = ConvertFrom-HermesEnvValue -Value $Value
    if (-not $value) { return $false }
    if ($value -match "^<[^>]+>$") { return $false }
    if ($value -match "^(?i:CHANGE_ME(?:_|$))") { return $false }
    if ($value -match "^(?i:missing|present|\[REDACTED\])$") { return $false }
    return $true
}

function Get-HermesEnvValue {
    param([string[]]$Names)
    foreach ($Name in $Names) {
        $envValue = [Environment]::GetEnvironmentVariable($Name)
        if (Test-HermesEnvValue -Value $envValue) { return (ConvertFrom-HermesEnvValue -Value $envValue) }
    }
    $secretFile = Join-Path $env:APPDATA "hermes-devstream\secrets.env"
    if (-not (Test-Path -LiteralPath $secretFile -PathType Leaf)) { return $null }
    foreach ($line in Get-Content -LiteralPath $secretFile) {
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#")) { continue }
        $match = [regex]::Match($trimmed, "^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$")
        if (-not $match.Success -or $Names -notcontains $match.Groups[1].Value) { continue }
        $value = ConvertFrom-HermesEnvValue -Value $match.Groups[2].Value
        if (Test-HermesEnvValue -Value $value) { return $value }
    }
    return $null
}

function Set-HermesProcessDefault {
    param(
        [string]$Name,
        [string]$Value
    )
    if (-not [Environment]::GetEnvironmentVariable($Name, "Process")) {
        [Environment]::SetEnvironmentVariable($Name, $Value, "Process")
    }
}

function Resolve-RuntimeLeasePath {
    param([string]$RuntimeLease)
    if ([string]::IsNullOrWhiteSpace($RuntimeLease)) {
        return (Join-Path $ProjectDir "devstream\truth\runtime-lease.json")
    }
    if ([System.IO.Path]::IsPathRooted($RuntimeLease)) {
        return $RuntimeLease
    }
    return (Join-Path $ProjectDir $RuntimeLease)
}

function Get-RuntimeLeaseAccount {
    param([string]$RuntimeLease)
    $path = Resolve-RuntimeLeasePath -RuntimeLease $RuntimeLease
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        return ""
    }
    try {
        $lease = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
    } catch {
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

function Test-StateEndpoint {
    param([int]$Port = 8777)
    try {
        $response = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:$Port/state"
        return [bool]($response.StatusCode -eq 200 -and $response.Content.Length -gt 20)
    } catch {
        return $false
    }
}

function Stop-ObsServerProcesses {
    Get-CimInstance Win32_Process | Where-Object {
        $_.CommandLine -and
        $_.CommandLine -match "streaming[\\/]serve_obs_page\.py" -and
        $_.CommandLine -match [regex]::Escape($ProjectDir) -and
        $_.Name -match "python|py|cmd"
    } | Sort-Object ProcessId -Descending | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
    }
}

@(
    @{ Target = "OBS_WS_PASSWORD"; Names = @("OBS_WS_PASSWORD", "OBS_WEBSOCKET_PASSWORD", "HERMES_OBS_WEBSOCKET_PASSWORD") },
    @{ Target = "OBS_WS_HOST"; Names = @("OBS_WS_HOST", "OBS_WEBSOCKET_HOST", "HERMES_OBS_WEBSOCKET_HOST") },
    @{ Target = "OBS_WS_PORT"; Names = @("OBS_WS_PORT", "OBS_WEBSOCKET_PORT", "HERMES_OBS_WEBSOCKET_PORT") }
) | ForEach-Object {
    $value = Get-HermesEnvValue -Names $_.Names
    if ($value) {
        [Environment]::SetEnvironmentVariable($_.Target, $value, "Process")
    }
}

$python = Join-Path $ProjectDir ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
    $python = "python.exe"
}

Remove-Item Env:FP_PARENT_PID -ErrorAction SilentlyContinue
[Environment]::SetEnvironmentVariable("FP_PARENT_PID", "0", "Process")
Set-HermesProcessDefault -Name "PYTHONUTF8" -Value "1"
Set-HermesProcessDefault -Name "PYTHONIOENCODING" -Value "utf-8"
Set-HermesProcessDefault -Name "BOT_LOG_TO_FILE" -Value "1"
Set-HermesProcessDefault -Name "OBS_SERVER_PORT" -Value "8777"
Set-HermesProcessDefault -Name "OBS_SYNC_INTERVAL_SEC" -Value "0"
Set-HermesProcessDefault -Name "FOULER_OBS_WS_DISABLED" -Value "1"
[Environment]::SetEnvironmentVariable("PS_FORMAT", "gen9ou", "Process")
$runtimeLeasePath = Resolve-RuntimeLeasePath -RuntimeLease $RuntimeLease
[Environment]::SetEnvironmentVariable("FOULER_RUNTIME_LEASE_PATH", $runtimeLeasePath, "Process")
$leaseAccount = Get-RuntimeLeaseAccount -RuntimeLease $RuntimeLease
if (-not [string]::IsNullOrWhiteSpace($leaseAccount)) {
    [Environment]::SetEnvironmentVariable("PS_USERNAME", $leaseAccount, "Process")
    [Environment]::SetEnvironmentVariable("SHOWDOWN_USER_ID", $leaseAccount, "Process")
    [Environment]::SetEnvironmentVariable("SHOWDOWN_ACCOUNTS", $leaseAccount, "Process")
    [Environment]::SetEnvironmentVariable("FOULER_ACTIVE_ACCOUNT", $leaseAccount, "Process")
}

Set-Location -LiteralPath $ProjectDir

if ($Foreground) {
    & $python -u "streaming\serve_obs_page.py"
    exit $LASTEXITCODE
}

$pidDir = Join-Path $ProjectDir ".pids"
$logDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $pidDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stdoutLog = Join-Path $logDir "jigglypuff-obs-server.log"
$stderrLog = Join-Path $logDir "jigglypuff-obs-server.err.log"
Stop-ObsServerProcesses
Start-Sleep -Seconds 1
$launch = Start-Process `
    -FilePath $python `
    -ArgumentList @("-u", "streaming\serve_obs_page.py") `
    -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

for ($i = 0; $i -lt 12; $i++) {
    Start-Sleep -Seconds 1
    if (Test-StateEndpoint -Port 8777) {
        exit 0
    }
    if ($launch.HasExited) {
        Write-Error "Fouler OBS server exited during startup with code $($launch.ExitCode)"
        exit 1
    }
}

Stop-ObsServerProcesses
Write-Error "Fouler OBS server process started but /state did not become healthy on port 8777"
exit 1
