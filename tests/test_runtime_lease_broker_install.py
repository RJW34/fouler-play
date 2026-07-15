from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_runtime_lease_broker.ps1"
BROKER = ROOT / "infrastructure" / "windows" / "fouler_lease_broker.py"
CLIENT = ROOT / "infrastructure" / "runtime_lease_client.py"


def test_installer_security_contract_is_static_and_fail_closed():
    source = INSTALLER.read_text(encoding="utf-8")
    lowered = source.lower()

    assert "[Parameter(Mandatory = $true)]\n    [ValidatePattern('^[0-9A-Fa-f]{64}$')]\n    [string]$ExpectedNssmSha256" in source
    assert "$nssmSnapshot.Sha256 -ne $expectedHash" in source
    assert "$installedHash -ne $expectedHash" in source
    assert "PinnedNssm $nssmSnapshot.Path" not in source
    assert "$safeBackupNssm" in source
    assert 'Join-Path $backupDirectory "nssm.previous.exe"' in source
    assert "installed NSSM rollback backup differs from its pre-mutation snapshot" in source
    assert 'Join-Path $BrokerRoot "tmp"' in source
    assert '"TEMP=$tempRoot", "TMP=$tempRoot"' in source
    assert "^D:\\\\Releases\\\\fouler-play\\\\[0-9a-f]{40}$" in source
    assert "ProjectDir must be an immutable D:\\Releases\\fouler-play\\<commit> release" in source
    assert "broker installer path" in source
    assert "$MyInvocation.MyCommand.Path" in source

    assert 'serviceAccount = "NT AUTHORITY\\LocalService"' in source
    assert '"ObjectName", "NT AUTHORITY\\LocalService"' in source
    assert "& $sc sidtype $ServiceName unrestricted" in source
    assert 'Get-Sid -Account "NT SERVICE\\$ServiceName"' in source
    assert 'New SecurityIdentifier("S-1-5-18")' not in source
    assert 'SecurityIdentifier("S-1-5-18")' in source
    assert 'SecurityIdentifier("S-1-5-32-544")' in source
    assert "runtimeDatabaseAccess = $false" in source
    assert 'pipeRuntimeRights = "FILE_READ_DATA|FILE_WRITE_DATA"' in source

    backup_call = source.index("Save-ExistingServiceConfiguration -Path")
    service_install = source.index("& $sc create $ServiceName")
    first_service_set = source.index('Invoke-Nssm -Arguments @("set", $ServiceName')
    assert backup_call < service_install < first_service_set
    assert '"start=" "disabled"' in source
    assert 'Invoke-Nssm -Arguments @("set", $ServiceName, "Start", "SERVICE_DISABLED")' in source
    assert "broker publication must remain stopped and Disabled until authority activation" in source
    activation = source.index("$authorityActivation = Assert-AuthorityActivationReceipt")
    enable = source.index('Invoke-Nssm -Arguments @("set", $ServiceName, "Start", "SERVICE_AUTO_START")')
    start = source.index("Start-Service -Name $ServiceName")
    assert first_service_set < activation < enable < start
    assert "fouler-lease-broker-activation/v1" in source
    assert "fouler-bootstrap-manifest/v1" in source
    assert "release file inventory no longer matches broker authority activation" in source
    assert "Get-CompetingBrokerProcesses" in source
    assert "stale or competing Fouler lease broker process survived" in source
    assert "Assert-RunningBrokerProcessIdentity" in source
    assert "$tokens.Count -ne 11" in source
    assert "broker child process is not the pinned immutable-release venv Python" in source
    assert "broker service must own exactly one direct release-venv child process" in source
    assert "sc.exe\" qc $ServiceName" in source
    assert "sc.exe\" queryex $ServiceName" in source
    assert "reg.exe\" export" in source
    assert "nssm-dump.txt" in source
    assert "Grant-ServiceReleaseRead" not in source
    assert "Save-ReleaseAcl" not in source

    assert "C:\\ProgramData\\HERMES-LeaseBroker\\fouler" in source
    assert 'Join-Path $BrokerRoot "consumption.sqlite3"' in source
    assert "Protect-DirectoryTree -Path $BrokerRoot -ServiceSid $serviceSid" in source
    assert "runtime SID is deliberately absent" in source
    assert "SetAccessRuleProtection($true, $false)" in source
    assert "[System.IO.Directory]::SetAccessControl" in source
    assert "[System.IO.File]::SetAccessControl" in source
    assert "/grant:r" not in source
    assert "/inheritance:r" not in source
    assert "GetFileInformationByHandle" in source
    assert "NumberOfLinks -ne 1" in source
    assert "Assert-NoReparsePathChain" in source
    assert "Assert-NoPathOverlap" in source
    assert "published NSSM hash changed immediately before execution" in source
    assert "rollback NSSM tool failed its hash pin immediately before execution" in source
    assert "existing/PATH NSSM" not in source
    assert "initialize-store" not in source
    assert "& $python" not in source
    assert '"-I -B `"$entrypoint`"' in source
    assert "startsBattles = $false" in source
    assert "startsStreaming = $false" in source
    assert "mutatesBattleTasks = $false" in source
    assert "start-scheduledtask" not in lowered
    assert "schtasks.exe" not in lowered
    assert "twitch" not in lowered
    assert re.search(r"\bobs\b", lowered) is None
    assert "run.py" not in lowered
    assert re.findall(r"Start-Service\s+-Name\s+([^\r\n]+)", source) == ["$ServiceName"]


def test_broker_uses_only_local_byte_mode_ctypes_pipe_with_explicit_sddl():
    source = BROKER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported_from = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "socket" not in imports
    assert "socket" not in imported_from
    assert "win32pipe" not in imports
    assert "pywintypes" not in imports
    assert "CreateNamedPipeW" in source
    assert "FILE_FLAG_FIRST_PIPE_INSTANCE" in source
    assert "PIPE_TYPE_BYTE" in source
    assert "PIPE_READMODE_BYTE" in source
    assert "PIPE_REJECT_REMOTE_CLIENTS" in source
    assert 'f"D:P(A;;0x0010019f;;;{broker_sid})"' in source
    assert 'f"(A;;0x00100083;;;{runtime_sid})"' in source
    assert "GetNamedPipeClientProcessId" in source
    assert "GetProcessTimes(client)" in source
    assert set(__import__(
        "infrastructure.windows.fouler_lease_broker",
        fromlist=["_ACTION_FIELDS"],
    )._ACTION_FIELDS) == {
        "reserve-runtime",
        "reserve-improve",
        "claim",
        "complete",
        "status",
    }
    assert "PIPE_UNLIMITED_INSTANCES" not in source
    assert "MAX_ACTIVE_WORKERS = 8" in source
    assert "CONNECTION_DEADLINE_SECONDS" in source
    assert "_overlapped_transfer" in source


def test_client_verifies_server_process_service_and_token_identity():
    source = CLIENT.read_text(encoding="utf-8")
    ast.parse(source)
    assert "GetNamedPipeServerProcessId" in source
    assert "QueryFullProcessImageNameW" in source
    assert "GetProcessTimes" in source
    assert 'token_user != "S-1-5-19"' in source
    assert 'f"NT SERVICE\\\\{service_name}"' in source
    assert "SE_GROUP_USE_FOR_DENY_ONLY" in source
    assert "QueryServiceStatusEx" in source
    assert "parent_pid != service_pid" in source
    assert "SECURITY_IDENTIFICATION" in source
    assert "FILE_READ_DATA | api.FILE_WRITE_DATA" in source
    assert "http://" not in source
    assert "https://" not in source
