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


def test_clean_supervisor_participates_in_runtime_lane_lease():
    source = read("scripts/fouler_clean_supervisor.ps1")

    assert "fouler-runtime-lane.lease.json" in source
    assert "Test-RuntimeLeaseAvailable" in source
    assert "blocked-runtime-lease" in source
    assert "[System.IO.FileMode]::CreateNew" in source
