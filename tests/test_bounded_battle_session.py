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

    assert bounded.main(["--", "python", "run.py", "--run-count", "1"]) == 0
    assert calls[0][0] == ["python", "run.py", "--run-count", "1"]
    assert published == [
        {
            "summary": "Bounded ladder session completed; no active battles remain.",
            "mode": "bounded_session_complete",
        }
    ]


def test_failed_or_inflight_exit_does_not_publish_idle_truth(monkeypatch):
    published = []
    monkeypatch.setattr(
        bounded.state_store,
        "write_runtime_ready_status",
        lambda **kwargs: published.append(kwargs),
    )

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
