import json
import time

from infrastructure.discord_reporting import build_contract_payload


REPLAY_ID = "gen9ou-2626011055"
PUBLIC_REPLAY_URL = f"https://replay.pokemonshowdown.com/{REPLAY_ID}"


def _bind_event_poster_files(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
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
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")
    monkeypatch.setattr(event_poster, "REPLAY_RESOLVE_DELAY_SEC", 0)
    return event_poster, event_queue_lib, queue_file, truth_dir


def _queue_pending_battle_result(event_queue_lib):
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs SlowUpload",
        "Battle battle-gen9ou-2626011055-privatehash ended loss against SlowUpload.",
        "Replay upload may lag behind the battle result event.",
        "battle_id=battle-gen9ou-2626011055-privatehash; replay_status=pending-public-upload",
        "Append replay once public upload verifies.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2626011055-privatehash",
        result="loss",
        opponent="SlowUpload",
        turns=41,
        replay_url=PUBLIC_REPLAY_URL,
        replay_id=REPLAY_ID,
        replay_status="pending-public-upload",
        replay_public_verified=False,
    )
    return event_queue_lib.queue_event("battle_result", "battles", payload, dedup_window_sec=0)


def test_event_poster_upgrades_pending_replay_before_discord_post(monkeypatch, tmp_path):
    event_poster, event_queue_lib, queue_file, truth_dir = _bind_event_poster_files(monkeypatch, tmp_path)
    _queue_pending_battle_result(event_queue_lib)

    events = json.loads(queue_file.read_text(encoding="utf-8"))
    events[0]["proof"]["replay"]["status"] = "pending public upload"
    queue_file.write_text(json.dumps(events), encoding="utf-8")

    probes = []
    posted_events = []

    def fake_replay_json_is_live(replay_id):
        probes.append(replay_id)
        return True

    def fake_post_to_discord(event):
        posted_events.append(event)
        return {"ok": True, "status": "posted", "destinationAlias": event["channel"], "blockers": []}

    monkeypatch.setattr(event_poster, "REPLAY_RESOLVE_ATTEMPTS", 1)
    monkeypatch.setattr(event_poster, "_replay_json_is_live", fake_replay_json_is_live)
    monkeypatch.setattr(event_poster, "write_deku_observation", fake_post_to_discord)

    assert event_poster.process_one_event() is True

    posted = posted_events[0]
    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))

    assert probes == [REPLAY_ID]
    assert f"- replay `{REPLAY_ID}`: {PUBLIC_REPLAY_URL}" in posted["content"]
    assert f"replay pending public upload `{REPLAY_ID}`" not in posted["content"]
    assert posted["proof"]["replay"] == {"status": "public", "id": REPLAY_ID, "url": PUBLIC_REPLAY_URL}
    assert f"public replay {REPLAY_ID}" in posted["current_battle_state"]
    assert delivery["proof"]["replay"]["status"] == "public"
    assert delivery["proof"]["replay"]["url"] == PUBLIC_REPLAY_URL
    assert delivery["reportSummary"]["replay"]["status"] == "public"
    assert delivery["reportSummary"]["replay"]["url"] == PUBLIC_REPLAY_URL


def test_event_poster_preserves_pending_replay_when_json_is_not_live(monkeypatch, tmp_path):
    event_poster, event_queue_lib, _queue_file, truth_dir = _bind_event_poster_files(monkeypatch, tmp_path)
    _queue_pending_battle_result(event_queue_lib)

    probes = []
    posted_events = []

    def fake_replay_json_is_live(replay_id):
        probes.append(replay_id)
        return False

    def fake_post_to_discord(event):
        posted_events.append(event)
        return {"ok": True, "status": "posted", "destinationAlias": event["channel"], "blockers": []}

    monkeypatch.setattr(event_poster, "REPLAY_RESOLVE_ATTEMPTS", 2)
    monkeypatch.setattr(event_poster, "_replay_json_is_live", fake_replay_json_is_live)
    monkeypatch.setattr(event_poster, "write_deku_observation", fake_post_to_discord)

    assert event_poster.process_one_event() is True

    posted = posted_events[0]
    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))

    assert probes == [REPLAY_ID, REPLAY_ID]
    assert f"replay pending public upload `{REPLAY_ID}`" in posted["content"]
    assert f"- replay `{REPLAY_ID}`: {PUBLIC_REPLAY_URL}" not in posted["content"]
    assert posted["proof"]["replay"] == {"status": "pending-public-upload", "id": REPLAY_ID, "url": ""}
    assert delivery["proof"]["replay"]["status"] == "pending-public-upload"
    assert delivery["proof"]["replay"]["url"] == ""


def test_event_poster_resolves_stale_pending_replay_before_quarantine(monkeypatch, tmp_path):
    event_poster, event_queue_lib, queue_file, truth_dir = _bind_event_poster_files(monkeypatch, tmp_path)
    _queue_pending_battle_result(event_queue_lib)

    events = json.loads(queue_file.read_text(encoding="utf-8"))
    events[0]["timestamp"] = time.time() - event_poster.EXPIRY_SEC - 60
    events[0]["proof"]["replay"]["status"] = "pending-public-upload"
    queue_file.write_text(json.dumps(events), encoding="utf-8")

    probes = []
    posted_events = []

    def fake_replay_json_is_live(replay_id):
        probes.append(replay_id)
        return True

    def fake_post_to_discord(event):
        posted_events.append(event)
        return {"ok": True, "status": "posted", "destinationAlias": event["channel"], "blockers": []}

    monkeypatch.setattr(event_poster, "REPLAY_RESOLVE_ATTEMPTS", 1)
    monkeypatch.setattr(event_poster, "_replay_json_is_live", fake_replay_json_is_live)
    monkeypatch.setattr(event_poster, "write_deku_observation", fake_post_to_discord)

    assert event_poster.process_one_event() is False

    queue_after = json.loads(queue_file.read_text(encoding="utf-8"))
    archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))

    assert probes == [REPLAY_ID]
    assert posted_events == []
    assert queue_after == []
    assert archive["reason"] == "stale-battle-result-quarantined-before-live-transport"
    assert archive["archivedEventCount"] == 1
    assert archive["archivedEventTypes"] == {"battle_result": 1}
    assert archive["archivedBattleResultCount"] == 1
    assert archive["liveDiscordMessagesSent"] is False
    assert archive["archivalDisposition"] == "stale-pending-events-expired-locally-not-sent"
    assert archive["events"][0]["eventType"] == "battle_result"
    assert archive["events"][0]["statusBeforeArchive"] == "pending"
    assert archive["events"][0]["replayStatus"] == "public"
    assert archive["events"][0]["publicReplayId"] == REPLAY_ID
    assert delivery["status"] == "blocked"
    assert delivery["errorCode"] == "stale_battle_result_quarantined"
    assert delivery["queue"]["pending"] == 0
    assert delivery["queue"]["pendingBattleResults"] == 0
    assert any("late live Discord posting is withheld" in blocker for blocker in delivery["blockers"])
