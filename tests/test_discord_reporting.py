import importlib
import builtins
import json
import os
import socket
import sys
import time
import urllib.error
from pathlib import Path

from infrastructure.discord_reporting import (
    build_contract_message,
    build_contract_payload,
    canonical_replay_url,
    format_elo_delta,
    format_payload_or_message,
    is_contract_message,
    public_replay_id_candidate,
    redacted_report_summary,
    recent_results_safety_alert,
    structured_report_fields,
    summarize_recent_results,
    summarize_recent_results_with_current,
    top_recurring_issue,
)
from infrastructure.event_queue_lib import classify_delivery_error, queue_health_summary


def _configure_battle_digest_test(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    truth_dir = tmp_path / "devstream" / "truth"
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", tmp_path / "logs" / "discord-events")
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")
    monkeypatch.setattr(event_poster, "BATTLE_DIGEST_STATE", truth_dir / "battle-report-digest-state.json")
    monkeypatch.setattr(event_poster, "BATTLE_DIGEST_SIZE", 5)
    monkeypatch.setattr(event_poster, "BATTLE_DIGEST_MAX_AGE_SEC", 900)
    monkeypatch.setattr(event_poster, "BATTLE_DIGEST_REPORTED_ID_LIMIT", 100)
    monkeypatch.setattr(event_poster, "DEKU_EVENT_QUEUE_ROOT", tmp_path / "deku-events")
    return event_poster, event_queue_lib, queue_file, truth_dir


def _queue_digest_test_battle(event_queue_lib, index: int) -> str:
    result = "win" if index % 2 == 0 else "loss"
    elo_before = 1000 + (index * 5)
    elo_after = elo_before + (10 if result == "win" else -5)
    replay_id = f"gen9ou-{2600000000 + index}"
    battle_id = f"battle-{replay_id}"
    payload = build_contract_payload(
        "PROOF",
        f"battle result {result} vs Opponent{index:02d}",
        f"battle finished {result} vs Opponent{index:02d} in {20 + index} turns",
        "This is durable ladder evidence for the current run.",
        f"battle_id={battle_id}; result={result}; turns={20 + index}",
        "No operator action required.",
        source="unit-test",
        battle_id=battle_id,
        result=result,
        opponent=f"Opponent{index:02d}",
        turns=20 + index,
        replay_url=f"https://replay.pokemonshowdown.com/{replay_id}",
        replay_id=replay_id,
        replay_status="public",
        replay_public_verified=True,
        elo_before=elo_before,
        elo_after=elo_after,
        rating_delta=elo_after - elo_before,
    )
    event_id = event_queue_lib.queue_event(
        "battle_result",
        "battles",
        payload,
        dedup_window_sec=0,
    )
    assert event_id
    return event_id


def test_event_poster_loads_env_chain_for_webhooks(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    env_file = tmp_path / ".env"
    env_file.write_text("DISCORD_BATTLES_WEBHOOK_URL=https://discord.com/api/webhooks/example/token\n", encoding="utf-8")
    monkeypatch.setattr(event_poster, "ENV_FILES", (env_file,))
    monkeypatch.delenv("DISCORD_BATTLES_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)

    loaded = event_poster.load_env_chain()
    url, source = event_poster.resolve_webhook_url("battles")

    assert loaded == [str(env_file)]
    assert source == "DISCORD_BATTLES_WEBHOOK_URL"
    assert url.endswith("/token")


def test_post_to_discord_writes_structured_durable_deku_event(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(event_poster, "DEKU_EVENT_QUEUE_ROOT", tmp_path / "deku-events")
    monkeypatch.setattr(event_poster.event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(
        event_poster,
        "_post_via_deku_remote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("direct SSH must not run")),
    )
    monkeypatch.setattr(
        event_poster,
        "_post_via_webhook",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("webhook must not run")),
    )

    result = event_poster.post_to_discord({
        "id": "event-1",
        "event_type": "status_update",
        "channel": "battles",
        "content": "Fouler runner is intentionally parked under stop-loss.",
    })

    assert result["ok"] is True
    assert result["status"] == "queued"
    assert result["transport"] == "deku_event_queue"
    queued = json.loads((tmp_path / "deku-events" / "pending" / "fouler-event-1.json").read_text(encoding="utf-8"))
    assert queued["schemaVersion"] == "deku-project-event/v1"
    assert queued["id"] == "fouler-event-1"
    assert queued["category"] == "fouler-play"
    assert queued["proof"] == [str(queue_file)]
    assert queued["payload"]["localEventId"] == "event-1"


def test_deku_event_queue_handoff_is_idempotent(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    monkeypatch.setattr(event_poster, "DEKU_EVENT_QUEUE_ROOT", tmp_path / "deku-events")
    event = {
        "id": "event-1",
        "event_type": "status_update",
        "channel": "battles",
        "content": "Fouler runner is intentionally offline under stop-loss.",
    }

    first = event_poster.post_to_discord(event)
    second = event_poster.post_to_discord(event)

    assert first["ok"] is True
    assert first["alreadyQueued"] is False
    assert second["ok"] is True
    assert second["alreadyQueued"] is True
    assert len(list((tmp_path / "deku-events" / "pending").glob("*.json"))) == 1


def test_deku_event_queue_fails_closed_on_id_collision(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    pending = tmp_path / "deku-events" / "pending"
    pending.mkdir(parents=True)
    (pending / "fouler-event-2.json").write_text(json.dumps({"id": "different-id"}), encoding="utf-8")
    monkeypatch.setattr(event_poster, "DEKU_EVENT_QUEUE_ROOT", tmp_path / "deku-events")

    result = event_poster.post_to_discord({
        "id": "event-2",
        "event_type": "status_update",
        "channel": "battles",
        "content": "Fouler runner remains parked.",
    })

    assert result["ok"] is False
    assert result["transport"] == "deku_event_queue"
    assert result["errorCode"] == "deku_event_queue_collision"


def test_battle_results_are_retained_locally_and_emit_one_bounded_batch_digest(monkeypatch, tmp_path):
    event_poster, event_queue_lib, queue_file, truth_dir = _configure_battle_digest_test(monkeypatch, tmp_path)

    local_event_ids = []
    for index in range(4):
        local_event_ids.append(_queue_digest_test_battle(event_queue_lib, index))
        assert event_poster.process_one_event() is True

    pending_dir = tmp_path / "deku-events" / "pending"
    assert not pending_dir.exists() or list(pending_dir.glob("*.json")) == []
    local_events = json.loads(queue_file.read_text(encoding="utf-8"))
    state = json.loads((truth_dir / "battle-report-digest-state.json").read_text(encoding="utf-8"))
    assert [event["status"] for event in local_events] == ["posted"] * 4
    assert len(state["pendingBattles"]) == 4
    assert state["reportedEventIds"] == []

    local_event_ids.append(_queue_digest_test_battle(event_queue_lib, 4))
    assert event_poster.process_one_event() is True

    queue_paths = list(pending_dir.glob("*.json"))
    assert len(queue_paths) == 1
    queued = json.loads(queue_paths[0].read_text(encoding="utf-8"))
    local_events = json.loads(queue_file.read_text(encoding="utf-8"))
    state = json.loads((truth_dir / "battle-report-digest-state.json").read_text(encoding="utf-8"))
    rendered = json.dumps(queued).lower()

    assert queued["eventType"] == "battle_digest"
    assert queued["payload"]["battleDigest"]["battleCount"] == 5
    assert queued["payload"]["battleDigest"]["wins"] == 3
    assert queued["payload"]["battleDigest"]["losses"] == 2
    assert queued["payload"]["battleDigest"]["eloStart"] == 1000
    assert queued["payload"]["battleDigest"]["eloEnd"] == 1030
    assert queued["payload"]["battleDigest"]["eloDelta"] == 30
    assert queued["payload"]["battleDigest"]["latestReplayUrl"].endswith("gen9ou-2600000004")
    assert len(queued["summary"]) < 900
    assert queued["proof"] == [str(queue_file), str(truth_dir / "battle-report-digest-state.json"), "https://replay.pokemonshowdown.com/gen9ou-2600000004"]
    assert "webhook" not in rendered
    assert "avatar_url" not in rendered
    assert "username" not in rendered
    assert [event["id"] for event in local_events] == local_event_ids
    assert [event["status"] for event in local_events] == ["posted"] * 5
    assert all(event.get("proof") for event in local_events)
    assert state["pendingBattles"] == []
    assert state["reportedEventIds"] == local_event_ids
    assert state["lastDigest"]["battleCount"] == 5


def test_battle_digest_age_flush_recovers_idempotently_after_queue_before_state_crash(monkeypatch, tmp_path):
    event_poster, event_queue_lib, _queue_file, truth_dir = _configure_battle_digest_test(monkeypatch, tmp_path)
    monkeypatch.setattr(event_poster, "BATTLE_DIGEST_MAX_AGE_SEC", 60)

    event_id = _queue_digest_test_battle(event_queue_lib, 0)
    assert event_poster.process_one_event() is True
    state = event_poster._read_battle_digest_state()
    assert [item["localEventId"] for item in state["pendingBattles"]] == [event_id]

    digest_event = event_poster._battle_digest_event(state["pendingBattles"])
    queued_first = event_poster._queue_deku_project_event(digest_event, digest_event["content"])
    assert queued_first["alreadyQueued"] is False

    first_buffered = float(state["pendingBattles"][0]["bufferedAtEpoch"])
    recovered = event_poster._flush_battle_digest_if_due(now=first_buffered + 61)
    assert recovered["ok"] is True
    assert recovered["alreadyQueued"] is True
    assert recovered["flushed"] is True
    assert len(list((tmp_path / "deku-events" / "pending").glob("*.json"))) == 1

    state_after = json.loads((truth_dir / "battle-report-digest-state.json").read_text(encoding="utf-8"))
    assert state_after["pendingBattles"] == []
    assert state_after["reportedEventIds"] == [event_id]


def test_battle_digest_parses_unchanged_elo_transition():
    import infrastructure.event_poster as event_poster

    content = format_payload_or_message(build_contract_payload(
        "PROOF",
        "battle result tie vs Example",
        "battle finished tie vs Example in 30 turns",
        "The rating stayed flat.",
        "battle_id=battle-gen9ou-2600000100; result=tie; turns=30",
        "No operator action required.",
        battle_id="battle-gen9ou-2600000100",
        result="tie",
        opponent="Example",
        turns=30,
        elo_before=1000,
        elo_after=1000,
        rating_delta=0,
    ))
    entry = event_poster._battle_digest_entry(
        {"id": "event-flat", "event_type": "battle_result", "content": content},
        now=1.0,
    )

    assert entry["elo"] == {"before": 1000, "after": 1000, "delta": 0}


def test_event_poster_doctor_reports_redacted_transport(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(
        event_poster,
        "_deku_event_queue_status",
        lambda alias: {
            "ready": True,
            "transport": "deku_event_queue",
            "category": "fouler-play",
            "credentialMaterialIncluded": False,
        },
    )

    payload = event_poster.build_doctor_payload()

    assert payload["ready"] is True
    assert payload["transportReady"] is True
    assert payload["queue"]["ready"] is True
    assert payload["primaryTransportStatus"]["transport"] == "deku_event_queue"
    assert payload["primaryTransportStatus"]["category"] == "fouler-play"
    assert payload["secretValuesPrinted"] is False
    assert "token" not in json.dumps(payload)
    json.dumps(payload)


def test_event_poster_doctor_is_not_ready_with_pending_backlog(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        '[{"id":"event-1","timestamp":1,"event_type":"battle_result","status":"pending","retry_count":0}]',
        encoding="utf-8",
    )
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(
        event_poster,
        "_deku_event_queue_status",
        lambda alias: {
            "ready": True,
            "transport": "deku_event_queue",
            "category": "fouler-play",
            "credentialMaterialIncluded": False,
        },
    )
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)

    payload = event_poster.build_doctor_payload()

    assert payload["transportReady"] is True
    assert payload["ready"] is False
    assert payload["queue"]["healthStatus"] == "backlogged"
    assert payload["queue"]["pendingBacklog"] == 1


def test_event_poster_dry_run_writes_redacted_delivery_proof(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    content = build_contract_payload(
        "PROOF",
        "battle result loss vs RedactedOpponent",
        "battle finished loss vs RedactedOpponent in 12 turns",
        "secret-token-should-not-render caused a disconnect-shaped loss that must be redacted",
        "battle_id=battle-gen9ou-2613956411; result=loss; opponent=RedactedOpponent; turns=12",
        "Review reconnect proof before the next run.",
        battle_id="battle-gen9ou-2613956411",
        result="loss",
        opponent="RedactedOpponent",
        turns=12,
        next_battle_action="Review reconnect proof before the next run.",
    )
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-1",
                    "timestamp": 1,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": content,
                    "status": "pending",
                    "retry_count": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")

    assert event_poster.process_one_event(dry_run=True) is True

    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))
    reporting = json.loads((truth_dir / "discord-reporting.json").read_text(encoding="utf-8"))
    rendered = json.dumps({"delivery": delivery, "reporting": reporting})

    assert delivery["schemaVersion"] == "fouler-play-discord-delivery/v1"
    assert delivery["status"] == "dry-run"
    assert delivery["destinationAlias"] == "battles"
    assert delivery["battleIds"] == ["gen9ou-2613956411"]
    assert delivery["queue"]["pendingBattleResults"] == 1
    assert delivery["queue"]["pendingBacklog"] == 1
    assert delivery["queue"]["healthStatus"] == "backlogged"
    assert delivery["reportSummary"]["battleIds"] == ["gen9ou-2613956411"]
    assert delivery["reportSummary"]["secretLikeContentRedacted"] is True
    assert delivery["secretValuesPrinted"] is False
    assert reporting["schemaVersion"] == "fouler-play-discord-reporting/v1"
    assert reporting["transport"]["type"] == "deku_event_queue"
    assert reporting["transport"]["category"] == "fouler-play"
    assert reporting["queue"]["deliveryFailures"] == 0
    assert "token" not in rendered
    assert "secret-token-should-not-render" not in rendered
    queue_after = json.loads(queue_file.read_text(encoding="utf-8"))
    assert queue_after[0]["status"] == "pending"


def test_idle_reporting_proof_keeps_latest_battle_result(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    content = build_contract_payload(
        "PROOF",
        "battle result win vs LatestOpponent",
        "",
        "Operator-facing battle posts should report concrete ladder facts.",
        "battle_id=battle-gen9ou-2613956411; result=win; opponent=LatestOpponent; turns=21; replay=https://replay.pokemonshowdown.com/gen9ou-2613956411; replay_status=public",
        "Append replay or ladder delta if more context lands after posting.",
        battle_id="battle-gen9ou-2613956411",
        result="win",
        opponent="LatestOpponent",
        turns=21,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2613956411",
        replay_status="public",
        decisive_reason="LatestOpponent lost after the bot preserved its defensive core.",
        recent_record="last 5: 4-1 (80% WR)",
    )
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-latest",
                    "timestamp": 100,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": content,
                    "status": "posted",
                    "retry_count": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")

    payload = event_poster.write_reporting_proof(status="idle", event=None, blockers=["no pending Discord events"])

    latest = payload["latestBattleResult"]
    assert latest["eventId"] == "event-latest"
    assert latest["battle_id"] == "battle-gen9ou-2613956411"
    assert latest["analysis"]["result"] == "win"
    assert latest["analysis"]["proofReadiness"]["readyForHermes"] is True


def test_idle_reporting_recomputes_stale_latest_battle_analysis(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    content = build_contract_payload(
        "PROOF",
        "battle result win vs StaleAnalysis",
        "",
        "Operator-facing battle posts should report concrete ladder facts.",
        "battle_id=battle-gen9ou-2613956412; result=win; opponent=StaleAnalysis; turns=18; replay=https://replay.pokemonshowdown.com/gen9ou-2613956412; replay_status=public",
        "Append replay or ladder delta if more context lands after posting.",
        battle_id="battle-gen9ou-2613956412",
        result="win",
        opponent="StaleAnalysis",
        turns=18,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2613956412",
        replay_status="public",
        recent_record="last 5: 5-0 (100% WR)",
    )
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-stale-analysis",
                    "timestamp": 100,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": content,
                    "status": "posted",
                    "analysis": {"proofReadiness": {"readyForHermes": True}},
                    "retry_count": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")

    payload = event_poster.write_reporting_proof(status="idle", event=None, blockers=["no pending Discord events"])

    readiness = payload["latestBattleResult"]["analysis"]["proofReadiness"]
    assert readiness["readyForHermes"] is False
    assert "battle_cause_unclassified" in readiness["qualityGaps"]


def test_run_battle_replay_handoff_preserves_pending_public_replay():
    from fp.run_battle import replay_handoff_fields

    fields = replay_handoff_fields(
        battle_tag="battle-gen9ou-2626011055-privatehash",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2626011055",
        verified_replay_url=None,
    )
    payload = build_contract_payload(
        "PROOF",
        "battle result win vs Altdebup",
        "Battle battle-gen9ou-2626011055-privatehash ended win against Altdebup.",
        "battle updates should preserve replay evidence even when public upload verification lags",
        (
            "battle_id=battle-gen9ou-2626011055-privatehash; result=win; "
            f"replay={fields['replay_url']}; replay_status={fields['replay_status']}"
        ),
        "Append ladder delta if more context lands after posting.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2626011055-privatehash",
        result="win",
        opponent="Altdebup",
        turns=31,
        replay_url=fields["replay_url"],
        replay_id=fields["replay_id"],
        replay_status=fields["replay_status"],
        replay_public_verified=fields["replay_public_verified"],
        raw_replay_url=fields["raw_replay_url"],
    )
    structured = structured_report_fields(payload, event_type="battle_result")

    assert fields["replay_id"] == "gen9ou-2626011055"
    assert fields["replay_url"] == "https://replay.pokemonshowdown.com/gen9ou-2626011055"
    assert fields["replay_status"] == "pending-public-upload"
    assert fields["replay_public_verified"] is False
    assert structured["proof"]["replay"]["status"] == "pending-public-upload"
    assert structured["proof"]["replay"]["id"] == "gen9ou-2626011055"
    assert structured["proof"]["replay"]["url"] == ""
    assert "gen9ou-2626011055" in structured["proof"]["battleIds"]


def test_pending_battle_result_replay_update_reuses_existing_queue_event(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)

    pending_payload = build_contract_payload(
        "PROOF",
        "battle result loss vs SlowUpload",
        "Battle battle-gen9ou-2626011055 ended loss against SlowUpload.",
        "Replay upload may lag behind the battle result event.",
        "battle_id=battle-gen9ou-2626011055-privatehash; replay_status=pending-public-upload",
        "Append replay once public upload verifies.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2626011055-privatehash",
        result="loss",
        opponent="SlowUpload",
        turns=41,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2626011055",
        replay_id="gen9ou-2626011055",
        replay_status="pending-public-upload",
        replay_public_verified=False,
    )
    public_payload = build_contract_payload(
        "PROOF",
        "battle result loss vs SlowUpload",
        "Battle battle-gen9ou-2626011055-privatehash ended loss against SlowUpload.",
        "Replay upload has verified and should update the pending queue event.",
        "battle_id=battle-gen9ou-2626011055; replay_status=public",
        "Review the public replay before the next improvement.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2626011055",
        result="loss",
        opponent="SlowUpload",
        turns=41,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2626011055",
        replay_id="gen9ou-2626011055",
        replay_status="public",
        replay_public_verified=True,
        verified_replay_url="https://replay.pokemonshowdown.com/gen9ou-2626011055",
    )

    first_id = event_queue_lib.queue_event("battle_result", "battles", pending_payload, dedup_window_sec=0)
    second_id = event_queue_lib.queue_event("battle_result", "battles", public_payload, dedup_window_sec=0)
    events = json.loads(queue_file.read_text(encoding="utf-8"))

    assert second_id == first_id
    assert len(events) == 1
    assert events[0]["update_count"] == 1
    assert events[0]["proof"]["replay"]["status"] == "public"
    assert events[0]["proof"]["replay"]["url"] == "https://replay.pokemonshowdown.com/gen9ou-2626011055"
    assert events[0]["proof_readiness"]["status"] == "proof-ready"


def test_event_poster_archives_stale_backlog_before_transport(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-stale-secret-id",
                    "timestamp": 1,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": "[PROOF] battle `gen9ou-2613956411`; token=secret-token-should-not-render",
                    "battle_id": "battle-gen9ou-2613956411",
                    "proof": {"battleIds": ["gen9ou-2613956411"], "items": ["replay public id only"]},
                    "analysis": {"nextHermesAction": "review loss"},
                    "proof_readiness": {"status": "proof-ready"},
                    "status": "pending",
                    "retry_count": 0,
                },
                {
                    "id": "event-fresh",
                    "timestamp": time.time(),
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": "[PROOF] battle `gen9ou-2613956999`",
                    "status": "pending",
                    "retry_count": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    archive_dir = tmp_path / "logs" / "discord-events"
    calls: list[dict] = []

    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")
    monkeypatch.setattr(event_poster, "post_to_discord", lambda event: calls.append(event) or {"ok": True, "status": "posted"})

    assert event_poster.process_one_event() is False

    queue_after = json.loads(queue_file.read_text(encoding="utf-8"))
    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))
    archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    rendered_archive = json.dumps(archive)

    assert calls == []
    assert len(queue_after) == 1
    assert queue_after[0]["id"] == "event-fresh"
    assert queue_after[0]["status"] == "pending"
    assert delivery["status"] == "blocked"
    assert delivery["errorCode"] == "stale_battle_result_quarantined"
    assert delivery["queue"]["pendingBacklog"] == 1
    assert delivery["queue"]["pendingBattleResults"] == 1
    assert delivery["queue"]["stalePendingBacklog"] == 0
    assert delivery["queue"]["freshPendingBacklog"] == 1
    assert delivery["queue"]["expiredDeliveries"] == 0
    assert archive["schemaVersion"] == "fouler-play-discord-backlog-archive/v1"
    assert archive["reason"] == "stale-battle-result-quarantined-before-live-transport"
    assert archive["archivedEventCount"] == 1
    assert archive["archivedBattleResultCount"] == 1
    assert archive["remainingFreshPendingEventCount"] == 1
    assert archive["remainingFreshBattleResultCount"] == 1
    assert archive["liveDiscordMessagesSent"] is False
    assert archive["events"][0]["eventIdHash"] != "event-stale-secret-id"
    assert archive["events"][0]["battleIds"] == ["battle-gen9ou-2613956411", "gen9ou-2613956411"]
    assert archive["secretValuesPrinted"] is False
    assert "secret-token-should-not-render" not in rendered_archive


def test_event_poster_quarantines_stale_battle_result_after_replay_resolution(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-stale-resolved",
                    "timestamp": time.time() - 3600,
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
                }
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    calls: list[dict] = []
    probes: list[str] = []

    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", tmp_path / "logs" / "discord-events")
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")
    monkeypatch.setattr(event_poster, "REPLAY_RESOLVE_ATTEMPTS", 1)
    monkeypatch.setattr(event_poster, "_replay_json_is_live", lambda replay_id: probes.append(replay_id) or True)
    monkeypatch.setattr(event_poster, "post_to_discord", lambda event: calls.append(event) or {"ok": True, "status": "posted"})

    assert event_poster.process_one_event() is False

    archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))

    assert probes == ["gen9ou-2626011055"]
    assert calls == []
    assert json.loads(queue_file.read_text(encoding="utf-8")) == []
    assert delivery["errorCode"] == "stale_battle_result_quarantined"
    assert archive["archivedBattleResultCount"] == 1
    assert archive["events"][0]["replayStatus"] == "public"
    assert archive["events"][0]["publicReplayId"] == "gen9ou-2626011055"
    assert archive["liveDiscordMessagesSent"] is False


def test_event_poster_once_cannot_post_stale_backlog(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-stale-once",
                    "timestamp": 1,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": "[PROOF] battle `gen9ou-999`",
                    "status": "pending",
                    "retry_count": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    calls: list[dict] = []

    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", tmp_path / "logs" / "discord-events")
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")
    monkeypatch.setattr(event_poster, "post_to_discord", lambda event: calls.append(event) or {"ok": True, "status": "posted"})
    monkeypatch.setattr(sys, "argv", ["event_poster.py", "--once"])

    assert event_poster.main() == 1
    assert calls == []
    assert json.loads(queue_file.read_text(encoding="utf-8")) == []


def test_expire_old_events_preserves_stale_battle_result_pending(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {"id": "event-old-1", "timestamp": 1, "event_type": "battle_result", "status": "pending", "retry_count": 0},
                {"id": "event-old-2", "timestamp": 2, "event_type": "autoresearch_summary", "status": "pending", "retry_count": 0},
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    archive_dir = tmp_path / "logs" / "discord-events"
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")

    assert event_queue_lib.expire_old_events(600) == 1
    assert event_queue_lib.cleanup_queue(keep_last=1) == 0

    archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    queue_after = json.loads(queue_file.read_text(encoding="utf-8"))
    archive_files = list(archive_dir.glob("backlog-archive-*.json"))

    assert archive["archivedEventCount"] == 1
    assert archive["archivedEventTypes"] == {"autoresearch_summary": 1}
    assert queue_after == [
        {"id": "event-old-1", "timestamp": 1, "event_type": "battle_result", "status": "pending", "retry_count": 0}
    ]
    assert archive_files


def test_backlog_archive_retention_prunes_oldest_timestamped_archives(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {"id": "event-old-1", "timestamp": 1, "event_type": "autoresearch_summary", "status": "pending", "retry_count": 0},
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    archive_dir = tmp_path / "logs" / "discord-events"
    archive_dir.mkdir(parents=True)
    old_archives = [
        archive_dir / "backlog-archive-20250101T000001Z.json",
        archive_dir / "backlog-archive-20250101T000002Z.json",
        archive_dir / "backlog-archive-20250101T000003Z.json",
    ]
    for index, archive_path in enumerate(old_archives, start=1):
        archive_path.write_text("{}", encoding="utf-8")
        os.utime(archive_path, (index, index))

    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_KEEP_LAST", 2)

    assert event_queue_lib.expire_old_events(600) == 1

    archive_files = sorted(archive_dir.glob("backlog-archive-*.json"))
    latest = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))

    assert len(archive_files) == 2
    assert not old_archives[0].exists()
    assert not old_archives[1].exists()
    assert old_archives[2].exists()
    assert latest["archivedEventCount"] == 1
    assert latest["prunedArchiveCount"] == 2


def test_backlog_archive_byte_guard_truncates_event_summaries_only(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    stale_events = []
    for index in range(120):
        battle_id = f"gen9ou-{2626000000 + index}"
        stale_events.append(
            {
                "id": f"event-old-{index}",
                "timestamp": 1 + index,
                "event_type": "battle_result" if index % 2 == 0 else "autoresearch_summary",
                "channel": "battles",
                "content": f"[PROOF] battle `{battle_id}`; token={'x' * 1000}",
                "battle_id": f"battle-{battle_id}",
                "proof": {"battleIds": [battle_id], "items": ["public replay id only"]},
                "analysis": {"nextHermesAction": "review loss"},
                "proof_readiness": {"status": "proof-ready"},
                "status": "pending",
                "retry_count": 0,
            }
        )

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(json.dumps(stale_events), encoding="utf-8")
    truth_dir = tmp_path / "devstream" / "truth"
    archive_dir = tmp_path / "logs" / "discord-events"

    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", truth_dir / "discord-backlog-archive.json")
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_MAX_BYTES", 5000)

    expected_archived = [event for event in stale_events if event["event_type"] != "battle_result"]

    assert event_queue_lib.expire_old_events(600) == len(expected_archived)

    archive_files = list(archive_dir.glob("backlog-archive-*.json"))
    latest_text = (truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8")
    archive = json.loads(latest_text)

    assert len(latest_text.encode("utf-8")) <= 5000
    assert len(archive_files) == 1
    assert archive_files[0].read_text(encoding="utf-8") == latest_text
    assert archive["archiveByteGuard"] == "per-event-summaries-truncated"
    assert archive["archiveMaxBytes"] == 5000
    assert archive["archivedEventCount"] == len(expected_archived)
    assert archive["archivedEventTypes"] == {"autoresearch_summary": 60}
    assert archive["archivedEventSummaryCount"] == len(archive["events"])
    assert 0 <= archive["archivedEventSummaryCount"] < len(expected_archived)
    assert archive["omittedArchivedEventSummaryCount"] == len(expected_archived) - len(archive["events"])
    assert "token=" not in latest_text
    assert [event["event_type"] for event in json.loads(queue_file.read_text(encoding="utf-8"))] == ["battle_result"] * 60


def test_event_poster_validates_autoresearch_events_before_discord():
    import infrastructure.event_poster as event_poster

    event = {
        "id": "event-autoresearch-hallucination",
        "event_type": "autoresearch_summary",
        "channel": "project",
        "content": "Next fix: use Gigantimaxing as a defensive pivot in Gen 9 OU.",
        "status": "pending",
        "retry_count": 0,
    }

    is_valid, reason = event_poster.validate_event_content(event)

    assert is_valid is False
    assert "gigantimax" in reason.lower()


def test_event_poster_validates_structured_strategy_fields_before_discord():
    import infrastructure.event_poster as event_poster

    event = {
        "id": "event-structured-hallucination",
        "event_type": "battle_result",
        "channel": "battles",
        "content": "[PROOF] battle `gen9ou-123`; result loss vs Example.",
        "analysis": {
            "currentBattleState": "battle loss; vs Example; 31 turns; public replay gen9ou-123",
            "nextHermesAction": "Try Dynamax as the late-game cleanup plan.",
        },
        "proof": {"battleIds": ["gen9ou-123"]},
        "status": "pending",
        "retry_count": 0,
    }

    is_valid, reason = event_poster.validate_event_content(event)

    assert is_valid is False
    assert "dynamax" in reason.lower()


def test_event_poster_validates_explicit_pokedex_claims_before_discord():
    import infrastructure.event_poster as event_poster

    event = {
        "id": "event-pokedex-hallucination",
        "event_type": "autoresearch_summary",
        "channel": "project",
        "content": "pokemon=Gholdengo; ability=Levitate; move=Definitely Fake Beam; type=Fire",
        "status": "pending",
        "retry_count": 0,
    }

    is_valid, reason = event_poster.validate_event_content(event)

    assert is_valid is False
    lowered = reason.lower()
    assert "levitate" in lowered
    assert "definitely fake beam" in lowered
    assert "type claim" in lowered


def test_event_poster_validates_freeform_ability_claims_before_discord():
    import infrastructure.event_poster as event_poster

    event = {
        "id": "event-freeform-ability-hallucination",
        "event_type": "autoresearch_summary",
        "channel": "project",
        "content": "Gholdengo has Levitate and should pivot into Ground coverage.",
        "status": "pending",
        "retry_count": 0,
    }

    is_valid, reason = event_poster.validate_event_content(event)

    assert is_valid is False
    assert "gholdengo" in reason.lower()
    assert "levitate" in reason.lower()


def test_event_poster_validates_freeform_type_effectiveness_claims_before_discord():
    import infrastructure.event_poster as event_poster

    event = {
        "id": "event-freeform-effectiveness-hallucination",
        "event_type": "autoresearch_summary",
        "channel": "project",
        "content": "Earthquake hits Corviknight super effectively, so prioritize that line.",
        "status": "pending",
        "retry_count": 0,
    }

    is_valid, reason = event_poster.validate_event_content(event)

    assert is_valid is False
    lowered = reason.lower()
    assert "earthquake" in lowered
    assert "corviknight" in lowered
    assert "multiplier" in lowered


def test_event_poster_accepts_oracle_backed_explicit_pokedex_claims():
    import infrastructure.event_poster as event_poster

    event = {
        "id": "event-pokedex-grounded",
        "event_type": "autoresearch_summary",
        "channel": "project",
        "content": "pokemon=Gholdengo; ability=Good as Gold; move=Shadow Ball; type=Ghost",
        "status": "pending",
        "retry_count": 0,
    }

    assert event_poster.validate_event_content(event) == (True, "")


def test_event_poster_dry_run_uses_same_gen9_validation(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-1",
                    "timestamp": 1,
                    "event_type": "autoresearch_deep_dive",
                    "channel": "project",
                    "content": "Try Mega Evolution as the next Gen 9 OU ladder adjustment.",
                    "status": "pending",
                    "retry_count": 0,
                }
            ]
        ),
        encoding="utf-8",
    )
    truth_dir = tmp_path / "devstream" / "truth"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_poster, "TRUTH_DIR", truth_dir)
    monkeypatch.setattr(event_poster, "DISCORD_REPORTING_PROOF", truth_dir / "discord-reporting.json")
    monkeypatch.setattr(event_poster, "DISCORD_DELIVERY_PROOF", truth_dir / "discord-delivery.json")
    monkeypatch.setattr(event_poster, "DISCORD_DOCTOR_PROOF", truth_dir / "discord-reporting-doctor.json")

    assert event_poster.process_one_event(dry_run=True) is True

    delivery = json.loads((truth_dir / "discord-delivery.json").read_text(encoding="utf-8"))

    assert delivery["status"] == "blocked"
    assert delivery["dryRun"] is True
    assert delivery["errorCode"] == "validation_failed"
    assert "mega" in " ".join(delivery["blockers"]).lower()


def test_event_poster_leaves_non_pokemon_operational_events_alone():
    import infrastructure.event_poster as event_poster

    event = {
        "id": "event-operational",
        "event_type": "process_crash",
        "channel": "project",
        "content": "bot_main exited and will be restarted by the supervisor.",
        "status": "pending",
        "retry_count": 0,
    }

    assert event_poster.event_requires_gen9_validation(event) is False
    assert event_poster.validate_event_content(event) == (True, "")


def test_battle_result_queue_event_populates_structured_fields(monkeypatch, tmp_path):
    queue_file = tmp_path / "events_queue.json"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    queue_file.write_text("[]", encoding="utf-8")

    import infrastructure.event_queue_lib as event_queue_lib
    event_queue_lib = importlib.reload(event_queue_lib)

    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs Example",
        "Battle battle-gen9ou-123 ended loss against Example.",
        "Structured queue fields make proof actionable without scraping Discord prose.",
        "battle_id=battle-gen9ou-123; result=loss; opponent=Example; turns=31; token=secret-token-value",
        "Review the replay before the next queue.",
        source="unit-test",
        battle_id="battle-gen9ou-123",
        result="loss",
        opponent="Example",
        turns=31,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-123",
        decisive_reason="Example broke through after hazards stayed up.",
        next_battle_action="Check hazard removal timing.",
    )

    event_id = event_queue_lib.queue_event("battle_result", "battles", payload, dedup_window_sec=0)
    assert event_id is not None

    event = event_queue_lib.get_pending_events()[0]
    rendered = json.dumps(event)

    assert event["battle_id"] == "battle-gen9ou-123"
    assert event["winner"] == "Example"
    assert event["loser"] == "fouler-play"
    assert event["turns"] == 31
    assert event["proof"]["battleIds"] == ["gen9ou-123"]
    assert "battle 123" in event["proof"]["items"]
    assert event["analysis"]["result"] == "loss"
    assert event["analysis"]["opponent"] == "Example"
    assert event["analysis"]["currentBattleState"] == "battle loss; vs Example; 31 turns; id 123; public replay gen9ou-123"
    assert event["analysis"]["whyItMatters"]
    assert event["analysis"]["nextHermesAction"] == "Check hazard removal timing."
    assert event["proof_readiness"]["status"] == "proof-ready"
    assert event["current_battle_state"] == event["analysis"]["currentBattleState"]
    assert event["next_hermes_action"] == "Check hazard removal timing."
    assert "secret-token-value" not in rendered
    queue_file.unlink(missing_ok=True)


def test_mission_alert_queue_event_does_not_infer_fake_battle_fields(monkeypatch, tmp_path):
    queue_file = tmp_path / "events_queue.json"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    queue_file.write_text("[]", encoding="utf-8")

    import infrastructure.event_queue_lib as event_queue_lib
    event_queue_lib = importlib.reload(event_queue_lib)

    event_id = event_queue_lib.queue_event(
        "mission_alert",
        "project",
        "[ALERT] **Fouler mission monitor: fouler-runtime-idle**\n"
        "What happened: runtime is idle.\n"
        "Proof: `devstream/truth/mission-monitor.json`.",
        dedup_window_sec=0,
    )
    assert event_id is not None

    event = event_queue_lib.get_pending_events()[0]
    assert event["event_type"] == "mission_alert"
    assert "battle_id" not in event
    assert "proof" not in event
    assert "analysis" not in event
    queue_file.unlink(missing_ok=True)


def test_structured_report_fields_backfills_from_rendered_message():
    message = build_contract_message(
        "PROOF",
        "battle result win vs RenderedOnly",
        "battle finished win vs RenderedOnly in 22 turns",
        "rendered messages should still produce safe structure",
        "battle `2613956411`; replay `gen9ou-2613956411`: https://replay.pokemonshowdown.com/gen9ou-2613956411",
        "none",
    )

    fields = structured_report_fields(message, event_type="battle_result")

    assert fields["battle_id"] == "battle-gen9ou-2613956411"
    assert fields["winner"] == "fouler-play"
    assert fields["loser"] == "RenderedOnly"
    assert fields["turns"] == 22
    assert fields["proof"]["battleIds"] == ["gen9ou-2613956411"]
    assert fields["analysis"]["result"] == "win"
    assert fields["result"] == "win"
    assert fields["analysis"]["currentBattleState"] == "battle win; vs RenderedOnly; 22 turns; id 2613956411; public replay gen9ou-2613956411"
    assert fields["analysis"]["whyItMatters"] == "rendered messages should still produce safe structure"
    # D4/D6: improve loop parked by default -> no "Classify ... before improve cycle" prompt
    assert not fields["analysis"]["nextHermesAction"].startswith("Classify")
    assert "improve cycle" not in fields["analysis"]["nextHermesAction"]
    assert fields["analysis"]["proofReadiness"]["readyForHermes"] is False
    assert "battle_cause_unclassified" in fields["analysis"]["proofReadiness"]["qualityGaps"]
    assert fields["proof_readiness"]["status"] == "proof-ready"


def test_queue_health_classifies_backlog_dns_and_webhook_failures():
    health = queue_health_summary(
        [
            {
                "id": "p1",
                "timestamp": 90.0,
                "event_type": "battle_result",
                "status": "pending",
                "retry_count": 0,
                "content": "battle finished loss vs Example in 31 turns battle-gen9ou-123",
            },
            {"id": "f1", "timestamp": 80.0, "event_type": "battle_result", "status": "failed", "last_error": "dns_failure"},
            {"id": "f2", "timestamp": 70.0, "event_type": "batch", "status": "failed", "last_error": "webhook_http_error"},
        ],
        now=100.0,
    )

    assert health["status"] == "dns-failed"
    assert health["pendingBacklog"] == 1
    assert health["pendingBattleResults"] == 1
    assert health["stalePendingBacklog"] == 0
    assert health["stalePendingBattleResults"] == 0
    assert health["freshPendingBacklog"] == 1
    assert health["freshPendingBattleResults"] == 1
    assert health["oldestPendingAgeSeconds"] == 10.0
    assert health["deliveryFailures"] == 2
    assert health["dnsFailures"] == 1
    assert health["webhookFailures"] == 1
    assert health["failureTypes"] == {"dns_failure": 1, "webhook_http_error": 1}
    assert health["pendingAgeBuckets"] == {"lt5m": 1, "m5to60": 0, "h1to24": 0, "d1to3": 0, "gt3d": 0}
    assert health["pendingPlaceholderFieldCounts"] == {}
    assert health["pendingBattleResultStructuredFields"] == {
        "analysis": 1,
        "battle_id": 1,
        "current_battle_state": 1,
        "loser": 1,
        "next_hermes_action": 1,
            "proof": 1,
            "proof_readiness": 1,
            "result": 1,
            "turns": 1,
            "why_it_matters": 1,
            "winner": 1,
    }
    assert health["backlogClassification"]["status"] == "dns-failed"
    assert health["backlogClassification"]["severity"] == "stream-safety-blocker"
    assert health["proofReadiness"]["status"] == "delivery-failed"
    assert health["proofReadiness"]["machineActionablePendingBattleResults"] == 1
    assert health["proofReadiness"]["localProofStatus"] == "classified-redacted-local-proof"
    assert health["proofReadiness"]["readyForLocalProofHandoff"] is False
    assert "repair DNS" in health["nextHermesAction"]
    assert health["failedEventTypes"] == {"batch": 1, "battle_result": 1}
    assert health["statusCounts"] == {"failed": 2, "pending": 1}


def test_archive_stale_failed_events_removes_terminal_failures(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    archive_dir = tmp_path / "logs" / "discord-events"
    latest_archive = tmp_path / "devstream" / "truth" / "discord-backlog-archive.json"
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "failed-old",
                    "timestamp": 100.0,
                    "event_type": "battle_result",
                    "status": "failed",
                    "retry_count": 3,
                    "last_error": "dns_failure",
                },
                {
                    "id": "failed-fresh",
                    "timestamp": 950.0,
                    "event_type": "battle_result",
                    "status": "failed",
                    "retry_count": 3,
                    "last_error": "validation_failed",
                },
                {
                    "id": "pending",
                    "timestamp": 990.0,
                    "event_type": "batch",
                    "status": "pending",
                    "retry_count": 0,
                },
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_DIR", archive_dir)
    monkeypatch.setattr(event_queue_lib, "BACKLOG_ARCHIVE_LATEST", latest_archive)

    archived = event_queue_lib.archive_stale_failed_events(max_age_sec=600, now=1000.0)

    assert archived == 1
    events = json.loads(queue_file.read_text(encoding="utf-8"))
    assert [event["id"] for event in events] == ["failed-fresh", "pending"]
    archive = json.loads(latest_archive.read_text(encoding="utf-8"))
    assert archive["reason"] == "stale-failed-discord-events-archived-before-live-readiness"
    assert archive["archivalDisposition"] == "terminal-failed-events-archived-locally-not-retried"
    assert archive["archivedEventTypes"] == {"battle_result": 1}
    assert archive["events"][0]["statusBeforeArchive"] == "failed"

def test_queue_health_marks_structured_backlog_as_local_proof_classified():
    health = queue_health_summary(
        [
            {
                "id": "p1",
                "timestamp": 90.0,
                "event_type": "battle_result",
                "status": "pending",
                "retry_count": 0,
                "battle_id": "battle-gen9ou-123",
                "winner": "Example",
                "loser": "fouler-play",
                "turns": 31,
                "proof": {"battleIds": ["gen9ou-123"], "items": ["battle `123`"]},
                "analysis": {
                    "currentBattleState": "battle loss; vs Example; 31 turns; id 123",
                    "whyItMatters": "loss proof should be visible locally",
                    "nextHermesAction": "review the replay",
                },
                "current_battle_state": "battle loss; vs Example; 31 turns; id 123",
                "why_it_matters": "loss proof should be visible locally",
                "next_hermes_action": "review the replay",
                "proof_readiness": {"status": "proof-ready"},
            }
        ],
        now=100.0,
    )

    assert health["status"] == "backlogged"
    assert health["stalePendingBacklog"] == 0
    assert health["freshPendingBacklog"] == 1
    assert health["proofReadiness"]["status"] == "queue-backlogged"
    assert health["proofReadiness"]["localProofStatus"] == "classified-redacted-local-proof"
    assert health["proofReadiness"]["localProofClassified"] is True
    assert health["proofReadiness"]["readyForLocalProofHandoff"] is True
    assert health["proofReadiness"]["machineActionablePendingBattleResults"] == 1
    assert health["proofReadiness"]["missingStructuredFieldCounts"] == {}


def test_read_queue_retries_transient_windows_file_lock(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text('[{"id":"event-1","status":"pending"}]', encoding="utf-8")
    original_open = builtins.open
    calls = {"count": 0}

    def flaky_open(path, *args, **kwargs):
        if Path(path) == queue_file and calls["count"] == 0:
            calls["count"] += 1
            raise PermissionError("temporary queue writer lock")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setattr(event_queue_lib, "QUEUE_LOCK_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(event_queue_lib, "QUEUE_LOCK_RETRY_DELAY_SEC", 0)
    monkeypatch.setattr(builtins, "open", flaky_open)

    assert event_queue_lib.read_queue() == [{"id": "event-1", "status": "pending"}]
    assert calls["count"] == 1


def test_read_queue_recovers_from_last_good_backup_after_corrupt_live_file(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)

    event_id = event_queue_lib.queue_event("mission_alert", "battles", "queue durability proof", dedup_window_sec=0)
    expected = event_queue_lib.read_queue()
    assert event_id is not None
    assert event_queue_lib._queue_backup_file().exists()

    queue_file.write_text("[", encoding="utf-8")

    assert event_queue_lib.read_queue() == expected
    assert list(tmp_path.glob("events_queue.json.corrupt-*"))


def test_webhook_dns_failure_is_reported_without_posting(monkeypatch):
    import infrastructure.event_poster as event_poster
    import urllib.request

    def raise_dns(*_args, **_kwargs):
        raise urllib.error.URLError(socket.gaierror("getaddrinfo failed"))

    monkeypatch.setattr(urllib.request, "urlopen", raise_dns)

    result = event_poster._post_via_webhook(
        {"id": "event-1", "event_type": "battle_result", "channel": "battles"},
        "https://discord.com/api/webhooks/example/token",
        "safe content",
    )

    assert result["ok"] is False
    assert result["errorCode"] == "dns_failure"
    assert classify_delivery_error(result["errorCode"]) == "dns_failure"


def test_redacted_report_summary_is_concise_and_secret_safe_for_loss_payload():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs Example",
        "Battle battle-gen9ou-999 ended loss against Example.",
        "The report should be useful without leaking credentials.",
        "battle_id=battle-gen9ou-999; token=super-secret-token",
        "Review timer handling next.",
        result="loss",
        opponent="Example",
        battle_id="battle-gen9ou-999-privatehash",
        replay_url="https://replay.pokemonshowdown.com/battle-gen9ou-999-privatehash",
        decisive_reason="Loss came from disconnect behavior after token=super-secret-token.",
        next_battle_action="Check reconnect handling before the next queue.",
    )

    summary = redacted_report_summary(payload)
    rendered = json.dumps(summary)

    assert summary["result"] == "loss"
    assert summary["opponent"] == "Example"
    assert summary["battleIds"] == ["gen9ou-999"]
    assert summary["opsSignal"] == "operational-loss"
    assert summary["replay"]["status"] == "pending-public-upload"
    assert summary["currentBattleState"] == "battle loss; vs Example; id 999-privatehash; replay pending gen9ou-999"
    assert summary["whyItMatters"]
    assert "disconnect behavior" in summary["viewerSummary"]
    assert "Check reconnect handling" in summary["nextAction"]
    assert summary["nextHermesAction"] == "Check reconnect handling before the next queue."
    assert "super-secret-token" not in rendered
    assert "REDACTED" in rendered


def test_contract_message_shape():
    message = build_contract_message(
        "CODE_FIX",
        "reporting path standardized",
        "central helper now builds the required message shape",
        "prevents ad hoc fragments from hitting the project channel",
        "pytest target=test_contract_message_shape; source=unit-test",
        "wire remaining call sites if new reporting surfaces appear",
    )
    assert message.startswith("[CODE_FIX] **reporting path standardized**")
    assert "📝 **What happened:**\ncentral helper now builds the required message shape" in message
    assert "🎯 **Why it matters:**\nprevents ad hoc fragments from hitting the project channel" in message
    assert "🔎 **Proof:**\n- pytest target=test_contract_message_shape\n- source `unit-test`" in message
    assert "⏭️ **Remaining:**\n- wire remaining call sites if new reporting surfaces appear" in message
    assert is_contract_message(message)


def test_payload_is_formatted_before_queue(monkeypatch, tmp_path):
    queue_file = tmp_path / "events_queue.json"
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    queue_file.write_text("[]", encoding="utf-8")

    import infrastructure.event_queue_lib as event_queue_lib
    event_queue_lib = importlib.reload(event_queue_lib)

    payload = build_contract_payload(
        "PROOF",
        "battle result win vs sample",
        "run_battle queued a result",
        "poster should not have to decode raw json",
        "battle_id=battle-gen9ou-1; result=win",
        "append replay later if available",
        source="unit-test",
    )

    event_id = event_queue_lib.queue_event("battle_result", "battles", payload, dedup_window_sec=0)
    assert event_id is not None

    pending = event_queue_lib.get_pending_events()
    assert len(pending) == 1
    content = pending[0]["content"]
    assert content.startswith("[PROOF] **battle result win vs sample**")
    assert "🔎 **Proof:**\n- battle `1`" in content
    assert "- source `unit-test`" in content
    queue_file.unlink(missing_ok=True)


def test_payload_formatter_passes_through_contract_messages():
    message = build_contract_message(
        "PROGRESSION",
        "already formatted",
        "message already matches contract",
        "double-formatting would be noisy",
        "unit proof",
        "none",
    )
    assert format_payload_or_message(message) == message


def test_payload_formatter_converts_json_payload():
    payload = build_contract_payload(
        "PROOF",
        "loss review ready",
        "monitor generated a review",
        "reviews need proof-backed summaries",
        "replay=https://example.test/replay",
        "read saved report",
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **loss review ready**")
    assert "📝 **What happened:**\nmonitor generated a review" in formatted
    assert "⏭️ **Remaining:**\n- read saved report" in formatted


def test_payload_formatter_builds_operator_facing_battle_report():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs GOATZILASPAMMER",
        "Battle battle-gen9ou-2555107042 ended loss against GOATZILASPAMMER.",
        "Operator-facing battle posts should immediately show whether the bot is climbing through repeatable play, variance, or an operational failure.",
        "battle_id=battle-gen9ou-2555107042; result=loss; team_file=fat-team-1-stall.txt; opponent=GOATZILASPAMMER; turns=47; replay=https://replay.pokemonshowdown.com/gen9ou-2555107042",
        "Append replay or ladder delta if more context lands after posting.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2555107042",
        result="loss",
        team_file="fat-team-1-stall.txt",
        opponent="GOATZILASPAMMER",
        turns=47,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2555107042",
        recent_record="last 5: 2-3 (40% WR)",
        decisive_reason="GOATZILASPAMMER closed the endgame before the bot stabilized the board.",
        next_battle_action="Review the replay before the next queue and tag whether this was policy, matchup, or ops.",
        elo_before=1223,
        elo_after=1203,
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **battle result loss vs GOATZILASPAMMER**")
    assert "battle finished loss vs GOATZILASPAMMER using 1 stall in 47 turns" in formatted
    assert "last 5: 2-3 (40% WR)" in formatted
    assert "Operator-facing battle posts should" not in formatted
    assert "battle updates should" not in formatted
    assert "loss changed the ladder run vs GOATZILASPAMMER" in formatted
    assert "GOATZILASPAMMER closed the endgame before the bot stabilized the board." in formatted
    assert "next battle focus: Review the replay before the next queue and tag whether this was policy, matchup, or ops." in formatted
    assert "- replay `gen9ou-2555107042`: https://replay.pokemonshowdown.com/gen9ou-2555107042" in formatted
    assert "- ELO `lost 20 (1223 → 1203, -20)`" in formatted


def test_payload_formatter_replaces_short_id_battle_recaps_with_fact_line():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs Smurf42069",
        "2635307132: loss vs Smurf42069; replay pending-public-upload.",
        "Operator-facing battle posts should report the exact ladder window and avoid invented flavor text.",
        "battle_id=battle-gen9ou-2635307132; result=loss; opponent=Smurf42069",
        "resolve replay link and tag a concrete cause",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2635307132",
        result="loss",
        opponent="Smurf42069",
        turns=41,
        replay_status="pending-public-upload",
        recent_record="last 5: 4-1 (80% WR)",
        decisive_reason="Replay is not public yet, so the loss is recorded without a claimed strategic cause.",
        next_battle_action="Resolve the replay link before assigning a strategic failure tag.",
        elo_before=1494,
        elo_after=1474,
        rating_delta=-20,
    )

    formatted = format_payload_or_message(payload)

    assert "2635307132: loss vs Smurf42069; replay pending-public-upload." not in formatted
    assert "battle finished loss vs Smurf42069 in 41 turns" in formatted
    assert "last 5: 4-1 (80% WR)" in formatted
    assert "closed the endgame before the bot stabilized the board" not in formatted
    assert "ELO lost 20" in formatted
    assert "ELO ELO" not in formatted
    assert formatted.count("replay pending public upload") == 1


def test_payload_formatter_generates_clean_battle_fact_line_without_explicit_what():
    payload = build_contract_payload(
        "PROOF",
        "battle result win vs Joshjay860",
        "",
        "Operator-facing battle posts should report concrete ladder facts.",
        "battle_id=battle-gen9ou-2635338540; result=win; team_file=gen9/ou/fat-team-1-stall; opponent=Joshjay860; turns=24; replay=https://replay.pokemonshowdown.com/gen9ou-2635338540; replay_status=public",
        "append replay or ladder delta if it becomes available",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2635338540",
        result="win",
        team_file="gen9/ou/fat-team-1-stall",
        opponent="Joshjay860",
        turns=24,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2635338540",
        replay_status="public",
        recent_record="last 5: 1-4 (20% WR)",
        elo_before=1302,
        elo_after=1318,
        rating_delta=16,
    )

    formatted = format_payload_or_message(payload)
    summary = redacted_report_summary(formatted)

    assert "battle finished win vs Joshjay860 using 1 stall in 24 turns" in formatted
    assert "last 5: 1-4 (20% WR)" in formatted
    assert "2635338540: win vs Joshjay860" not in formatted
    assert summary["opponent"] == "Joshjay860"
    assert summary["currentBattleState"] == "battle win; vs Joshjay860; 24 turns; id 2635338540; public replay gen9ou-2635338540"


def test_redacted_report_summary_sanitizes_opponent_from_formatted_report():
    payload = build_contract_payload(
        "PROOF",
        "battle result win vs Joshjay860",
        "2635338540: win vs Joshjay860; replay public; ELO gained 16 (1302 -> 1318, +16); last 5: 1-4 (20% WR)",
        "battle updates should confirm concrete proof",
        "battle_id=battle-gen9ou-2635338540; result=win; opponent=Joshjay860; turns=24",
        "append replay or ladder delta",
        battle_id="battle-gen9ou-2635338540",
        result="win",
        opponent="Joshjay860",
        turns=24,
    )
    formatted = format_payload_or_message(payload)

    summary = redacted_report_summary(formatted)
    fields = structured_report_fields(formatted, event_type="battle_result")

    assert summary["opponent"] == "Joshjay860"
    assert fields["analysis"]["opponent"] == "Joshjay860"
    assert fields["result"] == "win"
    assert "Joshjay860 2635338540" not in summary["currentBattleState"]


def test_structured_report_fields_exposes_top_level_loss_result():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs TimerOpponent",
        "battle-gen9ou-timeout ended loss against TimerOpponent.",
        "operational losses must be machine-readable as losses",
        "battle_id=battle-gen9ou-timeout; result=loss; opponent=TimerOpponent; turns=17",
        "Inspect reconnect/timer logs for gen9ou-timeout before treating the TimerOpponent loss as team or policy signal.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-timeout",
        result="loss",
        opponent="TimerOpponent",
        turns=17,
    )

    fields = structured_report_fields(payload, event_type="battle_result")

    assert fields["result"] == "loss"
    assert fields["analysis"]["result"] == "loss"
    assert fields["winner"] == "TimerOpponent"
    assert fields["loser"] == "fouler-play"


def test_formatter_rebuilds_already_formatted_stale_battle_report():
    stale = build_contract_message(
        "PROOF",
        "battle result win vs Dr. Spacebar",
        "2635342342: win vs Dr. Spacebar; replay public; ELO gained 20 (1318 -> 1338, +20); last 5: 2-3 (40% WR); Win has public replay proof; compare it against nearby wins before claiming a repeatable pattern.; next battle focus: Keep the replay and compare the next wins for the same concrete pattern.",
        "battle updates should confirm concrete proof",
        [
            "battle 2635342342",
            "replay gen9ou-2635342342: https://replay.pokemonshowdown.com/gen9ou-2635342342",
            "win vs Dr. Spacebar 47 turns",
            "window last 5: 2-3 (40% WR)",
        ],
        "Append replay or ladder delta if more context lands after posting.",
    )

    formatted = format_payload_or_message(stale)
    summary = redacted_report_summary(formatted)

    assert "2635342342: win vs Dr. Spacebar" not in formatted
    assert "Win has public replay proof" not in formatted
    assert "battle updates should" not in formatted
    assert "battle finished win vs Dr. Spacebar in 47 turns" in formatted
    assert "last 5: 2-3 (40% WR)" in formatted
    assert summary["opponent"] == "Dr. Spacebar"
    assert "battle updates should" not in str(summary["whyItMatters"]).lower()


def test_event_poster_quality_gate_blocks_canned_battle_report():
    import infrastructure.event_poster as event_poster

    message = build_contract_message(
        "PROOF",
        "battle result loss vs Example",
        "battle finished loss vs Example in 18 turns",
        "battle updates should tell us whether this was variance",
        ["battle battle-gen9ou-123", "result loss vs Example", "18 turns"],
        "review replay",
    )

    event = {
        "id": "quality-bad",
        "event_type": "battle_result",
        "channel": "battles",
        "content": message,
    }

    findings = event_poster.report_quality_findings(event)

    assert any(item.startswith("banned_phrase:battle updates should") for item in findings)
    assert event_poster.validate_event_content(event)[0] is False


def test_event_poster_quality_gate_blocks_impossible_recent_record():
    import infrastructure.event_poster as event_poster

    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs Tsaun",
        "battle finished loss vs Tsaun in 18 turns; last 5: 3-1 (60% WR)",
        "loss vs Tsaun is ladder evidence; last 5: 3-1 (60% WR)",
        "battle_id=battle-gen9ou-2636046261; result=loss; opponent=Tsaun; turns=18",
        "Review replay.",
        battle_id="battle-gen9ou-2636046261",
        result="loss",
        opponent="Tsaun",
        turns=18,
    )
    message = format_payload_or_message(payload)

    findings = event_poster.report_quality_findings(
        {
            "id": "quality-bad-recent",
            "event_type": "battle_result",
            "channel": "battles",
            "content": message,
        }
    )

    assert "recent_record_count_mismatch:last5!=4" in findings


def test_payload_formatter_prefers_structured_recent_record_over_mismatched_text():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs RecordMismatch",
        "",
        "structured fields should own the recent record",
        "battle_id=battle-gen9ou-record; result=loss; opponent=RecordMismatch; turns=18",
        "Review replay.",
        battle_id="battle-gen9ou-record",
        result="loss",
        opponent="RecordMismatch",
        turns=18,
        recent_record="last 5: 5-0 (100% WR)",
        recent_wins=3,
        recent_losses=2,
        recent_window_size=5,
    )

    rendered = format_payload_or_message(payload)

    assert "last 5: 3-2 (60% WR)" in rendered
    assert "last 5: 5-0" not in rendered


def test_payload_formatter_omits_boolean_placeholder_turns():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs Placeholder",
        "Battle battle-gen9ou-2555107042 ended loss against Placeholder.",
        "Boolean placeholder turn counts should not render as a real battle length.",
        "battle_id=battle-gen9ou-2555107042; result=loss; turns=False",
        "wait for completed proof with a numeric turn count",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2555107042",
        result="loss",
        opponent="Placeholder",
        turns=False,
    )

    formatted = format_payload_or_message(payload)

    assert "False turns" not in formatted
    assert "in False turns" not in formatted
    assert "battle finished loss vs Placeholder" in formatted


def test_payload_formatter_marks_cached_elo_unverified_when_it_contradicts_result():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs MatronJames",
        "Battle battle-gen9ou-2555107042 ended loss against MatronJames.",
        "Operator-facing battle posts should not relabel results from cached ladder movement.",
        "battle_id=battle-gen9ou-2555107042; result=loss",
        "review the replay",
        source="unit-test",
        battle_id="battle-gen9ou-2555107042",
        result="loss",
        opponent="MatronJames",
        elo_before=1117,
        elo_after=1136,
    )
    formatted = format_payload_or_message(payload)
    fields = structured_report_fields(payload, event_type="battle_result")

    assert formatted.startswith("[PROOF] **battle result loss vs MatronJames**")
    assert "battle finished loss vs MatronJames" in formatted
    assert "- ELO `unverified (cached 1117, fetched 1136)`" in formatted
    assert "contradicts loss" not in formatted
    assert fields["winner"] == "MatronJames"
    assert fields["loser"] == "fouler-play"
    assert fields["analysis"]["result"] == "loss"


def test_payload_formatter_uses_authoritative_rating_delta_when_present():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs MatronJames",
        "Battle battle-gen9ou-2555107042 ended loss against MatronJames.",
        "Operator-facing battle posts should trust the signed Showdown rating delta.",
        "battle_id=battle-gen9ou-2555107042; result=loss",
        "review the replay",
        source="unit-test",
        battle_id="battle-gen9ou-2555107042",
        result="loss",
        opponent="MatronJames",
        elo_before=1065,
        elo_after=1048,
        rating_delta=-17,
    )
    formatted = format_payload_or_message(payload)

    assert "- ELO `lost 17 (1065 → 1048, -17)`" in formatted
    assert "check needed" not in formatted


def test_elo_delta_labels_match_result_direction():
    assert format_elo_delta(1136, 1117, "loss") == "ELO lost 19 (1136 → 1117, -19)"
    assert format_elo_delta(1117, 1136, "win") == "ELO gained 19 (1117 → 1136, +19)"
    assert format_elo_delta(1065, 1048, "loss", rating_delta=-17) == "ELO lost 17 (1065 → 1048, -17)"
    assert format_elo_delta(1136, 1117, "win") == "ELO unverified (cached 1136, fetched 1117)"
    assert format_elo_delta(1117, 1136, "loss") == "ELO unverified (cached 1117, fetched 1136)"


def test_replay_url_canonicalization_rejects_private_unresolved_links():
    assert (
        canonical_replay_url("https://replay.pokemonshowdown.com/battle-gen9ou-111.json")
        == "https://replay.pokemonshowdown.com/gen9ou-111"
    )
    assert (
        canonical_replay_url("https://replay.pokemonshowdown.com/gen9ou-111")
        == "https://replay.pokemonshowdown.com/gen9ou-111"
    )
    assert canonical_replay_url("https://replay.pokemonshowdown.com/battle-gen9ou-111-privatehash") == ""
    assert public_replay_id_candidate("battle-gen9ou-111-privatehash") == "gen9ou-111"


def test_payload_formatter_marks_private_replay_as_pending_not_public_link():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs PrivateReplay",
        "Battle battle-gen9ou-111-privatehash ended loss against PrivateReplay.",
        "Private room ids should not be shown as confident public replay URLs.",
        "replay=https://replay.pokemonshowdown.com/battle-gen9ou-111-privatehash",
        "wait for public upload or use local replay JSON",
        result="loss",
        battle_id="battle-gen9ou-111-privatehash",
        replay_url="https://replay.pokemonshowdown.com/battle-gen9ou-111-privatehash",
    )
    formatted = format_payload_or_message(payload)

    assert "- replay pending public upload `gen9ou-111`" in formatted
    assert "https://replay.pokemonshowdown.com/gen9ou-111" not in formatted


def test_pending_replay_battle_result_is_not_hermes_analysis_ready():
    payload = build_contract_payload(
        "PROOF",
        "battle result win vs PendingReplay",
        "",
        "Operator-facing battle posts should report concrete ladder facts.",
        "battle_id=battle-gen9ou-222-privatehash; result=win; opponent=PendingReplay; turns=12",
        "Append replay or ladder delta if more context lands after posting.",
        battle_id="battle-gen9ou-222-privatehash",
        result="win",
        opponent="PendingReplay",
        turns=12,
        replay_status="pending-public-upload",
        recent_record="last 5: 3-2 (60% WR)",
    )

    formatted = format_payload_or_message(payload)
    fields = structured_report_fields(formatted, event_type="battle_result")

    readiness = fields["proof_readiness"]
    assert readiness["status"] == "proof-ready"
    assert readiness["readyForHermes"] is False
    assert "replay_pending_public_upload" in readiness["qualityGaps"]
    assert "battle_cause_unclassified" in readiness["qualityGaps"]
    # D4/D6: parked default -> "Resolve public replay, then classify" prompt suppressed
    assert not fields["analysis"]["nextHermesAction"].startswith("Resolve public replay")


def test_public_replay_without_cause_requires_hermes_classification():
    payload = build_contract_payload(
        "PROOF",
        "battle result win vs PublicNoCause",
        "",
        "Operator-facing battle posts should report concrete ladder facts.",
        "battle_id=battle-gen9ou-333; result=win; opponent=PublicNoCause; turns=17; replay=https://replay.pokemonshowdown.com/gen9ou-333; replay_status=public",
        "Append replay or ladder delta if more context lands after posting.",
        battle_id="battle-gen9ou-333",
        result="win",
        opponent="PublicNoCause",
        turns=17,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-333",
        replay_status="public",
        recent_record="last 5: 4-1 (80% WR)",
    )

    formatted = format_payload_or_message(payload)
    fields = structured_report_fields(formatted, event_type="battle_result")

    readiness = fields["proof_readiness"]
    assert readiness["status"] == "proof-ready"
    assert readiness["readyForHermes"] is False
    assert "replay_pending_public_upload" not in readiness["qualityGaps"]
    assert "battle_cause_unclassified" in readiness["qualityGaps"]
    # D4/D6: parked default -> the classify imperative is suppressed (benign follow-up remains)
    assert not fields["analysis"]["nextHermesAction"].startswith("Classify")


def test_payload_formatter_flags_operational_losses():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs oTheus03",
        "Battle battle-gen9ou-2556331152 ended loss against oTheus03.",
        "Operator-facing battle posts should immediately show whether the bot is climbing through repeatable play, variance, or an operational failure.",
        "battle_id=battle-gen9ou-2556331152; result=loss; team_file=fat-team-1-stall.txt; opponent=oTheus03; turns=54; replay=https://replay.pokemonshowdown.com/gen9ou-2556331152",
        "Append replay or ladder delta if more context lands after posting.",
        source="fp.run_battle",
        battle_id="battle-gen9ou-2556331152",
        result="loss",
        team_file="fat-team-1-stall.txt",
        opponent="oTheus03",
        turns=54,
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2556331152",
        recent_record="last 5: 2-3 (40% WR)",
        decisive_reason="Loss came from inactivity/disconnect behavior, so this looks operational before it looks strategic.",
        next_battle_action="Review reconnect / timer handling before blaming the team.",
        performance_change="Two recent losses include disconnect or inactivity endings.",
    )
    formatted = format_payload_or_message(payload)
    assert "inactivity/disconnect behavior" in formatted
    assert "runtime or timer evidence is part of this ladder loss" in formatted


def test_payload_formatter_summarizes_batch_payload_without_blob_dump():
    payload = build_contract_payload(
        "PROOF",
        "batch complete 2-1",
        "bot_monitor closed a live batch with 3 battle(s) and queued the concise Discord summary.",
        "Routine batch updates should show the scoreline, replay coverage, and follow-up work without dumping the raw multiline summary blob into Proof.",
        "vs A https://replay.pokemonshowdown.com/battle-gen9ou-111\nvs B https://replay.pokemonshowdown.com/battle-gen9ou-222\nvs C",
        "Loss review queue: 1 replay(s) pending deeper analysis.",
        source="bot_monitor.batch_complete",
        batch_results=[
            ("A", "won", "https://replay.pokemonshowdown.com/battle-gen9ou-111"),
            ("B", "lost", "https://replay.pokemonshowdown.com/battle-gen9ou-222"),
            ("C", "won", None),
        ],
        analysis_count=1,
        recent_record="last 10: 6-4 (60% WR)",
        trend="improving",
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **batch complete 2-1**")
    assert "3-battle window finished at 2-1 (67% WR)" in formatted
    assert "top loss pattern: losses were split across opponents (1 unique)" in formatted
    assert "last 10: 6-4 (60% WR)" in formatted
    assert "improving" in formatted
    assert "public replays 2/3" in formatted
    assert "loss reviews queued" not in formatted
    assert "- replay `gen9ou-111`: https://replay.pokemonshowdown.com/gen9ou-111" in formatted
    assert "- replay `gen9ou-222`: https://replay.pokemonshowdown.com/gen9ou-222" in formatted
    assert "vs A https://replay.pokemonshowdown.com/battle-gen9ou-111" not in formatted


def test_payload_formatter_summarizes_pipeline_report_with_lead_issue_only():
    payload = build_contract_payload(
        "PROOF",
        "batch analysis #42 ready",
        "pipeline analyzed the latest 30-battle batch and prepared the channel summary.",
        "Batch analysis only helps if the report callout is compact, proof-backed, and easy to scan before opening the full report.",
        "report=batch_0042_20260310.md; top_issues=1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip",
        "Open the report if the lead issue still needs a concrete fix after the summary.",
        source="pipeline.analyze",
        report="batch_0042_20260310.md",
        top_issues="1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip\n2. Booster Valiant forces too many emergency Tera lines",
        recent_record="last 30: 11-19 (37% WR)",
        trend="slipping",
        next_battle_action="Preserve the Gliscor slot until Gholdengo is actually pinned.",
    )
    formatted = format_payload_or_message(payload)
    assert formatted.startswith("[PROOF] **batch analysis #42 ready**")
    assert "lead issue: 1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip" in formatted
    assert "last 30: 11-19 (37% WR)" in formatted
    assert "slipping" in formatted
    assert "full batch analysis: batch_0042_20260310.md" in formatted
    assert "- report `batch_0042_20260310.md`" in formatted
    assert "- top issue `1. Gholdengo structures still overload the Gliscor slot after Ting-Lu chip`" in formatted


def test_recent_results_summary_and_recurring_issue_helpers():
    battles = [
        {"result": "loss"},
        {"result": "win"},
        {"result": "loss"},
        {"result": "loss"},
        {"result": "loss"},
    ]
    summary = summarize_recent_results(battles, window=5)
    assert summary["record"] == "last 5: 1-4 (20% WR)"
    assert summary["streak"] == "loss x3"

    issue = top_recurring_issue([
        "disconnect on timer",
        "disconnect after reconnect",
        "hazards got away",
    ])
    assert issue.startswith("battle ended through inactivity/disconnect behavior")


def test_recent_results_summary_counts_operational_results_as_losses():
    battles = [
        {"result": "win"},
        {"result": "lost"},
        {"result": "timeout"},
        {"result": "disconnect after reconnect"},
        {"result": "won"},
    ]

    summary = summarize_recent_results(battles, window=5)

    assert summary["record"] == "last 5: 2-3 (40% WR)"
    assert summary["streak"] == "win x1"


def test_recent_results_summary_counts_ladder_ties_as_losses():
    battles = [
        {"result": "win"},
        {"result": "tie"},
        {"result": "draw"},
        {"result": "loss"},
        {"result": "won"},
    ]

    summary = summarize_recent_results(battles, window=5)

    assert summary["record"] == "last 5: 2-3 (40% WR)"
    assert summary["streak"] == "win x1"


def test_recent_results_summary_ignores_unfilled_stats_rows():
    battles = [
        {"result": "win"},
        {"result": ""},
        {"result": "loss"},
        {"result": None},
        {"result": "win"},
    ]

    summary = summarize_recent_results(battles, window=5)

    assert summary["record"] == "last 3: 2-1 (67% WR)"
    assert summary["window_size"] == 3
    assert summary["streak"] == "win x1"


def test_payload_formatter_renders_ladder_tie_as_loss():
    payload = build_contract_payload(
        "PROOF",
        "battle result tie vs TimerOpponent",
        "battle finished tie vs TimerOpponent in 18 turns",
        "tie must be treated as loss for ladder mission accounting",
        "battle_id=battle-gen9ou-testtie; result=tie; opponent=TimerOpponent; turns=18",
        "Inspect replay.",
        battle_id="battle-gen9ou-testtie",
        result="tie",
        opponent="TimerOpponent",
        turns=18,
        next_battle_action="Inspect replay.",
    )

    formatted = format_payload_or_message(payload)
    fields = structured_report_fields(formatted, event_type="battle_result")

    assert formatted.startswith("[PROOF] **battle loss vs TimerOpponent**")
    assert "battle finished loss vs TimerOpponent" in formatted
    assert "battle finished tie vs TimerOpponent" not in formatted
    assert fields["result"] == "loss"
    assert fields["winner"] == "TimerOpponent"
    assert fields["loser"] == "fouler-play"


def test_recent_results_summary_with_current_includes_new_loss():
    battles = [
        {"battle_id": "battle-gen9ou-1", "result": "win"},
        {"battle_id": "battle-gen9ou-2", "result": "win"},
        {"battle_id": "battle-gen9ou-3", "result": "win"},
        {"battle_id": "battle-gen9ou-4", "result": "win"},
        {"battle_id": "battle-gen9ou-5", "result": "win"},
    ]

    summary = summarize_recent_results_with_current(
        battles,
        {"battle_id": "battle-gen9ou-6", "result": "loss"},
        window=5,
    )

    assert summary["record"] == "last 5: 4-1 (80% WR)"
    assert summary["streak"] == "loss x1"


def test_recent_results_summary_with_current_dedupes_battle_tag_alias():
    battles = [
        {"battle_id": "battle-gen9ou-1", "result": "win"},
        {"battle_id": "battle-gen9ou-2", "result": "loss"},
        {"battle_id": "battle-gen9ou-3", "result": "loss"},
        {"battle_id": "battle-gen9ou-4", "result": "loss"},
        {"battle_tag": "battle-gen9ou-5", "result": "loss"},
    ]

    summary = summarize_recent_results_with_current(
        battles,
        {"battle_id": "battle-gen9ou-5", "result": "win"},
        window=5,
    )

    assert summary["record"] == "last 5: 2-3 (40% WR)"
    assert summary["streak"] == "win x1"


def test_recent_results_safety_alert_flags_loss_streak():
    battles = [
        {"battle_id": "battle-gen9ou-1", "result": "win"},
        {"battle_id": "battle-gen9ou-2", "result": "loss"},
        {"battle_id": "battle-gen9ou-3", "result": "loss"},
        {"battle_id": "battle-gen9ou-4", "result": "loss"},
        {"battle_id": "battle-gen9ou-5", "result": "loss"},
    ]

    alert = recent_results_safety_alert(
        battles,
        {"battle_id": "battle-gen9ou-6", "result": "loss"},
        trend_window=20,
        loss_streak_threshold=5,
    )

    assert alert["trigger"] == "loss-streak"
    assert alert["loss_streak"] == 5
    assert alert["trend_record"] == "last 6: 1-5 (17% WR)"


def test_recent_results_safety_alert_flags_low_recent_win_rate():
    battles = [
        {"battle_id": f"battle-gen9ou-{idx}", "result": "loss" if idx % 3 else "win"}
        for idx in range(1, 20)
    ]

    alert = recent_results_safety_alert(
        battles,
        {"battle_id": "battle-gen9ou-20", "result": "win"},
        trend_window=20,
        loss_streak_threshold=5,
        low_win_rate_threshold=0.45,
        min_decisive_for_rate=10,
    )

    assert alert["trigger"] == "low-recent-win-rate"
    assert alert["trend_record"] == "last 20: 7-13 (35% WR)"


def test_format_payload_collapses_duplicate_recent_window_label():
    formatted = format_payload_or_message(
        json.dumps(
            {
                "event_class": "STAGNATION",
                "headline": "Fouler recent win-rate safety alert",
                "what_happened": "recent decisive win rate 40% is below 45%; last 20: 8-12 (40% WR); last 5 last 5: 2-3 (40% WR)",
                "why_it_matters": "recent ladder trend is below the safety threshold",
                "proof": "battle_id=battle-gen9ou-1; trigger=low-recent-win-rate",
                "remaining": "Review recent replays before launching another improve cycle.",
                "recent_record": "last 5: 2-3 (40% WR)",
            }
        )
    )

    assert "last 5 last 5" not in formatted
    assert "last 5: 2-3 (40% WR)" in formatted



def test_improve_loop_parked_by_default_suppresses_review_prompts(monkeypatch):
    """D4/D6: with no servicer (default), the dead review/classify prompts are gone."""
    monkeypatch.delenv("FOULER_IMPROVE_LOOP_ACTIVE", raising=False)
    from infrastructure import discord_reporting as dr

    assert dr._improve_loop_active() is False
    action = dr._hermes_next_action(ops_signal="routine", result="loss", next_action="")
    assert action == dr.IMPROVE_LOOP_PARKED_NOTE
    assert "analyze the replay" not in action
    assert dr._default_next_battle_action({}, result="loss", replay={}) == ""
    assert dr._default_next_battle_action({}, result="win", replay={}) == ""


def test_improve_loop_active_restores_review_prompts(monkeypatch):
    """When a real servicer is declared active, the original prompts return."""
    monkeypatch.setenv("FOULER_IMPROVE_LOOP_ACTIVE", "1")
    from infrastructure import discord_reporting as dr

    assert dr._improve_loop_active() is True
    action = dr._hermes_next_action(ops_signal="routine", result="loss", next_action="")
    assert "analyze the replay" in action
    classify = dr._default_next_battle_action({"opponent": "X"}, result="loss", replay={})
    assert "classify" in classify.lower()
