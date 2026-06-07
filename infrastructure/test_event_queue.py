#!/usr/bin/env python3
"""Integration tests for event queue system."""

import json
import os
import shutil
import sys
import time
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Use a test queue file
TEST_ROOT = Path(tempfile.mkdtemp(prefix="fouler_play_event_queue_test_"))
TEST_QUEUE = TEST_ROOT / "events_queue_test.json"
TEST_TRUTH_DIR = TEST_ROOT / "truth"
TEST_ARCHIVE_DIR = TEST_ROOT / "discord-events"
os.environ["EVENT_QUEUE_FILE"] = str(TEST_QUEUE)

# Clean slate
if TEST_QUEUE.exists():
    TEST_QUEUE.unlink()

from infrastructure.discord_reporting import build_contract_payload, replay_handoff_fields
import infrastructure.event_queue_lib as event_queue_lib

event_queue_lib.QUEUE_FILE = TEST_QUEUE
event_queue_lib.TRUTH_DIR = TEST_TRUTH_DIR
event_queue_lib.BACKLOG_ARCHIVE_DIR = TEST_ARCHIVE_DIR
event_queue_lib.BACKLOG_ARCHIVE_LATEST = TEST_TRUTH_DIR / "discord-backlog-archive.json"

queue_event = event_queue_lib.queue_event
read_queue = event_queue_lib.read_queue
get_pending_events = event_queue_lib.get_pending_events
mark_posted = event_queue_lib.mark_posted
mark_failed = event_queue_lib.mark_failed
expire_old_events = event_queue_lib.expire_old_events
cleanup_queue = event_queue_lib.cleanup_queue
queue_stats = event_queue_lib.queue_stats


def teardown_module(module):
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


def _bind_test_queue():
    TEST_ROOT.mkdir(parents=True, exist_ok=True)
    event_queue_lib.QUEUE_FILE = TEST_QUEUE
    event_queue_lib.TRUTH_DIR = TEST_TRUTH_DIR
    event_queue_lib.BACKLOG_ARCHIVE_DIR = TEST_ARCHIVE_DIR
    event_queue_lib.BACKLOG_ARCHIVE_LATEST = TEST_TRUTH_DIR / "discord-backlog-archive.json"


def _reset_test_queue():
    _bind_test_queue()
    TEST_QUEUE.write_text("[]", encoding="utf-8")

def test_basic_queue():
    """Test 1: Basic queue and read."""
    _reset_test_queue()
    eid = queue_event("test_event", "test_channel", "Hello world")
    assert eid is not None, "Should return event ID"
    
    pending = get_pending_events()
    assert len(pending) == 1, f"Expected 1 pending, got {len(pending)}"
    assert pending[0]["content"] == "Hello world"
    print("✅ Test 1: Basic queue/read PASSED")

def test_dedup():
    """Test 2: Deduplication within window."""
    _reset_test_queue()
    eid1 = queue_event("dedup_test", "ch", "Same message", dedup_window_sec=30)
    eid2 = queue_event("dedup_test", "ch", "Same message", dedup_window_sec=30)
    assert eid1 is not None
    assert eid2 is None, "Duplicate should be rejected"
    print("✅ Test 2: Deduplication PASSED")

def test_mark_posted():
    """Test 3: Mark as posted."""
    _reset_test_queue()
    eid = queue_event("post_test", "ch", f"Unique {time.time()}")
    assert mark_posted(eid)
    events = read_queue()
    found = [e for e in events if e["id"] == eid]
    assert found[0]["status"] == "posted"
    print("✅ Test 3: Mark posted PASSED")

def test_retry_and_fail():
    """Test 4: Retry logic and eventual failure."""
    _reset_test_queue()
    eid = queue_event("retry_test", "ch", f"Retry {time.time()}")
    for i in range(3):
        mark_failed(eid, f"error {i}")
    events = read_queue()
    found = [e for e in events if e["id"] == eid]
    assert found[0]["status"] == "failed"
    assert found[0]["retry_count"] == 3
    print("✅ Test 4: Retry/fail PASSED")

def test_simultaneous_batch_crash():
    """Test 5: Queue batch_complete + process_crash simultaneously."""
    # Clear test queue
    _reset_test_queue()
    
    eid1 = queue_event("batch_complete", "battles", "📊 Batch report",
                       precondition_check_fn="bot_is_alive")
    eid2 = queue_event("process_crash", "workspace", "🚨 Bot crashed",
                       precondition_check_fn="bot_is_dead", dedup_window_sec=60)
    
    assert eid1 is not None
    assert eid2 is not None
    
    pending = get_pending_events()
    assert len(pending) == 2
    
    # Verify preconditions are stored correctly
    batch_ev = [e for e in pending if e["event_type"] == "batch_complete"][0]
    crash_ev = [e for e in pending if e["event_type"] == "process_crash"][0]
    assert batch_ev["precondition_check"] == "bot_is_alive"
    assert crash_ev["precondition_check"] == "bot_is_dead"
    print("✅ Test 5: Simultaneous batch+crash queued with correct preconditions PASSED")

def test_fifo_ordering():
    """Test 6: 10 events queue in order."""
    _reset_test_queue()
    ids = []
    for i in range(10):
        eid = queue_event(f"order_{i}", "ch", f"Event {i} at {time.time()}")
        ids.append(eid)
        time.sleep(0.01)
    
    pending = get_pending_events()
    assert len(pending) == 10
    for i, ev in enumerate(pending):
        assert ev["id"] == ids[i], f"Order mismatch at {i}"
    print("✅ Test 6: FIFO ordering (10 events) PASSED")

def test_pending_backlog_cap_archives_oldest_without_posting():
    """Test 6b: Pending backlog is bounded and over-capacity proof is archived."""
    _reset_test_queue()
    old_max = event_queue_lib.MAX_PENDING_EVENTS
    event_queue_lib.MAX_PENDING_EVENTS = 3
    try:
        ids = []
        for i in range(4):
            eid = queue_event(f"cap_{i}", "ch", f"Event {i} at {time.time()}", dedup_window_sec=0)
            ids.append(eid)
            time.sleep(0.01)

        pending = get_pending_events()
        assert len(pending) == 3
        assert [ev["id"] for ev in pending] == ids[1:]

        archive = json.loads(event_queue_lib.BACKLOG_ARCHIVE_LATEST.read_text(encoding="utf-8"))
        assert archive["reason"] == "pending-discord-event-cap-archive"
        assert archive["archivedEventCount"] == 1
        assert archive["remainingPendingEventCount"] == 2
        assert archive["liveDiscordMessagesSent"] is False
        assert archive["events"][0]["eventType"] == "cap_0"
    finally:
        event_queue_lib.MAX_PENDING_EVENTS = old_max
    print("✅ Test 6b: Pending backlog cap archives oldest without posting PASSED")

def test_archive_preserves_pending_replay_summary():
    """Expired local proof must retain pending replay evidence after Discord lag."""
    _reset_test_queue()
    handoff = replay_handoff_fields(
        battle_tag="battle-gen9ou-2626011055-privatehash",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2626011055",
        verified_replay_url=None,
    )
    payload = build_contract_payload(
        "PROOF",
        "battle result win vs ArchiveCheck",
        "Battle battle-gen9ou-2626011055-privatehash ended win against ArchiveCheck.",
        "Archived Discord backlog should still point HERMES at the replay evidence.",
        (
            "battle_id=battle-gen9ou-2626011055-privatehash; result=win; "
            f"replay={handoff['replay_url']}; replay_status={handoff['replay_status']}"
        ),
        "Append ladder delta if more context lands after posting.",
        source="unit-test",
        battle_id="battle-gen9ou-2626011055-privatehash",
        result="win",
        opponent="ArchiveCheck",
        turns=12,
        replay_url=handoff["replay_url"],
        replay_id=handoff["replay_id"],
        replay_status=handoff["replay_status"],
        replay_public_verified=handoff["replay_public_verified"],
        raw_replay_url=handoff["raw_replay_url"],
        next_battle_action="Verify public upload before proof handoff.",
    )
    eid = queue_event("battle_result", "battles", payload, dedup_window_sec=0)
    assert eid is not None

    events = read_queue()
    events[0]["timestamp"] = time.time() - 700
    TEST_QUEUE.write_text(json.dumps(events), encoding="utf-8")

    expired = expire_old_events(600)
    assert expired == 1

    archive = json.loads(event_queue_lib.BACKLOG_ARCHIVE_LATEST.read_text(encoding="utf-8"))
    archived = archive["events"][0]
    assert "gen9ou-2626011055" in archived["battleIds"]
    assert archived["replay"] == {
        "status": "pending-public-upload",
        "id": "gen9ou-2626011055",
        "url": "",
    }
    assert archived["proofReadinessStatus"] == "proof-needs-fields"
    print("OK Test 6c: Pending replay archive proof PASSED")


def _replay_payload(
    *,
    headline: str,
    what_happened: str,
    proof_note: str,
    handoff=None,
) -> str:
    handoff = handoff or {}
    replay_url = handoff.get("replay_url")
    replay_status = handoff.get("replay_status") or "absent"
    proof = (
        "battle_id=battle-gen9ou-909-private; result=win; opponent=DedupeA; turns=12; "
        f"replay={replay_url or ''}; replay_status={replay_status}; {proof_note}"
    )
    return build_contract_payload(
        "PROOF",
        headline,
        what_happened,
        "Replay proof must survive queue lag and semantic idempotency.",
        proof,
        "none",
        source="unit-test",
        battle_id="battle-gen9ou-909-private",
        result="win",
        opponent="DedupeA",
        turns=12,
        replay_url=replay_url,
        replay_id=handoff.get("replay_id"),
        replay_status=replay_status,
        replay_public_verified=handoff.get("replay_public_verified"),
        raw_replay_url=handoff.get("raw_replay_url"),
        next_battle_action="none",
    )


def test_battle_result_idempotency_uses_battle_id_beyond_hash_window():
    _reset_test_queue()
    payload1 = build_contract_payload(
        "PROOF",
        "battle result win vs DedupeA",
        "Battle battle-gen9ou-909-private ended win against DedupeA.",
        "First wording.",
        "battle_id=battle-gen9ou-909-private; result=win",
        "none",
        source="unit-test",
        battle_id="battle-gen9ou-909-private",
        result="win",
        opponent="DedupeA",
        turns=12,
        next_battle_action="none",
    )
    payload2 = build_contract_payload(
        "PROOF",
        "battle result win vs DedupeA duplicate wording",
        "Same battle changed wording but has no replay upgrade.",
        "Second wording should still dedupe.",
        "battle_id=battle-gen9ou-909-private; result=win",
        "none",
        source="unit-test",
        battle_id="battle-gen9ou-909-private",
        result="win",
        opponent="DedupeA",
        next_battle_action="none",
    )

    eid1 = queue_event("battle_result", "battles", payload1, dedup_window_sec=0)
    eid2 = queue_event("battle_result", "battles", payload2, dedup_window_sec=0)

    assert eid1 is not None
    assert eid2 is None
    assert read_queue()[0]["idempotency_key"] == "battle_result:battles:gen9ou-909"


def test_pending_battle_result_public_replay_upgrade_refreshes_stale_event():
    _reset_test_queue()
    pending_handoff = replay_handoff_fields(
        battle_tag="battle-gen9ou-909-private",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-909",
        verified_replay_url=None,
    )
    public_handoff = replay_handoff_fields(
        battle_tag="battle-gen9ou-909-private",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-909",
        verified_replay_url="https://replay.pokemonshowdown.com/gen9ou-909",
    )

    eid1 = queue_event(
        "battle_result",
        "battles",
        _replay_payload(
            headline="battle result win vs DedupeA pending replay",
            what_happened="Battle battle-gen9ou-909-private ended win against DedupeA.",
            proof_note="initial queue saw pending public upload",
            handoff=pending_handoff,
        ),
        dedup_window_sec=0,
    )
    assert eid1 is not None

    stale_ts = time.time() - 700
    events = read_queue()
    events[0]["timestamp"] = stale_ts
    TEST_QUEUE.write_text(json.dumps(events), encoding="utf-8")

    eid2 = queue_event(
        "battle_result",
        "battles",
        _replay_payload(
            headline="battle result win vs DedupeA public replay",
            what_happened="Same battle replay is now publicly available.",
            proof_note="late queue pass verified public upload",
            handoff=public_handoff,
        ),
        dedup_window_sec=0,
    )

    assert eid2 == eid1
    events = read_queue()
    assert len(events) == 1
    event = events[0]
    assert event["timestamp"] > stale_ts
    assert event["status"] == "pending"
    assert event["retry_count"] == 0
    assert event["upgrade_reason"] == "public-replay-url-available"
    assert event["upgrade_count"] == 1
    assert event["proof"]["replay"] == {
        "status": "public",
        "id": "gen9ou-909",
        "url": "https://replay.pokemonshowdown.com/gen9ou-909",
    }
    assert event["proof_readiness"]["status"] == "proof-ready"
    assert "https://replay.pokemonshowdown.com/gen9ou-909" in event["content"]
    assert expire_old_events(600) == 0


def test_posted_pending_battle_result_public_replay_queues_followup():
    _reset_test_queue()
    pending_handoff = replay_handoff_fields(
        battle_tag="battle-gen9ou-909-private",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-909",
        verified_replay_url=None,
    )
    public_handoff = replay_handoff_fields(
        battle_tag="battle-gen9ou-909-private",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-909",
        verified_replay_url="https://replay.pokemonshowdown.com/gen9ou-909",
    )

    eid1 = queue_event(
        "battle_result",
        "battles",
        _replay_payload(
            headline="battle result win vs DedupeA pending replay",
            what_happened="Battle battle-gen9ou-909-private ended win against DedupeA.",
            proof_note="initial Discord post only had pending replay proof",
            handoff=pending_handoff,
        ),
        dedup_window_sec=0,
    )
    assert eid1 is not None
    assert mark_posted(eid1)

    eid2 = queue_event(
        "battle_result",
        "battles",
        _replay_payload(
            headline="battle result win vs DedupeA public replay",
            what_happened="Same battle replay is now publicly available.",
            proof_note="late queue pass verified public upload",
            handoff=public_handoff,
        ),
        dedup_window_sec=0,
    )

    assert eid2 is not None
    assert eid2 != eid1
    events = read_queue()
    assert len(events) == 2
    followup = [event for event in events if event["id"] == eid2][0]
    assert followup["status"] == "pending"
    assert followup["replay_upgrade_followup"] is True
    assert followup["proof"]["replay"]["status"] == "public"
    assert followup["proof"]["replay"]["url"] == "https://replay.pokemonshowdown.com/gen9ou-909"


def test_expiry():
    """Test 7: Event expiry."""
    _reset_test_queue()
    eid = queue_event("expire_test", "ch", "Will expire")
    # Manually backdate timestamp
    events = read_queue()
    events[0]["timestamp"] = time.time() - 700  # 11+ min ago
    TEST_QUEUE.write_text(json.dumps(events))
    
    expired = expire_old_events(600)
    assert expired == 1
    events = read_queue()
    assert events == [], "Expired events should be archived and compacted out of the live queue"
    print("✅ Test 7: Expiry PASSED")

def test_stats():
    """Test 8: Queue stats."""
    _bind_test_queue()
    stats = queue_stats()
    assert "total" in stats
    assert "pending" in stats
    print(f"✅ Test 8: Stats PASSED — {stats}")

# Run all tests
if __name__ == "__main__":
    test_basic_queue()
    test_dedup()
    test_mark_posted()
    test_retry_and_fail()
    test_simultaneous_batch_crash()
    test_fifo_ordering()
    test_archive_preserves_pending_replay_summary()
    test_pending_backlog_cap_archives_oldest_without_posting()
    test_battle_result_idempotency_uses_battle_id_beyond_hash_window()
    test_pending_battle_result_public_replay_upgrade_refreshes_stale_event()
    test_posted_pending_battle_result_public_replay_queues_followup()
    test_expiry()
    test_stats()
    
    # Cleanup
    TEST_QUEUE.unlink(missing_ok=True)
    print("\n🎉 ALL TESTS PASSED")
