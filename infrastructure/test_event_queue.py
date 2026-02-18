#!/usr/bin/env python3
"""Integration tests for event queue system."""

import json
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# Use a test queue file
TEST_QUEUE = PROJECT_ROOT / "events_queue_test.json"
os.environ["EVENT_QUEUE_FILE"] = str(TEST_QUEUE)

# Clean slate
if TEST_QUEUE.exists():
    TEST_QUEUE.unlink()

from infrastructure.event_queue_lib import (
    queue_event, read_queue, get_pending_events, mark_posted,
    mark_failed, expire_old_events, cleanup_queue, queue_stats,
)

def test_basic_queue():
    """Test 1: Basic queue and read."""
    eid = queue_event("test_event", "test_channel", "Hello world")
    assert eid is not None, "Should return event ID"
    
    pending = get_pending_events()
    assert len(pending) == 1, f"Expected 1 pending, got {len(pending)}"
    assert pending[0]["content"] == "Hello world"
    print("✅ Test 1: Basic queue/read PASSED")

def test_dedup():
    """Test 2: Deduplication within window."""
    eid1 = queue_event("dedup_test", "ch", "Same message", dedup_window_sec=30)
    eid2 = queue_event("dedup_test", "ch", "Same message", dedup_window_sec=30)
    assert eid1 is not None
    assert eid2 is None, "Duplicate should be rejected"
    print("✅ Test 2: Deduplication PASSED")

def test_mark_posted():
    """Test 3: Mark as posted."""
    eid = queue_event("post_test", "ch", f"Unique {time.time()}")
    assert mark_posted(eid)
    events = read_queue()
    found = [e for e in events if e["id"] == eid]
    assert found[0]["status"] == "posted"
    print("✅ Test 3: Mark posted PASSED")

def test_retry_and_fail():
    """Test 4: Retry logic and eventual failure."""
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
    TEST_QUEUE.write_text("[]")
    
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
    TEST_QUEUE.write_text("[]")
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

def test_expiry():
    """Test 7: Event expiry."""
    TEST_QUEUE.write_text("[]")
    eid = queue_event("expire_test", "ch", "Will expire")
    # Manually backdate timestamp
    events = read_queue()
    events[0]["timestamp"] = time.time() - 700  # 11+ min ago
    TEST_QUEUE.write_text(json.dumps(events))
    
    expired = expire_old_events(600)
    assert expired == 1
    events = read_queue()
    assert events[0]["status"] == "expired"
    print("✅ Test 7: Expiry PASSED")

def test_stats():
    """Test 8: Queue stats."""
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
    test_expiry()
    test_stats()
    
    # Cleanup
    TEST_QUEUE.unlink(missing_ok=True)
    print("\n🎉 ALL TESTS PASSED")
