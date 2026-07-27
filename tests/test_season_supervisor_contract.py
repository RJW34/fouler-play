from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "scripts" / "season_ladder_supervisor.py"
INSTALLER = ROOT / "scripts" / "install_season_supervisor_task.ps1"


def test_season_supervisor_is_bounded_and_has_no_improvement_or_stream_authority():
    source = SUPERVISOR.read_text(encoding="utf-8")

    assert '"roundSize"' in source
    assert '"maxRounds"' in source
    assert '"maxGames"' in source
    assert "season budget completed exactly; no further round authorized" in source
    assert "maxRatingDrawdown" in source
    assert "public rating source" in source
    assert "refresh_matchup_weights" not in source
    assert "cycle_improve" not in source
    assert "Start Streaming" not in source


def test_season_supervisor_pins_child_to_authority_parent_and_external_paths():
    source = SUPERVISOR.read_text(encoding="utf-8")

    for required in (
        "FOULER_RUNTIME_STATE_ROOT",
        "FOULER_RUNTIME_LOG_ROOT",
        "FOULER_RUNTIME_CACHE_ROOT",
        "FOULER_RUNTIME_TEMP_ROOT",
        "FOULER_ACCOUNT_SEASON_PATH",
        "DEKU_EVENT_QUEUE_ROOT",
        "FOULER_BATTLE_RESULT_QUEUE",
        "FOULER_SOURCE_COMMIT",
        "FOULER_SESSION_ID",
        "SUPERVISOR_PID_ENV",
        "SUPERVISOR_CREATE_TIME_ENV",
        "SUPERVISOR_NONCE_ENV",
    ):
        assert required in source
    assert '"DISCORD_BATTLES_WEBHOOK_URL"' in source
    assert "require_existing_paths=True" in source


def test_season_installer_uses_exact_release_limited_s4u_and_reversible_cutover():
    source = INSTALLER.read_text(encoding="utf-8")

    assert "ValidatePattern('^[0-9a-fA-F]{40}$')" in source
    assert 'LogonType S4U' in source
    assert 'RunLevel Limited' in source
    assert "Export-ScheduledTask" in source
    assert "Restore-TaskSnapshot" in source
    assert "Quiesce-MutableFouler" in source
    assert "immutable finite-season cutover" in source
    assert "publicOutputChanged = $false" in source
    assert "startStreaming = $false" in source
    assert "Start Streaming" not in source

