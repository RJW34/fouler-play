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


def test_mcts_mode_and_candidates_are_preserved(tmp_path):
    trace_dir = tmp_path / "decision_traces"
    _write_trace(
        trace_dir,
        "battle-gen9ou-4_turn7_1.json",
        {
            "battle_tag": "battle-gen9ou-4",
            "turn": 7,
            "timestamp": "2026-02-10T22:05:00Z",
            "decision_mode": "mcts",
            "choice": "earthquake",
            "mcts_policy_raw": {"earthquake": 0.62, "stealthrock": 0.25, "switch toxapex": 0.13},
        },
    )
    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(),
        scan_interval_sec=0.0,
    )
    state = provider.get_state_payload()
    latest = state["latest_decision"]
    assert latest["decision_mode"] == "mcts"
    assert latest["candidate_list"] == ["earthquake", "stealthrock", "switch toxapex"]
    assert latest["engine_choice"] == "earthquake"


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


def test_public_turns_precomputed_once_per_rescan(tmp_path):
    trace_dir = tmp_path / "decision_traces"
    _write_trace(
        trace_dir,
        "battle-gen9ou-9_turn1_1.json",
        {
            "battle_tag": "battle-gen9ou-9",
            "turn": 1,
            "timestamp": "2026-07-04T10:00:00Z",
            "decision_mode": "eval",
            "choice": "surf",
            "eval_scores_raw": {"surf": 0.9},
        },
    )
    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(),
        scan_interval_sec=3600.0,
    )

    first = provider.trace_cache.get_public_turns()
    assert first and first[0]["battle_id"] == "battle-gen9ou-9"
    assert first[0]["engine_choice"] == "surf"

    # Between rescans the same precomputed list is reused: no per-request
    # re-sanitize of every cached turn (the old per-poll cost that wedged
    # the event loop at ~4000 traces).
    assert provider.trace_cache.get_public_turns() is first

    _write_trace(
        trace_dir,
        "battle-gen9ou-9_turn2_1.json",
        {
            "battle_tag": "battle-gen9ou-9",
            "turn": 2,
            "timestamp": "2026-07-04T10:00:01Z",
            "decision_mode": "eval",
            "choice": "protect",
            "eval_scores_raw": {"protect": 0.6},
        },
    )
    provider.trace_cache.refresh(force=True)
    second = provider.trace_cache.get_public_turns()
    assert second is not first
    assert len(second) == 2


def test_dashboard_state_route_offloads_slow_provider(tmp_path):
    """Regression for the OBS page-server wedge: a slow get_state_payload used
    to run inline in the route handler and block the event loop for seconds
    per poll; it must now run off-loop via asyncio.to_thread."""
    import time as _time

    class _SlowProvider:
        def get_state_payload(self):
            _time.sleep(1.0)
            return {"ok": True}

        def get_turns_payload(self, *, limit=50):
            return {"turns": [], "limit": limit}

        def get_battles_payload(self):
            return {"battles": []}

    dashboard_html = tmp_path / "dashboard.html"
    overlay_html = tmp_path / "overlay.html"
    dashboard_html.write_text("<html>dashboard</html>", encoding="utf-8")
    overlay_html.write_text("<html>overlay</html>", encoding="utf-8")

    async def _run():
        app = web.Application()
        register_dashboard_routes(
            app,
            provider=_SlowProvider(),
            dashboard_html=dashboard_html,
            overlay_html=overlay_html,
        )
        server = TestServer(app)
        client = TestClient(server)
        await client.start_server()
        try:
            fetch = asyncio.ensure_future(client.get("/api/dashboard/state"))
            loop = asyncio.get_event_loop()
            started = loop.time()
            await asyncio.sleep(0.05)
            elapsed = loop.time() - started
            resp = await fetch
            assert resp.status == 200
            payload = await resp.json()
            assert payload == {"ok": True}
        finally:
            await client.close()
        return elapsed

    elapsed = asyncio.run(_run())
    assert elapsed < 0.6, f"event loop blocked for {elapsed:.2f}s during dashboard state build"


def _simple_trace(turn: int, choice: str) -> dict:
    return {
        "battle_tag": "battle-gen9ou-77",
        "turn": turn,
        "timestamp": f"2026-07-04T11:00:{turn:02d}Z",
        "decision_mode": "eval",
        "choice": choice,
        "eval_scores_raw": {choice: 0.9},
    }


def test_payload_memo_coalesces_concurrent_polls(tmp_path):
    trace_dir = tmp_path / "decision_traces"
    _write_trace(trace_dir, "battle-gen9ou-77_turn1_1.json", _simple_trace(1, "surf"))

    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(),
        scan_interval_sec=0.0,
        payload_memo_ttl_sec=60.0,
    )
    first = provider.get_state_payload()
    # Within the TTL, repeated polls (OBS sources poll every ~1.5s) reuse the
    # same built payload instead of re-running the trace scan + aggregation.
    assert provider.get_state_payload() is first

    uncached = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(),
        scan_interval_sec=0.0,
        payload_memo_ttl_sec=0.0,
    )
    a = uncached.get_state_payload()
    assert uncached.get_state_payload() is not a


def test_trace_files_parse_and_sanitize_once_per_change(tmp_path, monkeypatch):
    import streaming.hybrid_dashboard as hd

    trace_dir = tmp_path / "decision_traces"
    _write_trace(trace_dir, "battle-gen9ou-77_turn1_1.json", _simple_trace(1, "surf"))
    _write_trace(trace_dir, "battle-gen9ou-77_turn2_1.json", _simple_trace(2, "protect"))

    provider = DashboardDataProvider(
        trace_dir=trace_dir,
        state_module=_FakeStateStore(),
        scan_interval_sec=0.0,
        payload_memo_ttl_sec=0.0,
    )
    assert len(provider.get_state_payload()["timeline"]) == 2

    parse_calls = {"count": 0}
    real_parse = hd.parse_trace_file

    def counting_parse(path):
        parse_calls["count"] += 1
        return real_parse(path)

    monkeypatch.setattr(hd, "parse_trace_file", counting_parse)

    _write_trace(trace_dir, "battle-gen9ou-77_turn3_1.json", _simple_trace(3, "toxic"))
    provider.trace_cache.refresh(force=True)
    payload = provider.get_state_payload()

    # Incremental rescan: only the NEW trace file is parsed/sanitized; the two
    # unchanged files are served from the per-entry cache.
    assert parse_calls["count"] == 1
    assert len(payload["timeline"]) == 3
    assert payload["timeline"][0]["turn"] == 3
