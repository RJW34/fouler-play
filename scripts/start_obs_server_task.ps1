param(
    [string]$ProjectDir = (Resolve-Path "$PSScriptRoot\..").Path,
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

Set-Location -LiteralPath $ProjectDir

if ($Foreground) {
    & $python "streaming\serve_obs_page.py"
    exit $LASTEXITCODE
}

$pidDir = Join-Path $ProjectDir ".pids"
$logDir = Join-Path $ProjectDir "logs"
New-Item -ItemType Directory -Force -Path $pidDir | Out-Null
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$stdoutLog = Join-Path $logDir "jigglypuff-obs-server.log"
$stderrLog = Join-Path $logDir "jigglypuff-obs-server.err.log"
$launch = Start-Process `
    -FilePath $python `
    -ArgumentList @("streaming\serve_obs_page.py") `
    -WorkingDirectory $ProjectDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $stdoutLog `
    -RedirectStandardError $stderrLog `
    -PassThru

Start-Sleep -Seconds 4
if ($launch.HasExited) {
    Write-Error "Fouler OBS server exited during startup with code $($launch.ExitCode)"
    exit 1
}
exit 0
