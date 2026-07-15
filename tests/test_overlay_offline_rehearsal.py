from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

from streaming import serve_obs_page


def test_offline_rehearsal_requires_explicit_flag() -> None:
    assert serve_obs_page.offline_rehearsal_requested({}, []) is False
    assert (
        serve_obs_page.offline_rehearsal_requested(
            {"FOULER_OBS_OFFLINE_REHEARSAL": "1"},
            [],
        )
        is True
    )
    assert serve_obs_page.offline_rehearsal_requested({}, ["--offline-rehearsal"]) is True


def _offline_state(monkeypatch: pytest.MonkeyPatch, *, battles: list[dict]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(serve_obs_page, "OFFLINE_REHEARSAL_MODE", True)
    monkeypatch.setattr(
        serve_obs_page.state_store,
        "read_active_battles",
        lambda: {
            "battles": battles,
            "count": len(battles),
            "max_slots": 3,
            "updated": now,
        },
    )
    monkeypatch.setattr(
        serve_obs_page.state_store,
        "read_status",
        lambda: {"status": "Idle", "updated": now},
    )
    monkeypatch.setattr(
        serve_obs_page.state_store,
        "read_daily_stats",
        lambda: {"wins": 4, "losses": 3},
    )


def test_offline_state_uses_external_active_battle_truth_without_live_authority(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battles = [
        {
            "id": "battle-gen9ou-local-1",
            "opponent": "FoulerLocalOpp",
            "slot": 1,
            "players": ["FoulerRehearsal", "FoulerLocalOpp"],
        }
    ]
    _offline_state(monkeypatch, battles=battles)

    def unexpected_credential_probe(*_args, **_kwargs):
        raise AssertionError("production credential truth must not be read")

    monkeypatch.setattr(
        serve_obs_page,
        "recent_showdown_credential_failure",
        unexpected_credential_probe,
    )

    payload = serve_obs_page.build_state_payload()

    assert payload["offline_rehearsal"] is True
    assert payload["runtime_mode"] == "offline_rehearsal"
    assert payload["battles"] == battles
    assert payload["count"] == 1
    assert payload["accounts_elo"] == {}
    assert payload["status"]["elo"] == "OFFLINE"
    assert payload["status"]["runtime_mode"] == "offline_rehearsal"
    assert payload["status"]["today_wins"] == 4


def test_offline_slot_consumes_real_decision_trace_without_remote_sprites(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    battle_id = "battle-gen9ou-local-2"
    battles = [
        {
            "id": battle_id,
            "opponent": "FoulerLocalOpp",
            "slot": 1,
            "started": datetime.now(timezone.utc).isoformat(),
            "players": ["FoulerRehearsal", "FoulerLocalOpp"],
        }
    ]
    _offline_state(monkeypatch, battles=battles)
    public_view = tmp_path / "latest-public-battle.json"
    public_view.write_text(
        json.dumps(
            {
                "schema": "fouler-public-battle-view/v1",
                "battle_id": battle_id,
                "turn": 3,
                "format": "gen9ou",
                "user": {
                    "active": {"name": "greattusk", "hp_percent": 88},
                    "reserve": [],
                },
                "opponent": {
                    "active": {"name": "dragapult", "hp_percent": 70},
                    "reserve": [],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(serve_obs_page, "PUBLIC_BATTLE_VIEW_PATH", public_view)
    monkeypatch.setattr(serve_obs_page, "BATTLE_STATS_PATH", tmp_path / "missing-stats.json")
    monkeypatch.setattr(serve_obs_page, "BATTLE_LOG_DIR", tmp_path / "logs")

    payload = serve_obs_page._slot_state_payload(1)
    battle_view = payload["battle_lab"]["battle_view"]

    assert payload["battle_lab"]["freshness"] == "current"
    assert battle_view["turn"] == 3
    assert battle_view["match_ref"] == "Private rehearsal battle"
    assert battle_view["user"]["active"]["display_name"] == "Great Tusk"
    assert battle_view["user"]["active"]["sprite_urls"] == []
    assert battle_view["opponent"]["active"]["sprite_urls"] == []


@pytest.mark.asyncio
async def test_offline_mode_never_creates_public_elo_replay_or_proxy_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serve_obs_page, "OFFLINE_REHEARSAL_MODE", True)

    def unexpected_client(*_args, **_kwargs):
        raise AssertionError("offline rehearsal must not create an external HTTP client")

    monkeypatch.setattr(serve_obs_page.aiohttp, "ClientSession", unexpected_client)

    assert await serve_obs_page.fetch_showdown_elo("FoulerRehearsal") is None
    assert await serve_obs_page._replay_exists("gen9ou-local") is False
    payload = {"battles": [{"id": "battle-gen9ou-local"}]}
    assert await serve_obs_page._merge_deku_battles(payload) is payload
    deku_response = await serve_obs_page.handle_deku_state(
        make_mocked_request("GET", "/deku-state")
    )
    magneton_response = await serve_obs_page.handle_magneton_state(
        make_mocked_request("GET", "/magneton-state")
    )
    assert deku_response.status == 503
    assert magneton_response.status == 503


def test_offline_mode_does_not_schedule_elo_refresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_obs_page, "OFFLINE_REHEARSAL_MODE", True)

    def unexpected_task(*_args, **_kwargs):
        raise AssertionError("offline rehearsal must not schedule ELO tasks")

    monkeypatch.setattr(serve_obs_page.asyncio, "create_task", unexpected_task)
    serve_obs_page._schedule_elo_refresh(force=True)


@pytest.mark.asyncio
async def test_offline_deep_health_skips_production_subprocess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serve_obs_page, "OFFLINE_REHEARSAL_MODE", True)

    def unexpected_subprocess(*_args, **_kwargs):
        raise AssertionError("offline rehearsal must not run production health probes")

    monkeypatch.setattr(serve_obs_page.subprocess, "run", unexpected_subprocess)
    monkeypatch.setattr(
        serve_obs_page,
        "_public_surface_health_payload",
        lambda singleton, probe: (
            {"healthy": True, "probe": probe, "singleton": singleton},
            200,
        ),
    )

    payload, status = await serve_obs_page._load_devstream_health_payload(
        {"duplicateCount": 0}
    )

    assert status == 200
    assert payload["offlineRehearsal"] is True
    assert payload["probe"]["method"] == "offline-rehearsal-local"


@pytest.mark.asyncio
async def test_offline_responses_block_external_browser_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(serve_obs_page, "OFFLINE_REHEARSAL_MODE", True)
    response = web.Response(text="local")

    await serve_obs_page._offline_rehearsal_response_headers(
        make_mocked_request("GET", "/overlay"),
        response,
    )

    assert response.headers["X-Fouler-Offline-Rehearsal"] == "1"
    assert "default-src 'self'" in response.headers["Content-Security-Policy"]
    assert "frame-src 'none'" in response.headers["Content-Security-Policy"]


def test_offline_server_binds_only_loopback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(serve_obs_page, "OFFLINE_REHEARSAL_MODE", True)
    assert serve_obs_page.server_bind_host() == "127.0.0.1"

    monkeypatch.setattr(serve_obs_page, "OFFLINE_REHEARSAL_MODE", False)
    assert serve_obs_page.server_bind_host() == "0.0.0.0"
