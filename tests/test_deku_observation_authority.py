import ast
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8-sig")


def _import_roots(tree: ast.AST) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _called_name(node: ast.Call) -> str:
    parts: list[str] = []
    target: ast.AST = node.func
    while isinstance(target, ast.Attribute):
        parts.append(target.attr)
        target = target.value
    if isinstance(target, ast.Name):
        parts.append(target.id)
    return ".".join(reversed(parts))


def test_production_observation_writer_has_no_chat_or_remote_sender():
    source = _source("infrastructure/event_poster.py")
    tree = ast.parse(source)

    assert _import_roots(tree).isdisjoint({"requests", "subprocess"})
    assert not {
        "_post_via_deku_remote",
        "_post_via_webhook",
        "_post_via_cli",
        "load_env_chain",
        "resolve_webhook_url",
    }.intersection(
        node.name for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    )
    forbidden_calls = {
        "os.system",
        "requests.post",
        "requests.request",
        "subprocess.Popen",
        "subprocess.call",
        "subprocess.run",
    }
    assert forbidden_calls.isdisjoint(
        _called_name(node) for node in ast.walk(tree) if isinstance(node, ast.Call)
    )
    lowered = source.lower()
    assert "api/webhooks" not in lowered
    assert "deku-discord-post" not in lowered
    assert "openclaw" not in lowered


def test_reporting_import_is_filesystem_side_effect_free(tmp_path):
    state_root = tmp_path / "state"
    log_root = tmp_path / "logs"
    explicit_log = log_root / "event_poster.log"
    env = os.environ.copy()
    env.update(
        {
            "PYTHONPATH": str(ROOT),
            "PYTHONDONTWRITEBYTECODE": "1",
            "FOULER_RUNTIME_STATE_ROOT": str(state_root),
            "FOULER_RUNTIME_LOG_ROOT": str(log_root),
            "EVENT_POSTER_LOG": str(explicit_log),
        }
    )

    subprocess.run(
        [
            sys.executable,
            "-B",
            "-c",
            "import infrastructure.event_queue_lib; import infrastructure.event_poster",
        ],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert not state_root.exists()
    assert not log_root.exists()
    assert not explicit_log.exists()


def test_production_reporting_does_not_discover_chat_credentials():
    forbidden_fragments = ("discord_", "webhook", "channel_id", "openclaw")
    for relative in ("infrastructure/event_poster.py", "infrastructure/event_queue_lib.py"):
        tree = ast.parse(_source(relative))
        env_names: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _called_name(node) != "os.getenv" or not node.args:
                continue
            if isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
                env_names.append(node.args[0].value.lower())
        assert all(
            not any(fragment in name for fragment in forbidden_fragments)
            for name in env_names
        ), (relative, env_names)


def test_mission_monitor_cannot_queue_imperative_alerts():
    source = _source("scripts/fouler_mission_monitor.py")
    tree = ast.parse(source)

    assert "queue_discord_alert" not in source
    assert "--queue-alerts" not in source
    assert not any(
        isinstance(node, (ast.Import, ast.ImportFrom))
        and "event_queue_lib" in ast.unparse(node)
        for node in ast.walk(tree)
    )


def test_retired_standalone_producers_fail_closed():
    path = ROOT / "infrastructure" / "event-handlers.py"
    spec = importlib.util.spec_from_file_location("retired_fouler_event_handlers", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main() == 2
    with pytest.raises(module.RetiredEventProducerError):
        module.EventHandler.on_wr_drop("test", 0.1, 0.2, -0.1)

    wrapper = _source("scripts/fouler_deku_event_producer.ps1").lower()
    assert "retired" in wrapper
    assert "exit 2" in wrapper
    assert "python" not in wrapper
    assert "event_poster.py" not in wrapper


def test_compose_has_no_event_producer_service():
    compose = _source("docker-compose.yml")

    assert "event-poster:" not in compose
    assert "infrastructure/event_poster.py" not in compose


def test_pipeline_retains_analysis_locally_without_channel_identity():
    source = _source("pipeline.py")
    tree = ast.parse(source)

    assert "DISCORD_CHANNEL_ID" not in source
    assert "queue_event" not in source
    assert "build_contract_payload" not in source
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and _called_name(node) == "run_autoresearch"
    ]
    assert calls
    for call in calls:
        keyword = next((item for item in call.keywords if item.arg == "queue_discord"), None)
        assert keyword is not None
        assert isinstance(keyword.value, ast.Constant)
        assert keyword.value.value is False


def test_routine_analysis_is_retained_locally_without_outbox_fanout(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(event_poster, "DEKU_EVENT_QUEUE_ROOT", tmp_path / "deku-events")
    monkeypatch.setattr(event_poster.event_queue_lib, "QUEUE_FILE", queue_file)

    result = event_poster.write_deku_observation(
        {
            "id": "analysis-1",
            "event_type": "autoresearch_summary",
            "channel": "project",
            "content": "Local analysis completed and remains attached to the cycle evidence.",
        }
    )

    assert result == {
        "ok": True,
        "status": "retained-local",
        "transport": "local_event_queue",
        "destinationAlias": "project",
        "blockers": [],
        "outboxWritten": False,
    }
    pending = tmp_path / "deku-events" / "pending"
    assert not pending.exists()


def test_observation_envelope_has_no_command_authority(monkeypatch, tmp_path):
    import infrastructure.event_poster as event_poster

    queue_file = tmp_path / "events_queue.json"
    queue_file.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(event_poster, "DEKU_EVENT_QUEUE_ROOT", tmp_path / "deku-events")
    monkeypatch.setattr(event_poster.event_queue_lib, "QUEUE_FILE", queue_file)

    result = event_poster.write_deku_observation(
        {
            "id": "condition-open",
            "event_type": "status_update",
            "channel": "battles",
            "content": "trigger=low-recent-win-rate; local threshold crossed",
            "dedup_key": "fouler-play:performance:low-recent-win-rate:open",
            "edge_state": "open",
            "recommended_next_action": "Review the bounded local evidence before approving another cycle.",
        }
    )

    assert result["ok"] is True
    path = next((tmp_path / "deku-events" / "pending").glob("*.json"))
    envelope = json.loads(path.read_text(encoding="utf-8"))
    assert envelope["kind"] == "observation"
    assert envelope["authority"] == "none"
    assert envelope["producer"] == "fouler-play"
    assert envelope["eventType"] == "status_update"
    assert envelope["dedupKey"] == "fouler-play:performance:low-recent-win-rate:open"
    assert envelope["evidenceRefs"] == [str(queue_file)]
    assert envelope["recommendedNextAction"]
    rendered = json.dumps(envelope)
    assert "actionRequired" not in rendered
    assert "nextHermesAction" not in rendered
