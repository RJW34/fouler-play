import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from infrastructure import season_runtime_authority as authority

COMMIT = "a" * 40


def _write(path: Path, payload: dict) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _fixture(tmp_path: Path) -> tuple[Path, Path, dict, str]:
    release = tmp_path / "releases" / "fouler-play" / COMMIT
    release.mkdir(parents=True)
    manifest_path = tmp_path / "authority" / f"{COMMIT}.json"
    manifest_sha = _write(
        manifest_path,
        {
            "schemaVersion": "fouler-bootstrap-manifest/v1",
            "projectId": "fouler-play",
            "sourceCommit": COMMIT,
            "files": {},
        },
    )
    runtime = tmp_path / "runtime"
    for name in ("state", "logs", "cache", "temp", "events"):
        (runtime / name).mkdir(parents=True)
    for name, content in (
        ("fouler.env", "PS_USERNAME=DekuFoulerFresh\n"),
        (
            "account-season.json",
            (
                '{"schemaVersion":"fouler-play-account-season/v1",'
                '"account":"DekuFoulerFresh",'
                '"seasonId":"season-test-authority"}\n'
            ),
        ),
        (
            "control.json",
            (
                '{"schemaVersion":"devstream-runtime-control/v1",'
                '"state":"RUNNING","pauseEpoch":7}\n'
            ),
        ),
    ):
        (runtime / name).write_text(content, encoding="utf-8")
    now = datetime.now(timezone.utc)
    payload = {
        "schemaVersion": authority.SCHEMA_VERSION,
        "projectId": "fouler-play",
        "active": True,
        "seasonId": "season-test-authority",
        "generation": 1,
        "pauseEpoch": 7,
        "sourceCommit": COMMIT,
        "releaseRoot": str(release),
        "releaseManifestPath": str(manifest_path),
        "releaseManifestSha256": manifest_sha,
        "machine": "JIGGLYPUFF",
        "account": "DekuFoulerFresh",
        "proofWindow": {
            "startsAt": (now - timedelta(minutes=5)).isoformat(),
            "expiresAt": (now + timedelta(hours=2)).isoformat(),
        },
        "limits": {"roundSize": 30, "maxRounds": 2, "maxGames": 60},
        "battleScope": {
            "botMode": "search_ladder",
            "websocketUri": "wss://sim3.psim.us/showdown/websocket",
            "pokemonFormat": "gen9ou",
            "teamName": "gen9/ou/fat-team-2-balance",
            "maxConcurrentBattles": 3,
            "searchParallelism": 2,
            "replayBehavior": "always",
        },
        "stopLoss": {"ratingWindow": 60, "maxRatingDrawdown": 75},
        "grants": {
            "automaticRoundContinuation": True,
            "sourceChanges": False,
            "teamChanges": False,
            "automaticImprovement": False,
            "publicOutput": False,
        },
        "runtime": {
            "stateRoot": str(runtime / "state"),
            "logRoot": str(runtime / "logs"),
            "cacheRoot": str(runtime / "cache"),
            "tempRoot": str(runtime / "temp"),
            "secretEnvFile": str(runtime / "fouler.env"),
            "accountSeasonPath": str(runtime / "account-season.json"),
            "eventQueueRoot": str(runtime / "events"),
            "controlPath": str(runtime / "control.json"),
        },
    }
    authority_path = tmp_path / "authority" / "season.json"
    digest = _write(authority_path, payload)
    return release, authority_path, payload, digest


def _validate(monkeypatch, release: Path, path: Path, digest: str, **kwargs):
    monkeypatch.setattr(authority, "_git_head", lambda _root: COMMIT)
    monkeypatch.setattr(authority, "_tracked_git_status", lambda _root: "")
    return authority.validate_season_authority(
        authority_path=path,
        expected_sha256=digest,
        release_root=release,
        requested_account="DekuFoulerFresh",
        requested_bot_mode="search_ladder",
        requested_websocket_uri="wss://sim3.psim.us/showdown/websocket",
        requested_pokemon_format="gen9ou",
        requested_team_name="gen9/ou/fat-team-2-balance",
        requested_run_count=30,
        requested_max_concurrent_battles=3,
        requested_search_parallelism=2,
        requested_replay_behavior="always",
        require_existing_paths=True,
        hostname="JIGGLYPUFF",
        **kwargs,
    )


def test_finite_season_authority_accepts_exact_bounded_identity(monkeypatch, tmp_path):
    release, path, _payload, digest = _fixture(tmp_path)

    result = _validate(monkeypatch, release, path, digest)

    assert result["ok"] is True
    assert result["authorityType"] == "finite-season"
    assert result["season"]["roundSize"] == 30
    assert result["season"]["maxGames"] == 60


def test_finite_season_authority_rejects_digest_commit_account_and_unbounded_budget(
    monkeypatch, tmp_path
):
    release, path, payload, _digest = _fixture(tmp_path)
    payload["sourceCommit"] = "b" * 40
    payload["account"] = "WrongAccount"
    payload["limits"] = {"roundSize": 30, "maxRounds": 5, "maxGames": 150}
    digest = _write(path, payload)
    monkeypatch.setattr(authority, "_git_head", lambda _root: COMMIT)
    monkeypatch.setattr(authority, "_tracked_git_status", lambda _root: "")

    result = authority.validate_season_authority(
        authority_path=path,
        expected_sha256="0" * 64,
        release_root=release,
        requested_account="DekuFoulerFresh",
        hostname="JIGGLYPUFF",
    )

    blockers = "; ".join(result["blockers"])
    assert result["ok"] is False
    assert "pinned digest" in blockers
    assert "release HEAD" in blockers
    assert "account does not match" in blockers
    assert "maxRounds exceeds" in blockers
    assert "maxGames exceeds" in blockers
    assert digest != "0" * 64


def test_finite_season_child_requires_exact_direct_supervisor_binding(
    monkeypatch, tmp_path
):
    release, path, _payload, digest = _fixture(tmp_path)
    monkeypatch.setattr(authority, "_git_head", lambda _root: COMMIT)
    monkeypatch.setattr(authority, "_tracked_git_status", lambda _root: "")
    monkeypatch.setattr(authority.os, "getppid", lambda: 222)

    class FakeProcess:
        def __init__(self, pid):
            assert pid == 222

        def create_time(self):
            return 123.0

        def cmdline(self):
            return ["python.exe", str(release / "scripts" / "wrong.py")]

        def cwd(self):
            return str(release)

    monkeypatch.setattr(authority.psutil, "Process", FakeProcess)
    result = authority.validate_season_authority(
        authority_path=path,
        expected_sha256=digest,
        release_root=release,
        requested_account="DekuFoulerFresh",
        require_child_binding=True,
        environ={
            authority.SUPERVISOR_PID_ENV: "222",
            authority.SUPERVISOR_CREATE_TIME_ENV: "123",
            authority.SUPERVISOR_NONCE_ENV: "f" * 64,
        },
        hostname="JIGGLYPUFF",
    )

    assert result["ok"] is False
    assert any("not the season ladder supervisor" in item for item in result["blockers"])


def test_finite_season_rejects_policy_grant_stop_loss_and_generation_drift(
    monkeypatch, tmp_path
):
    release, path, payload, _digest = _fixture(tmp_path)
    payload["proofWindow"]["expiresAt"] = (
        datetime.now(timezone.utc) + timedelta(hours=80)
    ).isoformat()
    payload["stopLoss"]["maxRatingDrawdown"] = 100
    payload["grants"]["automaticImprovement"] = True
    payload["grants"]["publicOutput"] = True
    account_season = Path(payload["runtime"]["accountSeasonPath"])
    account_season.write_text(
        '{"schemaVersion":"fouler-play-account-season/v1",'
        '"account":"DekuFoulerFresh","seasonId":"stale-generation"}\n',
        encoding="utf-8",
    )
    digest = _write(path, payload)

    result = _validate(monkeypatch, release, path, digest)

    blockers = "; ".join(result["blockers"])
    assert result["ok"] is False
    assert "72-hour cap" in blockers
    assert "maxRatingDrawdown must equal 75" in blockers
    assert "automaticImprovement violates" in blockers
    assert "publicOutput violates" in blockers
    assert "account-season authority seasonId does not match" in blockers
