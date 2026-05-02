from streaming import state_store


def test_runtime_blocked_status_writes_fresh_viewer_truth(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")
    monkeypatch.setattr(state_store, "STABILITY_REPORT_PATH", tmp_path / "stability_report.json")

    payload = state_store.write_runtime_blocked_status(
        code="showdown_credential_rejected",
        summary="Showdown login failed; credential was rejected.",
    )

    active = state_store.read_active_battles()
    status = state_store.read_status()
    daily = state_store.read_daily_stats()

    assert active["count"] == 0
    assert active["runtime_blocked"] is True
    assert status["status"] == "Credential blocked"
    assert status["streaming"] is False
    assert "credential" in status["next_fix"].lower()
    assert daily["date"]
    assert payload["status"]["blocker_code"] == "showdown_credential_rejected"
    assert payload["stabilityReport"]["runtime_blocked"] is True
    assert payload["stabilityReport"]["stability"]["health"] == "blocked"
    assert state_store.STABILITY_REPORT_PATH.exists()
