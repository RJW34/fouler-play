import asyncio
import json
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from streaming.hybrid_dashboard import (
    DashboardDataProvider,
    parse_trace_turn,
    register_dashboard_routes,
)


class _FakeStateStore:
    def __init__(self, *, status=None, daily=None, battles=None):
        self._status = status or {"status": "Idle", "battle_info": ""}
        self._daily = daily or {"wins": 0, "losses": 0}
        self._battles = battles or {"battles": [], "count": 0, "updated": None}

    def read_status(self):
        return dict(self._status)

    def read_daily_stats(self):
        return dict(self._daily)

    def read_active_battles(self):
        return dict(self._battles)


def _write_trace(trace_dir: Path, filename: str, payload: dict) -> None:
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / filename).write_text(
        json.dumps(payload, ensure_ascii=True, indent=2),
        encoding="utf-8",
    )


def test_trace_aggregation_and_learning_signals(tmp_path):
    trace_dir = tmp_path / "decision_traces"
    _write_trace(
        trace_dir,
        "battle-gen9ou-1_turn1_1.json",
        {
            "battle_tag": "battle-gen9ou-1",
            "turn": 1,
            "timestamp": "2026-02-10T20:00:00Z",
            "decision_mode": "eval",
            "choice": "shadowball",
            "eval_scores_raw": {"shadowball": 0.8, "recover": 0.2},
            "hybrid": {
                "status": "applied",
                "engine_choice": "shadowball",
                "selected_decision": "recover",
                "override": True,
                "reason": "preserve hp",
                "candidates": ["shadowball", "recover"],
            },
        },
    )
    _write_trace(
        trace_dir,
        "battle-gen9ou-1_turn2_2.json",
        {
            "battle_tag": "battle-gen9ou-1",
            "turn": 2,
            "timestamp": "2026-02-10T20:00:01Z",
            "decision_mode": "eval",
            "choice": "shadowball",
            "eval_scores_raw": {"shadowball": 0.7, "protect": 0.3},
            "hybrid": {
                "status": "skipped",
                "engine_choice": "shadowball",
                "selected_decision": "shadowball",
                "override": False,
                "reason": "time_pressure",
                "candidates": ["shadowball", "protect"],
            },
        },
    )
    # Corrupt file should not crash payload generation.
    (trace_dir / "broken.json").write_text("{not-json", encoding="utf-8")

    fake_state = _FakeStateStore(
        status={"status": "Battling", "battle_info": "vs Example"},
        daily={"wins": 3, "losses": 1},
        battles={
            "battles": [
                {
                    "id": "battle-gen9ou-1",
                    "opponent": "Example",
                    "status": "active",
                    "slot": 1,
                }
            ],
            "count": 1,
            "updated": "2026-02-10T20:00:02Z",
        },
    )
    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=fake_state,
        scan_interval_sec=0.0,
    )

    state = provider.get_state_payload()

    assert state["stats"]["wins"] == 3
    assert state["stats"]["losses"] == 1
    assert state["stats"]["battle_count"] == 4
    assert state["stats"]["override_turn_count"] == 1
    assert state["stats"]["hybrid_turn_count"] == 2
    assert state["stats"]["override_rate"] == 50.0

    assert state["latest_decision"]["turn"] == 2
    assert state["latest_decision"]["engine_choice"] == "shadowball"
    assert state["latest_decision"]["hybrid_selected_choice"] == "shadowball"
    assert state["latest_decision"]["reason"] == "time_pressure"

    assert state["active_battles"][0]["current_turn"] == 2
    assert state["learning"]["top_override_patterns"][0]["pattern"] == "shadowball -> recover"
    assert state["learning"]["top_skip_reasons"][0]["reason"] == "time_pressure"
    assert state["trace_health"]["trace_count"] == 2
    assert state["trace_health"]["parse_errors"] == 1


def test_placeholder_payload_when_trace_data_missing(tmp_path):
    trace_dir = tmp_path / "decision_traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    (trace_dir / "broken.json").write_text("not-json", encoding="utf-8")

    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(),
        scan_interval_sec=0.0,
    )
    state = provider.get_state_payload()
    turns = provider.get_turns_payload(limit=50)

    assert state["latest_decision"]["reason"] == "no_trace_data"
    assert state["timeline"] == []
    assert state["trace_health"]["trace_count"] == 0
    assert state["trace_health"]["parse_errors"] == 1
    assert turns["turns"] == []
    assert turns["limit"] == 50
    assert "trace_health" in turns


def test_api_payload_schema_fields(tmp_path):
    trace_dir = tmp_path / "decision_traces"
    _write_trace(
        trace_dir,
        "battle-gen9ou-2_turn4_1.json",
        {
            "battle_tag": "battle-gen9ou-2",
            "turn": 4,
            "timestamp": "2026-02-10T21:00:00Z",
            "decision_mode": "eval",
            "choice": "earthquake",
            "eval_scores_raw": {"earthquake": 0.9, "protect": 0.1},
        },
    )

    dashboard_html = tmp_path / "dashboard.html"
    overlay_html = tmp_path / "overlay.html"
    dashboard_html.write_text("<html>dashboard</html>", encoding="utf-8")
    overlay_html.write_text("<html>overlay</html>", encoding="utf-8")

    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(
            daily={"wins": 1, "losses": 2},
            battles={"battles": [], "count": 0, "updated": "2026-02-10T21:00:00Z"},
        ),
        scan_interval_sec=0.0,
    )

    async def _run():
        app = web.Application()
        register_dashboard_routes(
            app,
            provider=provider,
            dashboard_html=dashboard_html,
            overlay_html=overlay_html,
        )
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            state_resp = await client.get("/api/dashboard/state")
            assert state_resp.status == 200
            state_payload = await state_resp.json()
            for key in (
                "decision_policy",
                "active_battles",
                "latest_decision",
                "timeline",
                "stats",
                "learning",
                "trace_health",
            ):
                assert key in state_payload

            turns_resp = await client.get("/api/dashboard/turns?limit=50")
            assert turns_resp.status == 200
            turns_payload = await turns_resp.json()
            for key in ("turns", "limit", "total_available", "trace_health"):
                assert key in turns_payload

            battles_resp = await client.get("/api/dashboard/battles")
            assert battles_resp.status == 200
            battles_payload = await battles_resp.json()
            for key in ("battles", "count", "decision_policy", "trace_health"):
                assert key in battles_payload

            dash_resp = await client.get("/dashboard/hybrid")
            assert dash_resp.status == 200
            overlay_resp = await client.get("/overlay/hybrid")
            assert overlay_resp.status == 200
        finally:
            await client.close()

    asyncio.run(_run())


def test_endgame_mode_is_preserved(tmp_path):
    trace_dir = tmp_path / "decision_traces"
    _write_trace(
        trace_dir,
        "battle-gen9ou-3_turn21_1.json",
        {
            "battle_tag": "battle-gen9ou-3",
            "turn": 21,
            "timestamp": "2026-02-10T22:00:00Z",
            "decision_mode": "endgame",
            "choice": "dracometeor",
            "eval_scores_raw": {"dracometeor": 1.0},
        },
    )
    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(),
        scan_interval_sec=0.0,
    )
    state = provider.get_state_payload()
    assert state["latest_decision"]["decision_mode"] == "endgame"
    assert state["timeline"][0]["decision_mode"] == "endgame"


def test_trace_reason_redacts_project_key():
    payload = {
        "battle_tag": "battle-gen9ou-4",
        "turn": 1,
        "timestamp": "2026-02-10T22:10:00Z",
        "decision_mode": "eval",
        "choice": "shadowball",
        "hybrid": {
            "status": "error",
            "reason": "failed with sk-proj-abcDEF1234567890_xyz",
        },
    }
    parsed = parse_trace_turn(payload, source_name="trace.json", fallback_epoch=0)
    assert parsed is not None
    assert "[redacted]" in parsed["reason"]
    assert "sk-proj-" not in parsed["reason"]
