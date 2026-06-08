import json
import time


def _bind_event_queue(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    truth_dir = tmp_path / "devstream" / "truth"
    archive_dir = tmp_path / "logs" / "discord-events"

    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")
    return event_queue_lib, queue_file, truth_dir


def test_stale_battle_result_survives_generic_queue_expiry(monkeypatch, tmp_path):
    event_queue_lib, queue_file, truth_dir = _bind_event_queue(monkeypatch, tmp_path)

    battle_id = event_queue_lib.queue_event(
        "battle_result",
        "battles",
        "[PROOF] battle result loss vs SlowUpload\nProof: battle `gen9ou-2626011055`",
        dedup_window_sec=0,
    )
    stale_id = event_queue_lib.queue_event(
        "process_crash",
        "workspace",
        "stale crash status",
        dedup_window_sec=0,
    )
    events = json.loads(queue_file.read_text(encoding="utf-8"))
    for event in events:
        event["timestamp"] = time.time() - 3600
    queue_file.write_text(json.dumps(events), encoding="utf-8")

    assert event_queue_lib.expire_old_events(600) == 1

    remaining = json.loads(queue_file.read_text(encoding="utf-8"))
    assert [event["id"] for event in remaining] == [battle_id]
    assert remaining[0]["event_type"] == "battle_result"
    assert remaining[0]["status"] == "pending"
    assert stale_id not in {event["id"] for event in remaining}

    archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    archived_types = [event["eventType"] for event in archive["events"]]
    assert archived_types == ["process_crash"]


def test_mixed_stale_queue_quarantine_then_generic_expiry(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    event_queue_lib, queue_file, truth_dir = _bind_event_queue(monkeypatch, tmp_path)
    stale_timestamp = time.time() - 3600
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-stale-battle",
                    "timestamp": stale_timestamp,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": "[PROOF] battle `gen9ou-2626011055`; replay pending public upload `gen9ou-2626011055`",
                    "battle_id": "battle-gen9ou-2626011055",
                    "replay_id": "gen9ou-2626011055",
                    "replay_status": "pending-public-upload",
                    "proof": {
                        "battleIds": ["gen9ou-2626011055"],
                        "replay": {"status": "pending-public-upload", "id": "gen9ou-2626011055", "url": ""},
                    },
                    "status": "pending",
                    "retry_count": 0,
                },
                {
                    "id": "event-stale-summary",
                    "timestamp": stale_timestamp + 1,
                    "event_type": "autoresearch_summary",
                    "channel": "project",
                    "content": "stale non-battle proof summary",
                    "status": "pending",
                    "retry_count": 0,
                },
            ]
        ),
        encoding="utf-8",
    )

    calls: list[dict] = []
    probes: list[str] = []
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")
    monkeypatch.setattr(event_poster, "REPLAY_RESOLVE_ATTEMPTS", 1)
    monkeypatch.setattr(event_poster, "REPLAY_RESOLVE_DELAY_SEC", 0)
    monkeypatch.setattr(event_poster, "_replay_json_is_live", lambda replay_id: probes.append(replay_id) or True)
    monkeypatch.setattr(event_poster, "post_to_discord", lambda event: calls.append(event) or {"ok": True, "status": "posted"})

    assert event_poster.process_one_event() is False

    queue_after_quarantine = json.loads(queue_file.read_text(encoding="utf-8"))
    quarantine_archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    quarantine_delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))

    assert probes == ["gen9ou-2626011055"]
    assert calls == []
    assert [event["id"] for event in queue_after_quarantine] == ["event-stale-summary"]
    assert queue_after_quarantine[0]["event_type"] == "autoresearch_summary"
    assert queue_after_quarantine[0]["status"] == "pending"
    assert quarantine_delivery["errorCode"] == "stale_battle_result_quarantined"
    assert quarantine_delivery["queue"]["pendingBattleResults"] == 0
    assert quarantine_delivery["queue"]["stalePendingBacklog"] == 1
    assert quarantine_archive["reason"] == "stale-battle-result-quarantined-before-live-transport"
    assert quarantine_archive["archivedEventTypes"] == {"battle_result": 1}
    assert quarantine_archive["events"][0]["replayStatus"] == "public"
    assert quarantine_archive["events"][0]["publicReplayId"] == "gen9ou-2626011055"

    assert event_poster.process_one_event() is False

    queue_after_expiry = json.loads(queue_file.read_text(encoding="utf-8"))
    expiry_archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    expiry_delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))

    assert calls == []
    assert queue_after_expiry == []
    assert expiry_delivery["errorCode"] == "stale_backlog_archived"
    assert expiry_delivery["queue"]["pendingBacklog"] == 0
    assert expiry_archive["reason"] == "pending-discord-event-expired-before-transport"
    assert expiry_archive["archivedEventTypes"] == {"autoresearch_summary": 1}
    assert expiry_archive["archivedBattleResultCount"] == 0
