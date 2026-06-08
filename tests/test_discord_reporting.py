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
    structured_report_fields,
    summarize_recent_results,
    top_recurring_issue,
)
from infrastructure.event_queue_lib import classify_delivery_error, queue_health_summary


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


def test_event_poster_doctor_reports_redacted_transport(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    monkeypatch.setenv("EVENT_QUEUE_FILE", str(queue_file))
    monkeypatch.setattr(event_queue_lib, "QUEUE_FILE", queue_file)
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")

    payload = event_poster.build_doctor_payload()

    assert payload["ready"] is True
    assert payload["transportReady"] is True
    assert payload["queue"]["ready"] is True
    assert payload["config"]["aliases"]["project"]["redactedUrl"].endswith("/api/webhooks/REDACTED")
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
    monkeypatch.setenv("DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/example/token")
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
    queue_file.write_text(
        json.dumps(
            [
                {
                    "id": "event-1",
                    "timestamp": 1,
                    "event_type": "battle_result",
                    "channel": "battles",
                    "content": "[PROOF] **battle result**\n- battle `2613956411`\nsecret-token-should-not-render",
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
    assert reporting["transport"]["redactedUrl"].endswith("/api/webhooks/REDACTED")
    assert reporting["queue"]["deliveryFailures"] == 0
    assert "token" not in rendered
    assert "secret-token-should-not-render" not in rendered
    queue_after = json.loads(queue_file.read_text(encoding="utf-8"))
    assert queue_after[0]["status"] == "pending"


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
    assert delivery["errorCode"] == "stale_backlog_archived"
    assert delivery["queue"]["pendingBacklog"] == 1
    assert delivery["queue"]["pendingBattleResults"] == 1
    assert delivery["queue"]["stalePendingBacklog"] == 0
    assert delivery["queue"]["freshPendingBacklog"] == 1
    assert delivery["queue"]["expiredDeliveries"] == 0
    assert archive["schemaVersion"] == "fouler-play-discord-backlog-archive/v1"
    assert archive["archivedEventCount"] == 1
    assert archive["archivedBattleResultCount"] == 1
    assert archive["remainingFreshPendingEventCount"] == 1
    assert archive["remainingFreshBattleResultCount"] == 1
    assert archive["liveDiscordMessagesSent"] is False
    assert archive["events"][0]["eventIdHash"] != "event-stale-secret-id"
    assert archive["events"][0]["battleIds"] == ["battle-gen9ou-2613956411", "gen9ou-2613956411"]
    assert archive["secretValuesPrinted"] is False
    assert "secret-token-should-not-render" not in rendered_archive


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


def test_expire_old_events_archives_before_cleanup_can_drop_stale_events(monkeypatch, tmp_path):
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

    assert event_queue_lib.expire_old_events(600) == 2
    assert event_queue_lib.cleanup_queue(keep_last=1) == 0

    archive = json.loads((truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8"))
    queue_after = json.loads(queue_file.read_text(encoding="utf-8"))
    archive_files = list(archive_dir.glob("backlog-archive-*.json"))

    assert archive["archivedEventCount"] == 2
    assert archive["archivedEventTypes"] == {"autoresearch_summary": 1, "battle_result": 1}
    assert queue_after == []
    assert archive_files


def test_backlog_archive_retention_prunes_oldest_timestamped_archives(monkeypatch, tmp_path):
    import infrastructure.event_queue_lib as event_queue_lib

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text(
        json.dumps(
            [
                {"id": "event-old-1", "timestamp": 1, "event_type": "battle_result", "status": "pending", "retry_count": 0},
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

    assert event_queue_lib.expire_old_events(600) == len(stale_events)

    archive_files = list(archive_dir.glob("backlog-archive-*.json"))
    latest_text = (truth_dir / "discord-backlog-archive.json").read_text(encoding="utf-8")
    archive = json.loads(latest_text)

    assert len(latest_text.encode("utf-8")) <= 5000
    assert len(archive_files) == 1
    assert archive_files[0].read_text(encoding="utf-8") == latest_text
    assert archive["archiveByteGuard"] == "per-event-summaries-truncated"
    assert archive["archiveMaxBytes"] == 5000
    assert archive["archivedEventCount"] == len(stale_events)
    assert archive["archivedEventTypes"] == {"autoresearch_summary": 60, "battle_result": 60}
    assert archive["archivedEventSummaryCount"] == len(archive["events"])
    assert 0 <= archive["archivedEventSummaryCount"] < len(stale_events)
    assert archive["omittedArchivedEventSummaryCount"] == len(stale_events) - len(archive["events"])
    assert "token=" not in latest_text
    assert json.loads(queue_file.read_text(encoding="utf-8")) == []


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
    assert fields["analysis"]["currentBattleState"] == "battle win; vs RenderedOnly; 22 turns; id 2613956411; public replay gen9ou-2613956411"
    assert fields["analysis"]["whyItMatters"] == "rendered messages should still produce safe structure"
    assert fields["analysis"]["nextHermesAction"] == "none"
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
    assert "GOATZILASPAMMER closed the endgame before the bot stabilized the board." in formatted
    assert "next battle focus: Review the replay before the next queue and tag whether this was policy, matchup, or ops." in formatted
    assert "- replay `gen9ou-2555107042`: https://replay.pokemonshowdown.com/gen9ou-2555107042" in formatted
    assert "- ELO `lost 20 (1223 → 1203, -20)`" in formatted


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


def test_payload_formatter_does_not_render_contradictory_elo_delta():
    payload = build_contract_payload(
        "PROOF",
        "battle result loss vs MatronJames",
        "Battle battle-gen9ou-2555107042 ended loss against MatronJames.",
        "Operator-facing battle posts must not imply a loss improved ladder rating.",
        "battle_id=battle-gen9ou-2555107042; result=loss",
        "verify the account/rating source before claiming a ladder delta",
        source="unit-test",
        battle_id="battle-gen9ou-2555107042",
        result="loss",
        opponent="MatronJames",
        elo_before=1117,
        elo_after=1136,
    )
    formatted = format_payload_or_message(payload)

    assert "- ELO `check needed (cached 1117, fetched 1136, +19 contradicts loss)`" in formatted
    assert "1117 → 1136 ELO" not in formatted


def test_elo_delta_labels_match_result_direction():
    assert format_elo_delta(1136, 1117, "loss") == "ELO lost 19 (1136 → 1117, -19)"
    assert format_elo_delta(1117, 1136, "win") == "ELO gained 19 (1117 → 1136, +19)"
    assert "contradicts win" in format_elo_delta(1136, 1117, "win")
    assert "contradicts loss" in format_elo_delta(1117, 1136, "loss")


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
    assert "operator reports should flag ladder-invisible runtime failures immediately" in formatted


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
    assert "public replays 2/3; loss reviews queued 1; reviewed 1" in formatted
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
