"""Contract for the retired Fouler-scoped host alert helper."""

import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
HELPER = REPO / "scripts" / "commit_hog_enqueue.py"


def test_retired_commit_hog_helper_fails_closed_without_queueing(tmp_path):
    queue_file = tmp_path / "events_queue.json"
    result = subprocess.run(
        [sys.executable, str(HELPER)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=30,
    )

    assert result.returncode == 2
    assert "retired" in result.stderr.lower()
    assert not queue_file.exists()
