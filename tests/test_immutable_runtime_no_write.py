from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from infrastructure.runtime_paths import RuntimePathError, resolve_runtime_paths


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
OWNED_RUNTIME_FILES = (
    "data/pkmn_sets.py",
    "fp/decision_trace.py",
    "fp/matchup_analyzer.py",
    "fp/matchup_memory.py",
    "fp/movepool_tracker.py",
    "fp/run_battle.py",
    "fp/search/main.py",
    "infrastructure/deployment_lineage.py",
    "infrastructure/elo_watchdog.py",
    "infrastructure/event_poster.py",
    "infrastructure/event_queue_lib.py",
    "infrastructure/runtime_paths.py",
    "replay_analysis/analyzer.py",
    "replay_analysis/autoresearch.py",
    "replay_analysis/batch_analyzer.py",
    "replay_analysis/detailed_impact_analysis.py",
    "replay_analysis/feedback_tracker.py",
    "replay_analysis/hypothesis_ledger.py",
    "replay_analysis/local_batch_analysis.py",
    "replay_analysis/team_performance.py",
    "replay_analysis/test_batch_local.py",
    "replay_analysis/turn_review.py",
    "scripts/devstream_session.py",
    "scripts/refresh_matchup_weights.py",
    "streaming/hybrid_dashboard.py",
    "streaming/serve_obs_page.py",
    "streaming/state_store.py",
)
# Current protected/excluded entrypoints and their dirty authority dependencies
# are overlaid into the clone for testing only. This lane does not edit them.
REHEARSAL_DEPENDENCIES = (
    "config.py",
    "infrastructure/discord_reporting.py",
    "infrastructure/deployment_state.py",
    "infrastructure/runtime_authorization.py",
    "infrastructure/runtime_lease_client.py",
    "pipeline.py",
    "process_lock.py",
    "run.py",
    "scripts/devstream_runtime_checks.py",
    "scripts/devstream_runtime_lease.py",
    "teams/load_team.py",
)


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )


def _snapshot_tree(root: Path) -> dict[str, tuple[object, ...]]:
    """Inventory every directory, file, link, ignored artifact, and .git byte."""
    snapshot: dict[str, tuple[object, ...]] = {".": ("dir",)}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        metadata = path.lstat()
        mode = stat.S_IMODE(metadata.st_mode)
        if path.is_symlink():
            snapshot[relative] = ("link", mode, os.readlink(path))
        elif path.is_dir():
            snapshot[relative] = ("dir", mode)
        elif path.is_file():
            snapshot[relative] = (
                "file",
                mode,
                metadata.st_size,
                hashlib.sha256(path.read_bytes()).hexdigest(),
            )
        else:
            snapshot[relative] = ("other", mode, metadata.st_size)
    return snapshot


def _make_rehearsal_release(tmp_path: Path) -> Path:
    release = tmp_path / "release"
    clone = _run(
        ["git", "clone", "--quiet", "--depth", "1", "--no-local", str(ROOT), str(release)],
        cwd=tmp_path,
    )
    assert clone.returncode == 0, clone.stderr

    for relative in (*OWNED_RUNTIME_FILES, *REHEARSAL_DEPENDENCIES):
        source = ROOT / relative
        assert source.is_file(), relative
        target = release / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    # A historical hot-reload marker is committed into the release so the
    # rehearsal proves importing/calling the decision runtime does not delete it.
    (release / ".reload").write_text("must-remain\n", encoding="ascii")
    commands = (
        ["git", "config", "user.email", "immutable-rehearsal@test.invalid"],
        ["git", "config", "user.name", "Immutable Rehearsal"],
        ["git", "add", "-A"],
        ["git", "commit", "--quiet", "-m", "immutable runtime rehearsal"],
    )
    for command in commands:
        result = _run(command, cwd=release)
        assert result.returncode == 0, result.stderr

    ignored_dir = release / "logs"
    ignored_dir.mkdir(parents=True, exist_ok=True)
    (ignored_dir / "ignored-proof.txt").write_text("ignored bytes must remain\n", encoding="ascii")
    return release


def _runtime_environment(tmp_path: Path) -> tuple[dict[str, str], dict[str, Path]]:
    external = tmp_path / "external"
    paths = {
        "state": external / "state",
        "log": external / "logs",
        "cache": external / "cache",
        "temp": external / "temp",
        "authority": external / "authority",
        "secrets": external / "secrets",
    }
    for path in paths.values():
        path.mkdir(parents=True, exist_ok=True)

    environment = os.environ.copy()
    for name in (
        "FOULER_DEPLOYMENT_ID",
        "FOULER_DEPLOYMENT_RECEIPT_PATH",
        "FOULER_RUNTIME_AUTHORIZATION_SHA256",
        "FOULER_RUNTIME_LEASE_PATH",
        "PS_PASSWORD",
    ):
        environment.pop(name, None)
    environment.update(
        {
            "APPDATA": str(paths["temp"] / "appdata"),
            "LOCALAPPDATA": str(paths["temp"] / "localappdata"),
            "PROGRAMDATA": str(paths["temp"] / "programdata"),
            "HOME": str(paths["temp"] / "home"),
            "USERPROFILE": str(paths["temp"] / "home"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": "",
            "GIT_OPTIONAL_LOCKS": "0",
            "FOULER_RUNTIME_PRODUCTION": "1",
            "FOULER_RUNTIME_STATE_ROOT": str(paths["state"]),
            "FOULER_RUNTIME_LOG_ROOT": str(paths["log"]),
            "FOULER_RUNTIME_CACHE_ROOT": str(paths["cache"]),
            "FOULER_RUNTIME_TEMP_ROOT": str(paths["temp"]),
            "FOULER_BATTLE_STATS_PATH": str(paths["state"] / "battle_stats.json"),
            "FOULER_MATCHUP_WEIGHTS_PATH": str(paths["state"] / "learning" / "matchup_weights.json"),
            "FOULER_MOVEPOOL_DATA_PATH": str(paths["state"] / "learning" / "movepool_data.json"),
            "MATCHUP_MEMORY_AB_LOG": str(paths["log"] / "matchup_ab_log.jsonl"),
            "DECISION_TRACE_DIR": str(paths["log"] / "decision_traces"),
            "FOULER_PUBLIC_BATTLE_VIEW_PATH": str(paths["log"] / "decision_traces" / "latest-public-battle.json"),
            "FOULER_ACCOUNT_SEASON_PATH": str(paths["authority"] / "account-season.json"),
            "FOULER_BATTLE_DIGEST_STATE": str(paths["state"] / "truth" / "battle-report-digest-state.json"),
            "FOULER_HYPOTHESIS_LEDGER": str(paths["state"] / "learning" / "hypotheses"),
            "FOULER_LOG_DIR": str(paths["log"]),
            "FOULER_ENV_FILE": str(paths["secrets"] / "fouler.env"),
            "EVENT_POSTER_LOG": str(paths["log"] / "event_poster.log"),
            "EVENT_QUEUE_FILE": str(paths["state"] / "events_queue.json"),
            "EVENT_QUEUE_BACKLOG_ARCHIVE_DIR": str(paths["log"] / "discord-events"),
            "DEKU_EVENT_QUEUE_ROOT": str(paths["state"] / "deku-events"),
            "MATCHUP_ANALYZER_ENABLE_LLM": "0",
            "MATCHUP_MEMORY_AB": "1",
            "PS_USERNAME": "immutable-rehearsal",
            "SHOWDOWN_USER_ID": "immutable-rehearsal",
            "TEMP": str(paths["temp"]),
            "TMP": str(paths["temp"]),
        }
    )
    authority_file = Path(environment["FOULER_ACCOUNT_SEASON_PATH"])
    authority_file.write_text(
        json.dumps(
            {
                "schemaVersion": "fouler-play-account-season/v1",
                "seasonId": "immutable-rehearsal-season",
                "account": "immutable-rehearsal",
            }
        ),
        encoding="utf-8",
    )
    authority_file.chmod(stat.S_IREAD)
    env_file = Path(environment["FOULER_ENV_FILE"])
    env_file.parent.mkdir(parents=True, exist_ok=True)
    env_file.write_text("# protected immutable rehearsal environment\n", encoding="utf-8")
    env_file.chmod(stat.S_IREAD)
    return environment, paths


def _write_read_only_json(path: Path, payload: dict[str, object]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    path.chmod(stat.S_IREAD)
    return path


def _production_policy_environment(tmp_path: Path) -> tuple[dict[str, str], Path, Path]:
    external = tmp_path / "policy-external"
    roots = {
        "FOULER_RUNTIME_STATE_ROOT": external / "state",
        "FOULER_RUNTIME_LOG_ROOT": external / "logs",
        "FOULER_RUNTIME_CACHE_ROOT": external / "cache",
        "FOULER_RUNTIME_TEMP_ROOT": external / "temp",
    }
    for root in roots.values():
        root.mkdir(parents=True, exist_ok=True)
    secret = external / "secrets" / "fouler.env"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("PS_USERNAME=DekuFoulerLab\n", encoding="utf-8")
    secret.chmod(stat.S_IREAD)
    authority = external / "authority" / "account-season.json"
    environment = {
        "FOULER_RUNTIME_PRODUCTION": "1",
        **{name: str(path) for name, path in roots.items()},
        "FOULER_ENV_FILE": str(secret),
        "FOULER_ACCOUNT_SEASON_PATH": str(authority),
        "PS_USERNAME": "DekuFoulerLab",
        "SHOWDOWN_USER_ID": "DekuFoulerLab",
        "PS_PASSWORD": "test-only-placeholder",
    }
    return environment, secret, authority


def test_windows_defaults_are_external_programdata_roots(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    program_data = tmp_path / "ProgramData"
    environment = {
        "PROGRAMDATA": str(program_data),
        "FOULER_RUNTIME_PRODUCTION": "1",
    }

    paths = resolve_runtime_paths(
        release,
        environ=environment,
        platform_name="nt",
        require_existing=False,
    )

    assert paths.state_root == (program_data / "HERMES" / "state" / "fouler").resolve()
    assert paths.log_root == (program_data / "HERMES" / "logs" / "fouler").resolve()
    assert paths.cache_root == (program_data / "HERMES" / "cache" / "fouler").resolve()
    assert paths.production is True
    assert not paths.state_root.is_relative_to(release.resolve())


@pytest.mark.parametrize("overlap_kind", ["equal", "inside", "parent"])
def test_runtime_paths_reject_release_overlap(tmp_path, overlap_kind):
    release = tmp_path / "release"
    release.mkdir()
    external = tmp_path / "external"
    log_root = external / "logs"
    cache_root = external / "cache"
    temp_root = external / "temp"
    for path in (log_root, cache_root, temp_root):
        path.mkdir(parents=True)
    state_root = {
        "equal": release,
        "inside": release / "runtime-state",
        "parent": tmp_path,
    }[overlap_kind]
    state_root.mkdir(parents=True, exist_ok=True)
    environment = {
        "FOULER_RUNTIME_PRODUCTION": "1",
        "FOULER_RUNTIME_STATE_ROOT": str(state_root),
        "FOULER_RUNTIME_LOG_ROOT": str(log_root),
        "FOULER_RUNTIME_CACHE_ROOT": str(cache_root),
        "FOULER_RUNTIME_TEMP_ROOT": str(temp_root),
    }

    with pytest.raises(RuntimePathError, match="overlaps immutable release"):
        resolve_runtime_paths(release, environ=environment)


def test_production_runtime_paths_require_preprovisioned_roots(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    external = tmp_path / "external"
    environment = {
        "FOULER_RUNTIME_PRODUCTION": "1",
        "FOULER_RUNTIME_STATE_ROOT": str(external / "state"),
        "FOULER_RUNTIME_LOG_ROOT": str(external / "logs"),
        "FOULER_RUNTIME_CACHE_ROOT": str(external / "cache"),
        "FOULER_RUNTIME_TEMP_ROOT": str(external / "temp"),
    }

    with pytest.raises(RuntimePathError, match="required runtime directories are missing"):
        resolve_runtime_paths(release, environ=environment)


def test_lease_bound_runtime_cannot_disable_production_validation(tmp_path):
    release = tmp_path / "release"
    release.mkdir()
    environment = {
        "FOULER_RUNTIME_PRODUCTION": "0",
        "FOULER_RUNTIME_LEASE_PATH": str(tmp_path / "authority" / "runtime-lease.json"),
        "FOULER_RUNTIME_STATE_ROOT": str(tmp_path / "external" / "state"),
        "FOULER_RUNTIME_LOG_ROOT": str(tmp_path / "external" / "logs"),
        "FOULER_RUNTIME_CACHE_ROOT": str(tmp_path / "external" / "cache"),
        "FOULER_RUNTIME_TEMP_ROOT": str(tmp_path / "external" / "temp"),
    }

    with pytest.raises(RuntimePathError, match="required runtime directories are missing"):
        resolve_runtime_paths(release, environ=environment)


def test_windows_production_loads_only_protected_env_file(tmp_path, monkeypatch):
    from scripts import devstream_session

    release = tmp_path / "release"
    release.mkdir()
    release_env = release / ".env"
    release_deku_env = release / ".env.deku"
    release_env.write_text("RELEASE_FALLBACK=forbidden\n", encoding="utf-8")
    release_deku_env.write_text("DEKU_FALLBACK=forbidden\n", encoding="utf-8")
    environment, secret, _authority = _production_policy_environment(tmp_path)
    monkeypatch.setattr(devstream_session, "ROOT", release)
    monkeypatch.setattr(
        devstream_session,
        "ENV_FILES",
        [secret, release_env, release_deku_env],
    )

    loaded = devstream_session.load_env_files(environ=environment, platform_name="nt")
    permissions = devstream_session.secure_env_files(
        execute=False,
        env=environment,
        platform_name="nt",
    )

    assert loaded["PS_USERNAME"] == "DekuFoulerLab"
    assert "RELEASE_FALLBACK" not in loaded
    assert "DEKU_FALLBACK" not in loaded
    assert [item["path"] for item in permissions] == [str(secret)]
    assert permissions[0]["ok"] is True


def test_windows_production_rejects_release_local_env_even_if_configured(tmp_path, monkeypatch):
    from scripts import devstream_session

    release = tmp_path / "release"
    release.mkdir()
    release_env = release / ".env"
    release_env.write_text("PS_USERNAME=forbidden\n", encoding="utf-8")
    release_env.chmod(stat.S_IREAD)
    environment, _secret, _authority = _production_policy_environment(tmp_path)
    environment["FOULER_ENV_FILE"] = str(release_env)
    monkeypatch.setattr(devstream_session, "ROOT", release)

    loaded = devstream_session.load_env_files(environ=environment, platform_name="nt")
    permissions = devstream_session.secure_env_files(
        execute=False,
        env=environment,
        platform_name="nt",
    )

    assert loaded["PS_USERNAME"] == "DekuFoulerLab"
    assert permissions[0]["ok"] is False
    assert any("immutable release" in item for item in permissions[0]["blockers"])


def test_lease_account_marker_never_overwrites_configured_account(monkeypatch):
    from scripts import devstream_session

    monkeypatch.setattr(
        devstream_session,
        "runtime_lease_account",
        lambda _args, _env: "LeaseAccount",
    )
    configured = {
        "PS_USERNAME": "ConfiguredAccount",
        "SHOWDOWN_USER_ID": "ConfiguredAccount",
        "SHOWDOWN_ACCOUNTS": "ConfiguredAccount",
        "FOULER_ACTIVE_ACCOUNT": "ConfiguredAccount",
    }

    result = devstream_session.apply_runtime_lease_account(
        configured,
        argparse.Namespace(runtime_lease="validated.json"),
    )

    assert result["PS_USERNAME"] == "ConfiguredAccount"
    assert result["SHOWDOWN_USER_ID"] == "ConfiguredAccount"
    assert result["SHOWDOWN_ACCOUNTS"] == "ConfiguredAccount"
    assert result["FOULER_ACTIVE_ACCOUNT"] == "ConfiguredAccount"
    assert result["FOULER_RUNTIME_LEASE_ACCOUNT"] == "LeaseAccount"


def test_account_season_authority_uses_protected_external_file(tmp_path):
    from scripts import devstream_session

    environment, _secret, authority = _production_policy_environment(tmp_path)
    _write_read_only_json(
        authority,
        {
            "schemaVersion": "fouler-play-account-season/v1",
            "seasonId": "pilot-20260715",
            "account": "Deku Fouler-Lab",
        },
    )
    guard = {"ok": True, "lease": {"account": "dekufoulerlab"}}

    check = devstream_session.account_season_authority_check(
        guard,
        env=environment,
        platform_name="nt",
    )

    assert check["ok"] is True
    assert check["runtimeMirrorAuthoritative"] is False
    assert check["readOnly"] is True
    assert check["seasonId"] == "pilot-20260715"


@pytest.mark.parametrize("failure", ["missing", "mismatch", "malformed", "writable", "overlap"])
def test_account_season_authority_fails_closed(tmp_path, failure):
    from scripts import devstream_session

    environment, _secret, authority = _production_policy_environment(tmp_path)
    if failure == "overlap":
        authority = Path(environment["FOULER_RUNTIME_STATE_ROOT"]) / "truth" / "account-season.json"
        environment["FOULER_ACCOUNT_SEASON_PATH"] = str(authority)
    if failure == "malformed":
        authority.parent.mkdir(parents=True, exist_ok=True)
        authority.write_text(
            '{"schemaVersion":"fouler-play-account-season/v1",'
            '"account":"DekuFoulerLab","account":"other","seasonId":"pilot"}',
            encoding="utf-8",
        )
        authority.chmod(stat.S_IREAD)
    elif failure != "missing":
        _write_read_only_json(
            authority,
            {
                "schemaVersion": "fouler-play-account-season/v1",
                "seasonId": "pilot-20260715",
                "account": "OtherAccount" if failure == "mismatch" else "DekuFoulerLab",
            },
        )
        if failure == "writable":
            authority.chmod(stat.S_IREAD | stat.S_IWRITE)

    check = devstream_session.account_season_authority_check(
        {"ok": True, "lease": {"account": "DekuFoulerLab"}},
        env=environment,
        platform_name="nt",
    )

    assert check["ok"] is False
    joined = " ".join(check["blockers"])
    expected = {
        "missing": "is missing",
        "mismatch": "does not match",
        "malformed": "malformed",
        "writable": "is writable",
        "overlap": "overlaps runtime state root",
    }[failure]
    assert expected in joined


def test_account_season_authority_rejects_symlink_or_reparse(tmp_path):
    from scripts import devstream_session

    environment, _secret, authority = _production_policy_environment(tmp_path)
    target = _write_read_only_json(
        authority.with_name("real-account-season.json"),
        {
            "schemaVersion": "fouler-play-account-season/v1",
            "seasonId": "pilot-20260715",
            "account": "DekuFoulerLab",
        },
    )
    try:
        authority.symlink_to(target)
    except OSError as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    check = devstream_session.account_season_authority_check(
        {"ok": True, "lease": {"account": "DekuFoulerLab"}},
        env=environment,
        platform_name="nt",
    )

    assert check["ok"] is False
    assert any("symlink or reparse" in item for item in check["blockers"])


@pytest.mark.parametrize("authority_state", ["missing", "mismatch"])
def test_live_preflight_blocks_missing_or_mismatched_canonical_season(
    tmp_path,
    monkeypatch,
    authority_state,
):
    from scripts import devstream_session

    environment, _secret, authority = _production_policy_environment(tmp_path)
    if authority_state == "mismatch":
        _write_read_only_json(
            authority,
            {
                "schemaVersion": "fouler-play-account-season/v1",
                "seasonId": "pilot-20260715",
                "account": "OtherAccount",
            },
        )
    monkeypatch.setattr(
        devstream_session,
        "recent_showdown_credential_failure",
        lambda _root: {"found": False, "code": None},
    )
    args = argparse.Namespace(
        max_concurrent_battles=3,
        runtime_lease=str(tmp_path / "runtime-lease.json"),
    )
    guard = {"ok": True, "lease": {"account": "DekuFoulerLab"}}

    preflight = devstream_session.runtime_launch_preflight(
        args,
        env=environment,
        lease_guard=guard,
    )

    assert preflight["ok"] is False
    assert preflight["accountSeasonAuthority"]["ok"] is False
    expected = "is missing" if authority_state == "missing" else "does not match"
    assert expected in " ".join(preflight["blockers"])


def test_live_preflight_requires_exactly_three_concurrent_battles(tmp_path, monkeypatch):
    from scripts import devstream_session

    environment, _secret, authority = _production_policy_environment(tmp_path)
    _write_read_only_json(
        authority,
        {
            "schemaVersion": "fouler-play-account-season/v1",
            "seasonId": "pilot-20260715",
            "account": "DekuFoulerLab",
        },
    )
    monkeypatch.setattr(
        devstream_session,
        "recent_showdown_credential_failure",
        lambda _root: {"found": False, "code": None},
    )
    guard = {"ok": True, "lease": {"account": "DekuFoulerLab"}}

    blocked = devstream_session.runtime_launch_preflight(
        argparse.Namespace(max_concurrent_battles=2, runtime_lease="validated.json"),
        env=environment,
        lease_guard=guard,
    )
    allowed = devstream_session.runtime_launch_preflight(
        argparse.Namespace(max_concurrent_battles=3, runtime_lease="validated.json"),
        env=environment,
        lease_guard=guard,
    )

    assert blocked["ok"] is False
    assert "must equal three" in " ".join(blocked["blockers"])
    assert allowed["ok"] is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/event",
        "http://127.0.0.2/event",
        "http://localhost.example/event",
        "ftp://localhost/event",
        "http://user@localhost/event",
        "not-a-url",
    ],
)
def test_stream_event_reporting_rejects_non_loopback_urls(monkeypatch, url):
    from fp import run_battle

    class UnexpectedSession:
        def __init__(self, *args, **kwargs):
            raise AssertionError("non-loopback event URL must not construct an HTTP client")

    monkeypatch.setenv("FOULER_STREAM_EVENTS", "1")
    monkeypatch.delenv("FOULER_OFFLINE_EVAL", raising=False)
    monkeypatch.setenv("STREAM_EVENT_URL", url)
    monkeypatch.setattr(run_battle.aiohttp, "ClientSession", UnexpectedSession)

    result = asyncio.run(run_battle.send_stream_event("TEST", {"ok": True}))

    assert result == {"skipped": True, "reason": "stream-event-url-not-loopback"}


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:8777/event",
        "https://localhost/event",
        "http://[::1]:8777/event",
    ],
)
def test_stream_event_loopback_url_validation_accepts_only_expected_hosts(url):
    from fp import run_battle

    assert run_battle._validated_loopback_stream_event_url(url) == url


def test_immutable_release_runtime_surfaces_write_only_external_state(tmp_path):
    release = _make_rehearsal_release(tmp_path)
    environment, external = _runtime_environment(tmp_path)
    authority_file = Path(environment["FOULER_ACCOUNT_SEASON_PATH"])
    authority_before = authority_file.read_bytes()
    before = _snapshot_tree(release)
    index_before = (release / ".git" / "index").read_bytes()

    rehearsal = r'''
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

release = Path.cwd()
state_root = Path(os.environ["FOULER_RUNTIME_STATE_ROOT"])
log_root = Path(os.environ["FOULER_RUNTIME_LOG_ROOT"])
cache_root = Path(os.environ["FOULER_RUNTIME_CACHE_ROOT"])
temp_root = Path(os.environ["FOULER_RUNTIME_TEMP_ROOT"])
external_roots = (state_root, log_root, cache_root, temp_root)

def assert_external(path):
    candidate = Path(path).resolve()
    assert any(candidate.is_relative_to(root.resolve()) for root in external_roots), candidate

from infrastructure import deployment_lineage

identity = deployment_lineage.checkout_identity(release)
assert identity["ready"], identity["blockers"]

from streaming import state_store

state_store.write_active_battles(
    {
        "battles": [
            {"id": f"battle-gen9ou-{slot}", "opponent": f"opponent-{slot}", "slot": slot}
            for slot in range(1, 4)
        ],
        "count": 3,
        "max_slots": 3,
    }
)
state_store.update_daily_stats(2, 1)
state_store.write_next_fix("Review the bounded external replay sample")

from fp.decision_trace import write_decision_trace

trace_path = write_decision_trace(
    {
        "battle_tag": "battle-gen9ou-1",
        "turn": 1,
        "timestamp": "2026-07-15T00:00:00+00:00",
        "format": "gen9ou",
        "choice": "move recover",
        "decision_mode": "mcts",
        "hybrid_status": "applied",
        "snapshot": {
            "user": {
                "account": "immutable-rehearsal",
                "active": {"name": "pikachu", "hp": 100, "max_hp": 100},
                "reserve": [],
            },
            "opponent": {
                "account": "opponent-1",
                "active": {"name": "eevee", "hp": 100, "max_hp": 100},
                "reserve": [],
            },
        },
    }
)
assert trace_path
assert_external(trace_path)
trace_dir = Path(os.environ["DECISION_TRACE_DIR"])

from streaming.hybrid_dashboard import DashboardDataProvider

dashboard = DashboardDataProvider(trace_dir=trace_dir, scan_interval_sec=0.2)
assert dashboard.get_state_payload()["active_battle_count"] == 3
dashboard.get_turns_payload(limit=10)

from data import pkmn_sets

class _SetsResponse:
    status_code = 200
    def json(self):
        return {"pikachu": {"moves": ["thunderbolt"]}}

pkmn_sets.requests.get = lambda *args, **kwargs: _SetsResponse()
sets_target = Path(pkmn_sets.PKMN_SETS_CACHE_DIR) / "immutable-rehearsal.json"
assert pkmn_sets.get_sets_file(sets_target.as_posix(), "https://example.invalid/sets")
assert_external(sets_target)

from fp import matchup_analyzer

gameplan = matchup_analyzer.Gameplan(
    opponent_win_condition="hazards",
    opponent_weaknesses=["limited recovery"],
    our_strategy="preserve removal",
    key_pivot_triggers=["pivot on hazard setter"],
    win_condition="endgame recovery loop",
)
matchup_analyzer._save_gameplan_cache("ours", "theirs", gameplan)

from fp.movepool_tracker import MovepoolTracker

tracker = MovepoolTracker()
tracker.record_battle_appearance("pikachu")
tracker.record_move("pikachu", "thunderbolt")
tracker.save()

from fp import matchup_memory

weights = matchup_memory.update_weights_from_artifacts([])
matchup_memory.write_weights(weights)
assert matchup_memory.load_weights()["artifact_count"] == 0
matchup_memory._log_ab_arm("battle-gen9ou-1", "on", "pikachu", None)

from scripts import refresh_matchup_weights

refresh_matchup_weights.configure_logging()
refresh_matchup_weights.log.info("immutable rehearsal external log")

from config import FoulPlayConfig, init_logging

init_logging(logging.INFO, True)
logging.getLogger("immutable-rehearsal").info("protected config external log")
assert (log_root / "init.log").is_file()

from fp import run_battle

assert run_battle.RUNTIME_STATE_ROOT == state_root.resolve()
assert run_battle.RUNTIME_LOG_ROOT == log_root.resolve()
run_battle.cleanup_old_logs()
worker_handler = run_battle._get_or_create_worker_handler(911)
assert_external(worker_handler.baseFilename)
logging.getLogger().removeHandler(worker_handler)
worker_handler.close()
run_battle._worker_handlers.pop(911, None)

class _ReplayResponse:
    status = 200
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    async def json(self, **kwargs):
        return {
            "id": "gen9ou-immutable-replay",
            "winner": "immutable-rehearsal",
            "log": "|player|p1|immutable-rehearsal\n|player|p2|opponent\n|win|immutable-rehearsal",
        }

class _ReplaySession:
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False
    def get(self, *args, **kwargs):
        return _ReplayResponse()

run_battle.aiohttp.ClientSession = _ReplaySession
saved_replay = asyncio.run(
    run_battle._save_replay_json_locally("battle-gen9ou-immutable-replay")
)
assert saved_replay["id"] == "gen9ou-immutable-replay"
replay_path = state_root / "replay_analysis" / "gen9ou-immutable-replay.json"
assert replay_path.is_file()

battle_stats = Path(os.environ["FOULER_BATTLE_STATS_PATH"])
battle_stats.write_text(
    json.dumps(
        {
            "battles": [
                {
                    "battle_id": "battle-gen9ou-immutable-replay",
                    "replay_id": "battle-gen9ou-immutable-replay",
                    "team_file": "fixture-team",
                    "result": "win",
                    "timestamp": "2026-07-15T00:00:00+00:00",
                    "rating": 1000,
                    "account": "immutable-rehearsal",
                }
            ]
        }
    ),
    encoding="utf-8",
)
assert asyncio.run(
    run_battle._enrich_battle_stats_rating_once(
        "battle-gen9ou-immutable-replay",
        elo_before=1000,
        elo_after=1012,
        rating_delta=12,
        result_key="win",
        winner="immutable-rehearsal",
        opponent_name="opponent",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-immutable-replay",
    )
)

from infrastructure import elo_watchdog

seen = {}
elo_watchdog.current_deployment_context = lambda **kwargs: {
    "activation": {"activationId": "activation-rehearsal", "deploymentId": "deployment-rehearsal"},
    "blockers": [],
}
elo_watchdog.read_battle_rows = lambda path: seen.setdefault("path", Path(path)) and []
judgment = elo_watchdog.check_and_judge(state_root=state_root)
assert judgment["status"] == "waiting-for-sample", judgment
assert seen["path"] == battle_stats

import run as run_entry

assert run_entry.RUNTIME_STATE_ROOT == state_root.resolve()
assert run_entry.BATTLE_STATS_FILE == battle_stats.resolve()
run_stats = run_entry.BattleStats()
run_stats._record_battle(
    "fixture-team",
    "win",
    "battle-gen9ou-protected-entrypoint",
    rating=1014,
)

import process_lock

assert_external(process_lock.PID_FILE)
assert Path(process_lock.PID_FILE).parent == state_root.resolve() / "pids"

from teams.load_team import TeamListIterator

team_iterator = TeamListIterator(["fixture-team-a", "fixture-team-b"])
assert team_iterator.get_next_team() == "fixture-team-a"
assert (state_root / "team-rotation.index").is_file()

import pipeline

assert pipeline.RUNTIME_STATE_ROOT == state_root.resolve()
analysis_pipeline = pipeline.Pipeline()
analysis_pipeline._save_state(2, 1)
assert (state_root / "pipeline-state.json").is_file()

from replay_analysis.autoresearch import run_autoresearch

autoresearch_report = run_autoresearch(last_n=2, queue_discord=False)
assert (state_root / "replay_analysis" / "autoresearch_latest.json").is_file()
assert autoresearch_report["window_size"] >= 1

from replay_analysis import hypothesis_ledger

hypothesis_path = hypothesis_ledger.emit_from_issue(
    {
        "key": "immutable_no_write",
        "title": "Immutable runtime proof",
        "summary": "All runtime writers remained external.",
        "recommendation": "Keep release writes disabled.",
        "proof": ["complete tree snapshot"],
    },
    {"batch": "immutable-rehearsal"},
)
assert hypothesis_path and hypothesis_path.is_file()
assert_external(hypothesis_path)

from replay_analysis.turn_review import TurnReviewer, TurnSnapshot

reviewer = TurnReviewer(bot_username="immutable-rehearsal")
reviewer.save_turn_review(
    TurnSnapshot(
        turn_number=1,
        bot_active="pikachu",
        bot_hp_percent=100.0,
        opp_active="eevee",
        opp_hp_percent=100.0,
        bot_choice="thunderbolt",
        bot_team_status="healthy",
        opp_team_status="healthy",
        field_conditions=[],
        why_critical="fixture turn",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-immutable-replay",
        alternative_options=[],
    )
)

from replay_analysis.feedback_tracker import FeedbackTracker

feedback = FeedbackTracker()
feedback.record_feedback(
    1,
    "https://replay.pokemonshowdown.com/gen9ou-immutable-replay",
    "thunderbolt",
    True,
)
assert feedback.feedback_file.is_file()

from replay_analysis.analyzer import ReplayAnalyzer

replay_analyzer = ReplayAnalyzer()
replay_analyzer.save_loss_replay(
    "https://replay.pokemonshowdown.com/gen9ou-immutable-loss",
    {"log": ""},
    [],
)

from replay_analysis import team_performance

team_performance.generate_team_report(write_json=True, print_summary=False)
assert team_performance.REPORT_OUTPUT_PATH.is_file()

from infrastructure import event_queue_lib

event_id = event_queue_lib.queue_event(
    "mission_alert",
    "project",
    "immutable runtime rehearsal observation",
    dedup_window_sec=0,
)
assert event_id
assert event_queue_lib.QUEUE_FILE.is_file()
assert event_queue_lib._queue_backup_file().is_file()

sys.path.insert(0, str(release / "scripts"))
from scripts import devstream_session

assert devstream_session.RUNTIME_STATE_ROOT == state_root.resolve()
assert devstream_session.RUNTIME_LOG_ROOT == log_root.resolve()
authority_check = devstream_session.account_season_authority_check(
    {"ok": True, "lease": {"account": "immutable-rehearsal"}},
    env=dict(os.environ),
)
assert authority_check["ok"], authority_check
env_policy = devstream_session.production_env_file_status(dict(os.environ))
assert env_policy["ok"], env_policy
devstream_session._atomic_write_text(
    state_root / "truth" / "immutable-session-write.json",
    '{"ok": true}\n',
)
devstream_session.write_pid_value(
    devstream_session.BATTLE_PID_FILE,
    os.getpid(),
    ["immutable-rehearsal"],
)
devstream_session.write_supervisor_status({"status": "immutable-rehearsal"})

from streaming import serve_obs_page

assert serve_obs_page.RUNTIME_STATE_ROOT == state_root.resolve()
assert serve_obs_page.RUNTIME_LOG_ROOT == log_root.resolve()
serve_obs_page._write_pid_file()
assert serve_obs_page.PID_FILE.is_file()

from infrastructure import event_poster

event_poster._configure_cli_logging()
event_poster.logger.info("immutable rehearsal external event-poster log")
assert event_poster.LOG_FILE.is_file()

from fp.search import main as search_main

assert not hasattr(search_main, "_maybe_hot_reload")
assert (release / ".reload").read_text(encoding="ascii") == "must-remain\n"
assert deployment_lineage.checkout_identity(release)["ready"]
logging.shutdown()
'''
    result = _run([sys.executable, "-B", "-c", rehearsal], cwd=release, env=environment)
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    after = _snapshot_tree(release)
    assert after == before
    assert (release / ".git" / "index").read_bytes() == index_before
    assert (release / ".reload").read_text(encoding="ascii") == "must-remain\n"
    assert (release / "logs" / "ignored-proof.txt").read_text(encoding="ascii") == "ignored bytes must remain\n"
    assert authority_file.read_bytes() == authority_before

    assert (external["state"] / "active_battles.json").is_file()
    assert (external["state"] / "battle_stats.json").is_file()
    assert (external["state"] / "learning" / "matchup_weights.json").is_file()
    assert (external["state"] / "learning" / "movepool_data.json").is_file()
    assert (external["cache"] / "pkmn_sets_cache" / "immutable-rehearsal.json").is_file()
    assert (external["cache"] / "matchup" / "ours_vs_theirs.json").is_file()
    assert list((external["log"] / "decision_traces").glob("battle-gen9ou-1_turn1_*.json"))
    assert (external["log"] / "decision_traces" / "latest-public-battle.json").is_file()
    assert (external["log"] / "matchup_ab_log.jsonl").is_file()
    assert (external["log"] / "matchup_weights_refresh.log").is_file()
    assert (external["log"] / "worker_911_init.log").is_file()
    assert (external["log"] / "init.log").is_file()
    assert (external["log"] / "event_poster.log").is_file()
    assert (external["state"] / "team-rotation.index").is_file()
    assert (external["state"] / "pipeline-state.json").is_file()
    assert (external["state"] / "events_queue.json").is_file()
    assert (external["state"] / "events_queue.json.bak").is_file()
    assert (external["state"] / "pids" / "devstream_battle_session.pid").is_file()
    assert (external["state"] / "pids" / "obs_server.pid").is_file()
    assert (external["state"] / "truth" / "supervisor-status.json").is_file()
    assert (external["state"] / "replay_analysis" / "autoresearch_latest.json").is_file()
    assert (external["state"] / "replay_analysis" / "team_report.json").is_file()
    assert list((external["state"] / "learning" / "hypotheses").glob("*.json"))

    external_base = tmp_path / "external"
    allowed_roots = tuple(path.resolve() for path in external.values())
    for artifact in external_base.rglob("*"):
        assert any(artifact.resolve().is_relative_to(root) for root in allowed_roots), artifact
