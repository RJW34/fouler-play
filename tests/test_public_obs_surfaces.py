from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
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
    "/dashboard/hybrid",
    "/overlay/hybrid",
    "WORKER 01",
    "WORKER 02",
    "DEKU plays",
    "DEKU SIMULCAST",
)


def test_public_scene_collection_excludes_operator_dashboard() -> None:
    collection = json.loads(PUBLIC_SCENE_COLLECTION.read_text(encoding="utf-8"))
    serialized = json.dumps(collection, sort_keys=True)

    for forbidden in FORBIDDEN_PUBLIC_STRINGS[:3]:
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
    served_html = serve_obs_page.BATTLE_SLOT_HTML.format(slot=1)

    assert "width:100vw;height:100vh" in html.replace(" ", "")
    assert "width:640px;height:540px" not in html.replace(" ", "")
    assert "width:100vw;height:100vh" in served_html.replace(" ", "")
    assert "width:640px;height:540px" not in served_html.replace(" ", "")


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
    assert "MATCHMAKING" in html
    assert "RANKED BATTLE FEED 2" in html
    assert "NEXT MATCH LOADING" not in html
    assert "BATTLE QUEUE" not in html
    assert "FEATURED MATCH" not in html
    assert "IDLE" not in html
    assert "window.location.replace(url)" in html


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
    assert "window.location.replace(url)" in html


def test_hybrid_scene_builder_prunes_operator_dashboard_sources() -> None:
    collection = {
        "name": "source",
        "sources": [
            {
                "name": "Battle Scene",
                "id": "scene",
                "settings": {
                    "items": [
                        {"name": "Debug Overlay", "visible": True},
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
    assert all(item["name"] != "Debug Overlay" for item in scene_items)
    assert "/dashboard/hybrid" not in serialized
    assert "/overlay/hybrid" not in serialized
    assert out["sources"][1]["settings"]["url"] == "http://localhost:8777/overlay?mode=bottom&hide_recent=1"
    assert out["sources"][2]["settings"]["url"] == "http://localhost:8777/slot/1?slot_idle=public"
    for source in out["sources"][2:5]:
        settings = source["settings"]
        assert settings["width"] == BATTLE_SLOT_WIDTH
        assert settings["height"] == BATTLE_SLOT_HEIGHT
        assert settings["css"] == PUBLIC_BATTLE_BROWSER_CSS
        assert settings["restart_when_active"] is False

    slot_item = out["sources"][0]["settings"]["items"][1]
    assert slot_item["bounds"] == {"x": float(BATTLE_SLOT_WIDTH), "y": float(BATTLE_SLOT_HEIGHT)}
    assert slot_item["bounds_crop"] is False
    assert slot_item["crop_left"] == 0
