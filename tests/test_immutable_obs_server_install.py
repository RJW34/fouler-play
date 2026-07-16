from __future__ import annotations

from pathlib import Path

from aiohttp import web

from streaming import run_obs_server_service as service


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "scripts" / "install_obs_server_service.ps1"


def test_obs_installer_is_backup_first_exact_release_and_non_starting() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert '"HERMES-FoulerObsServer"' in source
    assert "^D:\\\\Releases\\\\fouler-play\\\\[0-9a-f]{40}$" in source
    assert "{40,64}" not in source
    assert 'Join-Path $ProjectDir ".venv\\Scripts\\python.exe"' in source
    assert 'Join-Path $ProjectDir "streaming\\run_obs_server_service.py"' in source
    assert '"OBS_SERVER_HOST=127.0.0.1"' in source
    assert '"OBS_SERVER_PORT=8777"' in source
    assert '"SERVICE_AUTO_START"' in source
    assert '"SERVICE_DEMAND_START"' not in source
    assert "Start-Service" not in source
    assert "Start-Process" not in source
    assert "-Start is retired" in source
    assert "startsProcesses = $false" in source

    backup = source.index("$backup = Save-RollbackBackup")
    stop = source.index("Stop-ManagedProcess", backup)
    task_retirement = source.index("Disable-LegacyObsTasks", backup)
    publication = source.index(
        "Write-AtomicBytes -Bytes $sourceSnapshot.Bytes -Destination $StableNssm"
    )
    assert backup < stop
    assert backup < task_retirement
    assert backup < publication
    assert "fouler-obs-service-rollback/v1" in source
    assert "NSSM rollback backup differs from its pre-mutation snapshot" in source
    assert "failed to export the canonical OBS service registry backup" in source
    assert "restored NSSM hash differs from backup" in source
    assert "Automatic file/new-service rollback completed" in source


def test_obs_installer_rejects_alternate_lifecycle_and_process_identity() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert '"HERMES-FoulerObsKeepAlive", "HERMES-FoulerObsServer"' in source
    assert "Disable-LegacyObsTasks" in source
    assert "Assert-LegacyObsTasksDisabled" in source
    assert "retired alternate OBS task remains enabled" in source
    assert "Assert-NoAlternateObsProcesses" in source
    assert "mutable or alternate Fouler OBS process" in source
    assert "port 8777 is owned outside the stopped canonical OBS service" in source
    assert "Assert-InstalledObsServiceIdentity" in source
    assert "$tokens.Count -ne 5" in source
    assert "SCM/NSSM does not own exactly one exact OBS Python child" in source
    assert "canonical OBS service environment changed" in source
    assert r"D:\Projects\fouler-play" not in source
    assert "OBS_WS_PASSWORD" not in source
    assert "PS_PASSWORD" not in source
    assert "TWITCH" not in source.upper()


def test_obs_process_chain_keeps_a_single_nssm_child_as_an_array() -> None:
    source = INSTALLER.read_text(encoding="utf-8")

    assert "$allChildren = @(\n        if ($servicePid -gt 0) {" in source
    assert "$allChildren = if ($servicePid -gt 0)" not in source
    assert "$allChildren.Count -ne 1" in source


def test_service_release_pattern_accepts_only_one_lowercase_commit() -> None:
    release = "D:\\Releases\\fouler-play\\" + ("a" * 40)

    assert service.WINDOWS_RELEASE_RE.fullmatch(release)
    assert not service.WINDOWS_RELEASE_RE.fullmatch(release + "0")
    assert not service.WINDOWS_RELEASE_RE.fullmatch(
        "D:\\Projects\\fouler-play\\" + ("a" * 40)
    )
    assert not service.WINDOWS_RELEASE_RE.fullmatch(
        "D:\\Releases\\fouler-play\\" + ("A" * 40)
    )


def test_public_surface_forces_loopback_bind(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_app(app: object, *args: object, **kwargs: object) -> None:
        calls.append({"app": app, "args": args, **kwargs})

    def fake_run_path(path: str, *, run_name: str) -> None:
        assert Path(path).name == "serve_obs_page.py"
        assert run_name == "__main__"
        web.run_app(object(), host="0.0.0.0", port=9999)

    monkeypatch.setattr(web, "run_app", fake_run_app)
    monkeypatch.setattr(service.runpy, "run_path", fake_run_path)

    service._run_public_surface()

    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8777
    assert web.run_app is fake_run_app


def test_service_enables_obs_source_updates_by_default_and_honors_rehearsal_disable(
    monkeypatch,
) -> None:
    validation = {"lease": {"account": "FoulerPilot"}}
    environment: dict[str, str] = {}
    monkeypatch.setattr(service, "_assert_service_runtime_layout", lambda: None)
    monkeypatch.setattr(service, "_validated_runtime", lambda: validation)
    monkeypatch.setattr(service, "lease_environment", lambda _validation: {})

    service._configure_environment(environment)

    assert environment["FOULER_OBS_WS_DISABLED"] == "0"
    if service.os.name == "nt":
        for name, path in service.WINDOWS_EXTERNAL_PATHS.items():
            assert environment[name] == str(path)

    environment["FOULER_OBS_WS_DISABLED"] = "1"
    service._configure_environment(environment)

    assert environment["FOULER_OBS_WS_DISABLED"] == "1"


def test_service_errors_redact_secret_environment_values(monkeypatch) -> None:
    secret = "immutable-obs-secret-value-for-redaction"
    monkeypatch.setenv("FOULER_TEST_SECRET", secret)

    redacted = service._redact_sensitive_values(f"failure contained {secret}")

    assert secret not in redacted
    assert "[REDACTED]" in redacted
