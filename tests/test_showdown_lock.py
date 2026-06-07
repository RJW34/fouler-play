import json

from infrastructure import showdown_lock


def test_committed_showdown_lock_pins_jiggly_source():
    lock = showdown_lock.load_lock()

    assert lock["path"] == "D:\\Projects\\pokemon-showdown"
    assert lock["expected_head"] == "3d25154b0489523a2f5515ba9489292257b27666"
    assert lock["allow_dirty"] is False
    assert lock["websocket_path"] == "/showdown/websocket"


def test_verify_showdown_source_rejects_missing_path(tmp_path):
    lock_path = tmp_path / "showdown.lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "path": str(tmp_path / "missing-showdown"),
                "expected_head": "abc",
                "expected_branch": "master",
                "allow_dirty": False,
            }
        ),
        encoding="utf-8",
    )

    status = showdown_lock.verify_showdown_source(lock_path)

    assert status["ok"] is False
    assert "missing" in status["reason"]
