# Induce a controlled websocket disconnect to prove in-process auto-reconnect.
# Blocks BOTH directions to the child's current Showdown server IP for ~80s and
# verifies mid-block that the TCP connection actually died. try/finally GUARANTEES
# the firewall rules are removed even on error.
$ErrorActionPreference = 'Stop'
$RuleOut = 'fouler-reconnect-block-out'
$RuleIn  = 'fouler-reconnect-block-in'

$pids = @(Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -like '*ladder_run.py*' } |
    ForEach-Object { $_.ProcessId })
if (-not $pids) { Write-Output 'ERROR: no ladder_run.py child found'; exit 1 }

$conn = Get-NetTCPConnection -RemotePort 443 -State Established -ErrorAction SilentlyContinue |
    Where-Object { $pids -contains $_.OwningProcess } | Select-Object -First 1
if (-not $conn) { Write-Output 'ERROR: no established :443 connection for child'; exit 1 }
$ip = $conn.RemoteAddress
$owner = $conn.OwningProcess
Write-Output "ws owner pid=$owner remote=$ip"

Get-NetFirewallRule -DisplayName $RuleOut,$RuleIn -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue

try {
    New-NetFirewallRule -DisplayName $RuleOut -Direction Outbound -Action Block -RemoteAddress $ip -Enabled True | Out-Null
    New-NetFirewallRule -DisplayName $RuleIn  -Direction Inbound  -Action Block -RemoteAddress $ip -Enabled True | Out-Null
    Write-Output ("BLOCK ON @ " + (Get-Date -Format 'HH:mm:ss') + " ip=$ip")
    Start-Sleep -Seconds 45
    $mid = Get-NetTCPConnection -RemotePort 443 -RemoteAddress $ip -State Established -ErrorAction SilentlyContinue |
        Where-Object { $pids -contains $_.OwningProcess }
    Write-Output ("mid-block: original ws still established? " + [bool]$mid)
    Start-Sleep -Seconds 35
}
finally {
    Get-NetFirewallRule -DisplayName $RuleOut,$RuleIn -ErrorAction SilentlyContinue | Remove-NetFirewallRule -ErrorAction SilentlyContinue
    Write-Output ("BLOCK OFF @ " + (Get-Date -Format 'HH:mm:ss'))
}
$still = Get-NetFirewallRule -DisplayName $RuleOut,$RuleIn -ErrorAction SilentlyContinue
Write-Output ("rules_removed=" + ($null -eq $still))
