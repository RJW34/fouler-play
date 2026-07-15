from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_legacy_autoresearch_discord_sender_is_fail_closed() -> None:
    path = ROOT / "infrastructure" / "autoresearch" / "discord_report.py"
    text = path.read_text(encoding="utf-8")

    assert "retired" in text.lower()
    assert "return 78" in text
    assert "send_discord_message.py" not in text
    assert "subprocess.run" not in text
    assert "CHANNEL_ID" not in text


def test_magneton_obs_controller_has_no_output_controls() -> None:
    path = ROOT / "scripts" / "magneton-obs-automation" / "magneton-obs-control.py"
    text = path.read_text(encoding="utf-8")

    assert "retired" in text.lower()
    assert "return 78" in text
    for token in ("StartRecord", "StopRecord", "StartStream", "StopStream", "obsws"):
        assert token not in text


def test_deku_event_producer_is_a_fail_closed_transport_tombstone() -> None:
    path = ROOT / "scripts" / "fouler_deku_event_producer.ps1"
    text = path.read_text(encoding="utf-8")

    assert "retired" in text.lower()
    assert "DEKU-managed relay owns delivery" in text
    assert "exit 2" in text
    assert r"D:\Projects\fouler-play" not in text
    assert "event_poster.py" not in text


def test_stale_mutable_checkout_wrappers_are_inert() -> None:
    paths = (
        ROOT / "scripts" / "fouler_continuous_daemon.ps1",
        ROOT / "scripts" / "run_improve_window.ps1",
        ROOT / "scripts" / "fouler_discord_event_drain.ps1",
        ROOT / "scripts" / "matchup_weights_refresh.ps1",
    )

    for path in paths:
        text = path.read_text(encoding="utf-8")
        assert "retired" in text.lower(), path
        assert "exit 2" in text, path
        assert r"D:\Projects\fouler-play" not in text, path
        assert "run.py" not in text, path
        assert "event_poster.py" not in text, path
        assert "refresh_matchup_weights.py" not in text, path


def test_process_snapshot_is_release_relative_and_redacts_commands() -> None:
    path = ROOT / "scripts" / "fouler_process_snapshot.ps1"
    text = path.read_text(encoding="utf-8")

    assert "$Repo = (Split-Path -Parent $PSScriptRoot)" in text
    assert r"D:\Projects\fouler-play" not in text
    assert "HERMES\\state\\fouler" in text
    assert "function Redact-CommandLine" in text
    assert "commandLine = (Redact-CommandLine -CommandLine $Cmd)" in text
