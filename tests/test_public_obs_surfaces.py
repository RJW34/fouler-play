from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import make_mocked_request

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from scripts.build_obs_hybrid_scene_collection import BATTLE_SLOT_HEIGHT, BATTLE_SLOT_WIDTH, PUBLIC_BATTLE_BROWSER_CSS, build_collection
from streaming import serve_obs_page


PUBLIC_SCENE_COLLECTION = ROOT_DIR / "streaming" / "fouler_play_hybrid_scenes.json"
PUBLIC_OVERLAY_HTML = ROOT_DIR / "streaming" / "overlay.html"
PUBLIC_BATTLE_INJECT_CSS = ROOT_DIR / "streaming" / "battle_inject.css"
PUBLIC_BATTLE_SLOT_HTML = ROOT_DIR / "streaming" / "battle_slot.html"
PUBLIC_DEVSTREAM_CONTRACT = ROOT_DIR / "devstream.yaml"

FORBIDDEN_PUBLIC_STRINGS = (
    "Debug Overlay",
    "Starting Soon",
    "Be Right Back",
    "BRB Text",
    "Ending Text",
    "Window Capture",
    "capture_audio",
    "/dashboard/hybrid",
    "/overlay/hybrid",
    "WORKER 01",
    "WORKER 02",
    "DEKU plays",
    "DEKU SIMULCAST",
)


def test_windows_service_leaves_signal_ownership_to_nssm(monkeypatch) -> None:
    monkeypatch.setattr(serve_obs_page, "LIFECYCLE_OWNER", "windows-service")
    assert serve_obs_page._use_process_signal_handlers() is False

    monkeypatch.setattr(serve_obs_page, "LIFECYCLE_OWNER", "")
    assert serve_obs_page._use_process_signal_handlers() is True


@pytest.mark.asyncio
async def test_small_html_pages_bypass_windows_native_sendfile(tmp_path, monkeypatch) -> None:
    page = tmp_path / "test.html"
    page.write_text("<html><body>OBS surface</body></html>", encoding="utf-8")
    monkeypatch.setattr(serve_obs_page, "STREAMING_DIR", tmp_path)

    response = await serve_obs_page._html_file_response(page.name)

    assert isinstance(response, web.Response)
    assert not isinstance(response, web.FileResponse)
    assert response.text == "<html><body>OBS surface</body></html>"


def test_public_scene_collection_excludes_operator_dashboard() -> None:
    collection = json.loads(PUBLIC_SCENE_COLLECTION.read_text(encoding="utf-8"))
    serialized = json.dumps(collection, sort_keys=True)

    for forbidden in FORBIDDEN_PUBLIC_STRINGS[:9]:
        assert forbidden not in serialized


def test_project_public_obs_contract_uses_battle_lab_surfaces() -> None:
    contract = PUBLIC_DEVSTREAM_CONTRACT.read_text(encoding="utf-8")
    parsed = json.loads(contract)

    assert "/overlay?mode=bottom&hide_recent=1" in contract
    assert "/overlay/hybrid" not in contract
    assert "Dashboard Focus" not in contract
    assert "http://192.168.1.126:8777/slot/1?slot_idle=public" in contract
    assert "fallbackUrls" in contract
    health_surface = next(surface for surface in parsed["obs"]["surfaces"] if surface["name"] == "health")
    assert health_surface["url"] == "http://192.168.1.126:8777/health"
    assert health_surface["fallbackUrls"] == ["http://jigglypuff.tail4859dd.ts.net:8777/health"]
    assert health_surface["operatorOnly"] is True
    assert health_surface["captureRequired"] is False


def test_public_overlay_copy_is_viewer_facing() -> None:
    html = PUBLIC_OVERLAY_HTML.read_text(encoding="utf-8")

    for forbidden in FORBIDDEN_PUBLIC_STRINGS[3:]:
        assert forbidden not in html

    assert "FEATURED MATCH 1" in html
    assert "FEATURED MATCH 2" in html
    assert "SHOWDOWN BATTLE LAB" in html
    assert "Next match ready" in html
    assert "Next matchup ready" in html
    assert "BATTLE SLOT" not in html
    assert "Match lane ready" not in html
    assert "Awaiting battle" not in html
    assert "waiting for the next slot assignment" not in html


def test_showdown_browser_css_hides_privacy_chrome_without_crop() -> None:
    css = PUBLIC_BATTLE_INJECT_CSS.read_text(encoding="utf-8")

    assert "#onetrust-banner-sdk" in css
    assert ".fc-ccpa-root" in css
    assert "display: none !important" in css


def test_public_battle_slot_is_viewport_responsive_for_obs_browser_sources() -> None:
    html = PUBLIC_BATTLE_SLOT_HTML.read_text(encoding="utf-8")
    served_html = serve_obs_page.BATTLE_SLOT_HTML.replace("__SLOT__", "1")

    assert html == str(serve_obs_page.BATTLE_SLOT_HTML)
    assert "width:100vw;height:100vh" in html.replace(" ", "")
    assert "width:1280px;height:720px" in html.replace(" ", "")
    assert "width:640px;height:540px" not in html.replace(" ", "")
    assert "width:100vw;height:100vh" in served_html.replace(" ", "")
    assert "width:640px;height:540px" not in served_html.replace(" ", "")
    assert "Waiting for battle" not in html
    assert "SLOT ?" not in html
    assert "<title>Battle Slot" not in served_html
    assert "window.location.replace" not in served_html
    assert "play.pokemonshowdown.com" not in served_html


def test_showdown_browser_css_keeps_battle_panel_uncropped() -> None:
    css = PUBLIC_BATTLE_INJECT_CSS.read_text(encoding="utf-8")

    assert "overflow: visible !important" in css
    assert "min-width: 640px !important" in css
    assert "min-height: 360px !important" in css
    assert "max-width: none !important" in css
    assert "transform: scale(2) !important" in css
    assert "transform-origin: top left !important" in css
    assert ".innerbattle" in css
    assert ".backdrop" in css


@pytest.mark.asyncio
async def test_public_slot_source_uses_local_viewer_overlay(monkeypatch) -> None:
    monkeypatch.setattr(serve_obs_page, "build_state_payload", lambda: {"battles": []})
    request = make_mocked_request(
        "GET",
        "/slot/2?slot_idle=public",
        match_info={"slot": "2"},
    )

    response = await serve_obs_page.handle_battle_slot(request)
    html = response.text

    assert response.status == 200
    assert "var SLOT=2;" in html
    assert "Pokemon Showdown / Ranked match 2" in html
    assert "Battle Lab" in html
    assert "Battle timeline" in html
    assert "Session" in html
    assert "GEN 9 OU" in html
    assert "Format" in html
    assert "Battle time" in html
    assert "NEXT MATCH LOADING" not in html
    assert "BATTLE QUEUE" not in html
    assert "FEATURED MATCH" not in html
    assert "IDLE" not in html
    assert "window.location.replace" not in html
    assert "play.pokemonshowdown.com" not in html


@pytest.mark.asyncio
async def test_public_slot_source_loads_battle_surface_when_active(monkeypatch) -> None:
    monkeypatch.setattr(
        serve_obs_page,
        "build_state_payload",
        lambda: {"battles": [{"id": "battle-gen9ou-active", "slot": 2}]},
    )
    request = make_mocked_request(
        "GET",
        "/slot/2?slot_idle=public",
        match_info={"slot": "2"},
    )

    response = await serve_obs_page.handle_battle_slot(request)
    html = response.text

    assert response.status == 200
    assert "var SLOT=2;" in html
    assert "var STATE_URL='/slot/'+SLOT+'/state';" in html
    assert "window.location.replace" not in html


@pytest.mark.asyncio
async def test_slot_state_exposes_obs_safe_battle_lab_payload(monkeypatch) -> None:
    battle = {
        "id": "battle-gen9ou-active",
        "slot": 2,
        "opponent": "Test Opponent",
        "started": "2026-06-22T00:00:00",
        "players": ["LEBOTJAMESXD00N", "Test Opponent"],
    }
    monkeypatch.setattr(
        serve_obs_page,
        "build_state_payload",
        lambda: {
            "battles": [battle],
            "status": {"status": "Active", "today_wins": 3, "today_losses": 2, "accounts_elo": {"LEBOTJAMESXD00N": 1234}},
            "accounts_elo": {"LEBOTJAMESXD00N": 1234},
            "updated": "2026-06-22T00:01:00",
        },
    )
    monkeypatch.setattr(
        serve_obs_page,
        "_recent_battle_events",
        lambda active_battle: (["Turn 4", "Bot selected Move Recover"], 4, "battle-gen9ou-active.log"),
    )
    monkeypatch.setattr(serve_obs_page, "_recent_battle_results", lambda: [])
    request = make_mocked_request("GET", "/slot/2/state", match_info={"slot": "2"})

    response = await serve_obs_page.handle_slot_state(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["battle_id"] == "battle-gen9ou-active"
    assert payload["battle_lab"]["active"] is True
    assert payload["battle_lab"]["opponent"] == "Test Opponent"
    assert payload["battle_lab"]["turn"] == 4
    assert payload["battle_lab"]["elo"] == 1234
    assert payload["battle_lab"]["events"] == ["Turn 4", "Bot selected Move Recover"]


def test_hybrid_scene_builder_prunes_operator_dashboard_sources() -> None:
    collection = {
        "name": "source",
        "current_scene": "Starting Soon",
        "current_program_scene": "Starting Soon",
        "scene_order": [
            {"name": "Triple Battles"},
            {"name": "Starting Soon"},
            {"name": "Be Right Back"},
            {"name": "Ending"},
        ],
        "sources": [
            {
                "name": "Battle Scene",
                "id": "scene",
                "settings": {
                    "items": [
                        {"name": "Debug Overlay", "visible": True},
                        {"name": "Window Capture", "visible": True},
                        {"name": "Stats Overlay", "visible": True},
                        {
                            "name": "Battle Slot 1",
                            "visible": True,
                            "bounds": {"x": 853.0, "y": 1440.0},
                            "bounds_crop": True,
                            "crop_left": 12,
                        },
                    ]
                },
            },
            {
                "name": "Debug Overlay",
                "id": "browser_source",
                "settings": {"url": "http://localhost:8777/dashboard/hybrid"},
            },
            {
                "name": "Starting Soon",
                "id": "scene",
                "settings": {"items": [{"name": "Starting Soon Text", "visible": True}]},
            },
            {
                "name": "Window Capture",
                "id": "window_capture",
                "settings": {"capture_audio": True, "window": "YouTube - Google Chrome"},
            },
            {
                "name": "Stats Overlay",
                "id": "browser_source",
                "settings": {"url": "http://localhost:8777/old", "width": 1280, "height": 720},
            },
            {
                "name": "Battle Slot 1",
                "id": "browser_source",
                "settings": {"url": "http://localhost:8777/old", "width": 853, "height": 1440, "css": ""},
            },
            {
                "name": "Battle Slot 2",
                "id": "browser_source",
                "settings": {"url": "http://localhost:8777/old", "width": 853, "height": 1440, "css": ""},
            },
            {
                "name": "Battle Slot 3",
                "id": "browser_source",
                "settings": {"url": "http://localhost:8777/old", "width": 854, "height": 1440, "css": ""},
            },
        ],
    }

    out = build_collection(collection, "public", 8777)
    source_names = {source["name"] for source in out["sources"]}
    scene_items = out["sources"][0]["settings"]["items"]
    serialized = json.dumps(out, sort_keys=True)

    assert "Debug Overlay" not in source_names
    assert "Starting Soon" not in source_names
    assert "Window Capture" not in source_names
    assert out["scene_order"] == [{"name": "Triple Battles"}]
    assert out["current_scene"] == "Triple Battles"
    assert out["current_program_scene"] == "Triple Battles"
    assert all(item["name"] != "Debug Overlay" for item in scene_items)
    assert all(item["name"] != "Window Capture" for item in scene_items)
    assert "/dashboard/hybrid" not in serialized
    assert "/overlay/hybrid" not in serialized
    assert "capture_audio" not in serialized
    stats_source = next(source for source in out["sources"] if source["name"] == "Stats Overlay")
    assert stats_source["settings"]["url"] == "http://localhost:8777/overlay?mode=bottom&hide_recent=1"
    slot_sources = [source for source in out["sources"] if source["name"].startswith("Battle Slot")]
    assert slot_sources[0]["settings"]["url"] == "http://localhost:8777/slot/1?slot_idle=public"
    for source in slot_sources:
        settings = source["settings"]
        assert settings["width"] == BATTLE_SLOT_WIDTH
        assert settings["height"] == BATTLE_SLOT_HEIGHT
        assert settings["css"] == PUBLIC_BATTLE_BROWSER_CSS
        assert settings["restart_when_active"] is False

    slot_item = out["sources"][0]["settings"]["items"][1]
    assert slot_item["bounds"] == {"x": float(BATTLE_SLOT_WIDTH), "y": float(BATTLE_SLOT_HEIGHT)}
    assert slot_item["bounds_crop"] is False
    assert slot_item["crop_left"] == 0

