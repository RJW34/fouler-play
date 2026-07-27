from __future__ import annotations

from pathlib import Path

from aiohttp import web

from streaming import run_season_obs_server as server
from streaming import serve_obs_page as public_surface


def _validation(tmp_path: Path) -> dict:
    runtime = tmp_path / "runtime"
    return {
        "ok": True,
        "season": {
            "id": "season-test-authority",
            "sourceCommit": "a" * 40,
            "account": "DekuFoulerFresh",
            "runtime": {
                "stateRoot": str(runtime / "state"),
                "logRoot": str(runtime / "logs"),
                "cacheRoot": str(runtime / "cache"),
                "tempRoot": str(runtime / "temp"),
                "accountSeasonPath": str(runtime / "account-season.json"),
                "eventQueueRoot": str(runtime / "events"),
            },
        },
    }


def test_season_obs_environment_comes_only_from_validated_authority(
    monkeypatch, tmp_path
) -> None:
    validation = _validation(tmp_path)
    calls: list[dict[str, object]] = []

    def fake_validate(**kwargs):
        calls.append(kwargs)
        return validation

    monkeypatch.setattr(server, "validate_season_authority", fake_validate)
    environment = {
        "FOULER_OBS_WS_DISABLED": "0",
        "OBS_SERVER_HOST": "0.0.0.0",
        "OBS_SERVER_PORT": "9999",
        "PS_USERNAME": "stale-account",
    }

    result = server.configure_environment(
        authority_path=str(tmp_path / "season.json"),
        authority_sha256="b" * 64,
        environment=environment,
        hostname="JIGGLYPUFF",
    )

    assert result is validation
    assert calls == [
        {
            "authority_path": str(tmp_path / "season.json"),
            "expected_sha256": "b" * 64,
            "release_root": server.ROOT,
            "require_child_binding": False,
            "require_existing_paths": True,
            "environ": environment,
            "hostname": "JIGGLYPUFF",
        }
    ]
    assert environment["FOULER_RUNTIME_STATE_ROOT"].endswith(r"runtime\state")
    assert environment["FOULER_RUNTIME_LOG_ROOT"].endswith(r"runtime\logs")
    assert environment["FOULER_RUNTIME_CACHE_ROOT"].endswith(r"runtime\cache")
    assert environment["FOULER_RUNTIME_TEMP_ROOT"].endswith(r"runtime\temp")
    assert environment["DEKU_EVENT_QUEUE_ROOT"].endswith(r"runtime\events")
    assert environment["PS_USERNAME"] == "DekuFoulerFresh"
    assert environment["FOULER_ACTIVE_ACCOUNT"] == "DekuFoulerFresh"
    assert environment["FOULER_OBS_AUTHORITY_MANAGED"] == "1"
    assert environment["FOULER_OBS_WS_DISABLED"] == "1"
    assert environment["OBS_SERVER_HOST"] == "127.0.0.1"
    assert environment["OBS_SERVER_PORT"] == "8777"


def test_season_obs_rejects_invalid_authority(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        server,
        "validate_season_authority",
        lambda **_kwargs: {"ok": False, "blockers": ["wrong release", "expired"]},
    )

    try:
        server.configure_environment(
            authority_path=str(tmp_path / "season.json"),
            authority_sha256="c" * 64,
            environment={},
        )
    except RuntimeError as exc:
        assert "wrong release; expired" in str(exc)
    else:
        raise AssertionError("invalid finite-season authority was accepted")


def test_public_surface_forces_loopback_bind(monkeypatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_run_app(app: object, *args: object, **kwargs: object) -> None:
        calls.append({"app": app, "args": args, **kwargs})

    def fake_run_path(path: str, *, run_name: str) -> None:
        assert Path(path).name == "serve_obs_page.py"
        assert run_name == "__main__"
        web.run_app(object(), host="0.0.0.0", port=9999)

    monkeypatch.setattr(web, "run_app", fake_run_app)
    monkeypatch.setattr(server.runpy, "run_path", fake_run_path)

    server._run_public_surface()

    assert len(calls) == 1
    assert calls[0]["host"] == "127.0.0.1"
    assert calls[0]["port"] == 8777
    assert web.run_app is fake_run_app


def test_errors_redact_secret_environment_values(monkeypatch) -> None:
    secret = "finite-season-obs-secret-for-redaction"
    monkeypatch.setenv("FOULER_TEST_SECRET", secret)

    redacted = server._redact_sensitive_values(f"failure contained {secret}")

    assert secret not in redacted
    assert "[REDACTED]" in redacted


def test_authority_managed_surface_never_falls_back_to_legacy_lease(
    monkeypatch,
) -> None:
    monkeypatch.setattr(public_surface, "AUTHORITY_MANAGED_OBS", True)
    monkeypatch.setattr(
        public_surface,
        "_account_season_authority",
        lambda: {"ready": False, "account": "DekuFoulerFresh"},
    )
    monkeypatch.setattr(
        public_surface,
        "_runtime_lease_account",
        lambda: (_ for _ in ()).throw(
            AssertionError("legacy runtime lease must not be read")
        ),
    )
    monkeypatch.setenv("FOULER_ACTIVE_ACCOUNT", "DekuFoulerFresh")

    assert public_surface._configured_showdown_accounts() == ["DekuFoulerFresh"]


def test_authority_managed_flag_is_explicit() -> None:
    assert public_surface.authority_managed_obs({}) is False
    assert (
        public_surface.authority_managed_obs(
            {"FOULER_OBS_AUTHORITY_MANAGED": "1"}
        )
        is True
    )
