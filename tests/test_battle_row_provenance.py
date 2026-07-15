import json
import uuid

import pytest

import run


PROVENANCE_ENV_NAMES = tuple(
    dict.fromkeys(
        (
            "FOULER_SOURCE_COMMIT",
            "FOULER_SESSION_ID",
            *(name for name, _row_key in run._OPTIONAL_BATTLE_PROVENANCE_ENV),
        )
    )
)


def _clear_provenance_env(monkeypatch):
    for name in PROVENANCE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_process_provenance_prefers_configured_ids_without_git(monkeypatch):
    _clear_provenance_env(monkeypatch)
    monkeypatch.setenv("FOULER_SOURCE_COMMIT", "source-commit")
    monkeypatch.setenv("FOULER_SESSION_ID", "session-id")
    monkeypatch.setenv("FOULER_CHANGE_ID", "change-id")
    monkeypatch.setenv("FOULER_DEPLOYMENT_ID", "deployment-id")
    monkeypatch.setenv("FOULER_RUNTIME_LEASE_ID", "runtime-lease-id")

    def unexpected_git_call(*args, **kwargs):
        raise AssertionError("configured source commit must not invoke git")

    monkeypatch.setattr(run.subprocess, "run", unexpected_git_call)

    assert run._build_process_battle_provenance() == {
        "source_commit": "source-commit",
        "session_id": "session-id",
        "change_id": "change-id",
        "deployment_id": "deployment-id",
        "runtime_lease_id": "runtime-lease-id",
    }


def test_process_provenance_reads_git_and_generates_session_once(monkeypatch):
    _clear_provenance_env(monkeypatch)
    expected_commit = "a" * 40
    expected_session = uuid.UUID("12345678-1234-5678-1234-567812345678")
    git_calls = []

    def fake_git_call(command, **kwargs):
        git_calls.append((command, kwargs))
        return run.subprocess.CompletedProcess(command, 0, stdout=f"{expected_commit}\n")

    monkeypatch.setattr(run.subprocess, "run", fake_git_call)
    monkeypatch.setattr(run.uuid, "uuid4", lambda: expected_session)

    provenance = run._build_process_battle_provenance()

    assert provenance == {
        "source_commit": expected_commit,
        "session_id": str(expected_session),
    }
    assert len(git_calls) == 1
    command, kwargs = git_calls[0]
    assert command == ["git", "rev-parse", "HEAD"]
    assert kwargs["stderr"] is run.subprocess.DEVNULL


def test_process_battle_provenance_is_immutable():
    with pytest.raises(TypeError):
        run.BATTLE_ROW_PROVENANCE["session_id"] = "replacement"


def test_active_account_scope_requires_matching_protected_authority(tmp_path, monkeypatch):
    authority = tmp_path / "account-season.json"
    authority.write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-account-season/v1",
                "account": "DekuFoulerPilot",
                "seasonId": "dekufoulerpilot-gen9ou-20260715",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(run, "ACCOUNT_SEASON_FILE", authority)
    monkeypatch.setattr(
        run.FoulPlayConfig,
        "username",
        "Deku Fouler Pilot",
        raising=False,
    )

    assert run._active_account_scope(require_authority=True) == (
        "Deku Fouler Pilot",
        "dekufoulerpilot-gen9ou-20260715",
    )

    authority.write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-account-season/v1",
                "account": "DifferentAccount",
                "seasonId": "wrong-season",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="does not match"):
        run._active_account_scope(require_authority=True)


@pytest.mark.asyncio
async def test_all_battle_rows_reuse_process_provenance_without_git(
    tmp_path, monkeypatch
):
    stats_path = tmp_path / "battle_stats.json"
    monkeypatch.setattr(run, "BATTLE_STATS_FILE", stats_path)
    expected_provenance = dict(run.BATTLE_ROW_PROVENANCE)

    for name in PROVENANCE_ENV_NAMES:
        monkeypatch.setenv(name, f"changed-{name.lower()}")

    def unexpected_git_call(*args, **kwargs):
        raise AssertionError("recording a battle must not invoke git")

    def unexpected_uuid_call():
        raise AssertionError("recording a battle must not generate a session ID")

    monkeypatch.setattr(run.subprocess, "run", unexpected_git_call)
    monkeypatch.setattr(run.uuid, "uuid4", unexpected_uuid_call)

    stats = run.BattleStats()
    await stats.record_win("team-a", "battle-win")
    await stats.record_loss("team-b", "battle-loss")
    await stats.record_disconnect("team-c", "battle-disconnect")

    rows = json.loads(stats_path.read_text(encoding="utf-8"))["battles"]
    assert len(rows) == 3
    for row in rows:
        assert {key: row[key] for key in expected_provenance} == expected_provenance
        assert row["source_commit"]
        assert row["session_id"]
