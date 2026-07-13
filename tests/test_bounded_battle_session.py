from types import SimpleNamespace

from scripts import run_bounded_battle_session as bounded


def test_normal_bounded_exit_publishes_idle_truth(monkeypatch):
    calls = []
    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda command, **kwargs: calls.append((command, kwargs)) or SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(bounded, "active_battle_count", lambda: 0)
    published = []
    monkeypatch.setattr(
        bounded.state_store,
        "write_runtime_ready_status",
        lambda **kwargs: published.append(kwargs),
    )
    cleaned = []
    monkeypatch.setattr(bounded, "cleanup_owned_session_pid_file", lambda: cleaned.append(True) or True)

    assert bounded.main(["--", "python", "run.py", "--run-count", "1"]) == 0
    assert calls[0][0] == ["python", "run.py", "--run-count", "1"]
    assert published == [
        {
            "summary": "Bounded ladder session completed; no active battles remain.",
            "mode": "bounded_session_complete",
        }
    ]
    assert cleaned == [True]


def test_failed_or_inflight_exit_does_not_publish_idle_truth(monkeypatch):
    published = []
    monkeypatch.setattr(
        bounded.state_store,
        "write_runtime_ready_status",
        lambda **kwargs: published.append(kwargs),
    )
    monkeypatch.setattr(bounded, "cleanup_owned_session_pid_file", lambda: True)

    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=3),
    )
    monkeypatch.setattr(bounded, "active_battle_count", lambda: 0)
    assert bounded.main(["python", "run.py"]) == 3

    monkeypatch.setattr(
        bounded.subprocess,
        "run",
        lambda command, **kwargs: SimpleNamespace(returncode=0),
    )
    monkeypatch.setattr(bounded, "active_battle_count", lambda: 1)
    assert bounded.main(["python", "run.py"]) == 0
    assert published == []


def test_cleanup_owned_session_pid_file_removes_launcher_parent_claim(monkeypatch, tmp_path):
    pid_file = tmp_path / "devstream_battle_session.pid"
    pid_file.write_text(
        '{"pid": 4123, "command": ["python", "scripts/run_bounded_battle_session.py"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bounded, "SESSION_PID_FILE", pid_file)
    monkeypatch.setattr(bounded.os, "getpid", lambda: 5123)
    monkeypatch.setattr(bounded.os, "getppid", lambda: 4123)

    assert bounded.cleanup_owned_session_pid_file() is True
    assert not pid_file.exists()


def test_cleanup_owned_session_pid_file_preserves_replaced_claim(monkeypatch, tmp_path):
    pid_file = tmp_path / "devstream_battle_session.pid"
    pid_file.write_text(
        '{"pid": 9999, "command": ["python", "scripts/run_bounded_battle_session.py"]}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bounded, "SESSION_PID_FILE", pid_file)
    monkeypatch.setattr(bounded.os, "getpid", lambda: 5123)
    monkeypatch.setattr(bounded.os, "getppid", lambda: 4123)

    assert bounded.cleanup_owned_session_pid_file() is False
    assert pid_file.exists()


def test_missing_child_command_still_cleans_owned_pid_claim(monkeypatch):
    cleaned = []
    monkeypatch.setattr(bounded, "cleanup_owned_session_pid_file", lambda: cleaned.append(True) or True)

    assert bounded.main([]) == 2
    assert cleaned == [True]
