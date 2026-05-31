from scripts import showdown_login_check
from streaming import state_store


def test_publish_successful_login_proof_refreshes_ready_truth(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")
    monkeypatch.setattr(state_store, "STABILITY_REPORT_PATH", tmp_path / "stability_report.json")

    payload = {
        "execute": True,
        "ok": True,
        "login": {"ok": True},
    }

    published = showdown_login_check.publish_runtime_truth(payload)

    assert published["activeBattles"]["runtime_mode"] == "login_proven"
    assert published["status"]["status"] == "Ready"
    assert published["status"]["runtime_mode"] == "login_proven"


def test_publish_failed_login_proof_refreshes_blocked_truth(tmp_path, monkeypatch):
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")
    monkeypatch.setattr(state_store, "STABILITY_REPORT_PATH", tmp_path / "stability_report.json")

    payload = {
        "execute": True,
        "ok": False,
        "blockers": ["showdown rejected login"],
    }

    published = showdown_login_check.publish_runtime_truth(payload)

    assert published["activeBattles"]["runtime_blocked"] is True
    assert published["status"]["status"] == "Credential blocked"
    assert published["status"]["blocker_code"] == "showdown_credential_rejected"


def test_offline_rehearsal_publishes_separate_truth(tmp_path, monkeypatch):
    monkeypatch.setattr(showdown_login_check, "OFFLINE_REHEARSAL_FILE", tmp_path / "offline-rehearsal.json")
    monkeypatch.setattr(state_store, "ACTIVE_BATTLES_PATH", tmp_path / "active_battles.json")
    monkeypatch.setattr(state_store, "STREAM_STATUS_PATH", tmp_path / "stream_status.json")
    monkeypatch.setattr(state_store, "DAILY_STATS_PATH", tmp_path / "daily_stats.json")
    monkeypatch.setattr(state_store, "STABILITY_REPORT_PATH", tmp_path / "stability_report.json")

    payload = {
        "checkedAt": "2026-05-03T00:00:00+00:00",
        "ok": True,
        "offlineRehearsal": True,
        "note": "offline",
    }

    published = showdown_login_check.publish_runtime_truth(payload)

    assert showdown_login_check.OFFLINE_REHEARSAL_FILE.exists()
    assert published["activeBattles"]["runtime_mode"] == "offline_rehearsal"
    assert published["status"]["status"] == "Offline rehearsal ready"
