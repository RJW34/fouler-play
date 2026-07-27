from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_season_obs_server_task.ps1"


def test_installer_binds_one_task_to_exact_season_and_release() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert '"DEVSTREAM-JIG-FoulerObsServer"' in source
    assert '"E:\\Devstream\\Releases\\fouler-play"' in source
    assert '"C:\\ProgramData\\Devstream\\staging\\fouler\\manifests"' in source
    assert '"C:\\ProgramData\\Devstream\\authority\\fouler"' in source
    assert '"E:\\DevstreamRuntime\\fouler"' in source
    assert 'Join-Path $release "streaming\\run_season_obs_server.py"' in source
    assert "--authority" in source
    assert "--authority-sha256" in source
    assert "Assert-ManifestedRelease" in source
    assert "release file inventory count no longer matches" in source
    assert "manifested release file hash changed" in source
    assert "-LogonType S4U" in source
    assert "-RunLevel Limited" in source
    assert "-MultipleInstances IgnoreNew" in source


def test_installer_is_backup_first_reversible_and_output_safe() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    backup = source.index("$taskSnapshots = @{}")
    register = source.index("Register-ScheduledTask", backup)
    stop_legacy = source.index("Stop-Service -Name $LegacyServiceName", register)
    start_new = source.index("Start-ScheduledTask -TaskName $TaskName", stop_legacy)
    disable_legacy = source.index(
        "Set-Service -Name $LegacyServiceName -StartupType Disabled", start_new
    )
    assert backup < register < stop_legacy < start_new < disable_legacy
    assert "Restore-TaskSnapshot" in source
    assert "Start-Service -Name $LegacyServiceName" in source
    assert "port 8777 is owned outside the declared legacy OBS service" in source
    assert "port 8777 is not owned by the exact finite-season OBS entrypoint" in source
    assert "finite-season OBS /health did not become ready" in source
    assert "legacy OBS scheduled task must be disabled before service cutover" in source
    assert "finite-season OBS must own exactly one IPv4 loopback listener" in source
    assert "publicOutputChanged = $false" in source
    assert "startStreaming = $false" in source
    assert "StartStream" not in source
    assert "OBS_WS_PASSWORD" not in source
    assert "TWITCH" not in source.upper()


def test_installer_names_hermes_only_as_the_retired_predecessor() -> None:
    source = INSTALLER.read_text(encoding="utf-8")
    hermes_lines = [line for line in source.splitlines() if "HERMES" in line]

    assert hermes_lines
    assert all("Legacy" in line for line in hermes_lines)
    assert "C:\\ProgramData\\HERMES" not in source
