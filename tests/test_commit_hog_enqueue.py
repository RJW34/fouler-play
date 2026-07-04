"""Tests for scripts/commit_hog_enqueue.py (commit-hog watchdog Discord enqueue).

The helper replaced a PowerShell `python -c` one-liner that PS 5.1 argument
quoting mangled into a SyntaxError. These tests run it exactly the way the
watchdog does: as a subprocess with env-var inputs, reading the LAST stdout
line as the event id.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "scripts" / "commit_hog_enqueue.py"


def _run_helper(tmp_path, content):
    env = os.environ.copy()
    env["FOULER_REPO"] = str(REPO)
    env["FOULER_OPS_ALERT_CONTENT"] = content
    env["EVENT_QUEUE_FILE"] = str(tmp_path / "events_queue.json")
    result = subprocess.run(
        [sys.executable, str(HELPER)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(REPO),
        timeout=60,
    )
    return result, tmp_path / "events_queue.json"


def test_queues_ops_alert_and_prints_id_as_last_line(tmp_path):
    content = "[OPS ALERT] JIGGLYPUFF commit-hog watchdog test: java (pid 1234): 7.0 GB commit > 6 GB limit"
    result, queue_file = _run_helper(tmp_path, content)
    assert result.returncode == 0, f"stderr: {result.stderr}"

    # The watchdog consumes the LAST stdout line as the event id.
    event_id = result.stdout.strip().splitlines()[-1]
    assert event_id and event_id != "deduped"

    events = json.loads(queue_file.read_text(encoding="utf-8"))
    assert len(events) == 1
    event = events[0]
    assert event["id"] == event_id
    assert event["event_type"] == "ops_alert"
    assert event["channel"] == "project"
    assert event["status"] == "pending"
    assert "commit-hog watchdog test" in event["content"]


def test_identical_content_within_window_dedupes(tmp_path):
    content = "[OPS ALERT] dedup-window probe for commit-hog watchdog"
    first, queue_file = _run_helper(tmp_path, content)
    assert first.returncode == 0, f"stderr: {first.stderr}"
    second, _ = _run_helper(tmp_path, content)
    assert second.returncode == 0, f"stderr: {second.stderr}"
    assert second.stdout.strip().splitlines()[-1] == "deduped"
    events = json.loads(queue_file.read_text(encoding="utf-8"))
    assert len(events) == 1


def test_empty_content_exits_nonzero_and_queues_nothing(tmp_path):
    result, queue_file = _run_helper(tmp_path, "")
    assert result.returncode == 2
    assert "empty" in result.stderr.lower()
    assert not queue_file.exists() or json.loads(queue_file.read_text(encoding="utf-8") or "[]") == []
