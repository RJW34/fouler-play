import asyncio
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8")


def test_run_py_honors_battle_stats_file_env():
    source = read("run.py")

    assert 'os.getenv("BATTLE_STATS_FILE")' in source
    assert "BATTLE_STATS_FILE.parent.mkdir" in source
    assert 'Path(__file__).resolve().parent / "battle_stats.json"' in source


def test_run_battle_uses_same_battle_stats_contract_for_recent_summary():
    source = read("fp/run_battle.py")

    assert "def battle_stats_file()" in source
    assert 'os.getenv("BATTLE_STATS_FILE")' in source
    assert "_stats_path = battle_stats_file()" in source


def test_pending_replay_message_keeps_clickable_replay_url():
    source = read("fp/run_battle.py")

    assert 'elif replay_id:' in source
    assert "https://replay.pokemonshowdown.com/{replay_id}" in source
    assert "pending public upload" in source


def test_battle_supervisor_singleton_regex_has_no_backspace_control_chars():
    source = read("scripts/start_battle_supervisor_task.ps1")

    assert "\x08" not in source
    assert "\\bsupervise\\b" in source or "(^|\\s)supervise(\\s|$)" in source


def test_clean_supervisor_validates_lock_pid_and_writes_truth_status():
    source = read("scripts/fouler_clean_supervisor.ps1")

    assert "function Test-CleanSupervisorPid" in source
    assert "fouler_clean_supervisor\\.ps1" in source
    assert "clean-supervisor-status.json" in source
    assert "name like 'python%'" in source
    assert "ParentProcessId" in source
    assert "venvChildren" in source


def test_recursive_improvement_entrypoints_share_runtime_lane_lease():
    agent = read("infrastructure/improve_agent.py")
    loop = read("infrastructure/improve_loop.py")
    session = read("scripts/devstream_session.py")

    assert 'acquire_runtime_lease(holder="improve_agent")' in agent
    assert 'acquire_runtime_lease(holder="improve_loop")' in loop
    assert 'acquire_runtime_lease(holder="devstream_session supervise")' in session
    assert "RuntimeLeaseBusy" in agent
    assert "RuntimeLeaseBusy" in loop
    assert "RuntimeLeaseBusy" in session


def test_run_battle_finalizer_clears_pre_battle_elo_cache_without_dropping_snapshot():
    import fp.run_battle as run_battle

    battle_tag = "battle-gen9ou-cache"
    run_battle._elo_before_cache[battle_tag] = 1234
    run_battle._last_battle_elo[battle_tag] = {"elo_after": 1250}

    run_battle._clear_pre_battle_elo_cache(battle_tag)

    assert battle_tag not in run_battle._elo_before_cache
    assert run_battle._last_battle_elo[battle_tag] == {"elo_after": 1250}
    run_battle._last_battle_elo.pop(battle_tag, None)


def test_run_battle_replay_save_tasks_are_bounded_and_observed(monkeypatch):
    import fp.run_battle as run_battle

    seen = []
    run_battle._replay_save_tasks.clear()
    monkeypatch.setattr(run_battle, "REPLAY_SAVE_TASKS_MAX", 1)

    async def marker(name):
        seen.append(name)
        return name

    async def exercise():
        first = run_battle._track_replay_save_task(marker("first"))
        second = run_battle._track_replay_save_task(marker("second"))
        assert first is not None
        assert second is None
        await first
        await asyncio.sleep(0)

    asyncio.run(exercise())

    assert seen == ["first"]
    assert run_battle._replay_save_tasks == set()

def test_recursive_improvement_gate_verifies_showdown_source_lock():
    agent = read("infrastructure/improve_agent.py")
    lock = read("infrastructure/showdown.lock.json")

    assert "verify_showdown_source" in agent
    assert '"expected_head": "3d25154b0489523a2f5515ba9489292257b27666"' in lock
    assert '"path": "D:\\\\Projects\\\\pokemon-showdown"' in lock
    assert '"allow_dirty": false' in lock


def test_clean_supervisor_participates_in_runtime_lane_lease():
    source = read("scripts/fouler_clean_supervisor.ps1")

    assert "fouler-runtime-lane.lease.json" in source
    assert "Test-RuntimeLeaseAvailable" in source
    assert "blocked-runtime-lease" in source
    assert "[System.IO.FileMode]::CreateNew" in source
