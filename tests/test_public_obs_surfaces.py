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
    assert "http://127.0.0.1:8777/slot/1?slot_idle=public" in contract
    health_surface = next(surface for surface in parsed["obs"]["surfaces"] if surface["name"] == "health")
    assert health_surface["url"] == "http://127.0.0.1:8777/health"
    assert health_surface["operatorOnly"] is True
    assert health_surface["captureRequired"] is False


def test_declared_surfaces_match_the_loopback_bind_they_are_served_from() -> None:
    """The overlay server binds 127.0.0.1 only; declared URLs must say so.

    Regression guard for the 2026-07-20 split between declared and actual: the
    manifest advertised http://192.168.1.126:8777/... for surfaces that are
    served on a loopback-only bind and reached through the
    HERMES-BroadcastSourceTunnels SSH forward. Every one of those URLs was
    unreachable by construction from every machine in the fleet, and .126 was a
    retired address on top of that. Correcting the octet would not have helped.
    """
    parsed = json.loads(PUBLIC_DEVSTREAM_CONTRACT.read_text(encoding="utf-8"))
    obs = parsed["obs"]
    surfaces = obs["surfaces"]
    assert surfaces

    for surface in surfaces:
        assert surface["url"].startswith("http://127.0.0.1:8777/"), (
            f"{surface['name']}: surfaces are served on a loopback-only bind and reached "
            "through the broadcast-node SSH tunnel; a routable host here cannot work"
        )
        # fallbackUrls were never read by any consumer and pointed at a tailnet
        # name for a node that is routinely offline. Do not reintroduce them as
        # if they bought resilience.
        assert "fallbackUrls" not in surface, f"{surface['name']}: dead fallback declaration"

    access = obs["surfaceAccess"]
    assert access["bind"] == "127.0.0.1:8777"
    assert access["bindIsLoopbackOnly"] is True
    assert access["kind"] == "ssh-tunnel"
    assert access["remotelyProbeable"] is False, (
        "the control plane carries no tunnel to 8777; probing these from DEKU "
        "manufactures a permanent false 'surface unavailable'"
    )


def test_contract_declares_no_retired_or_unroutable_hosts() -> None:
    """.126 is a retired address and the tailnet name is not a working fallback."""
    contract = PUBLIC_DEVSTREAM_CONTRACT.read_text(encoding="utf-8")
    assert "192.168.1.126" not in contract, "retired JIGGLYPUFF address"
    assert "tail4859dd.ts.net" not in contract, "tailnet fallback is not reachable in practice"


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
    assert 'class="battle-board"' in served_html
    assert "renderLiveBoard" in served_html
    assert "private strategy stays private" in served_html
    assert "live agent state" not in served_html.lower()


def test_obs_slot_stays_on_reactive_local_surface_during_active_battles() -> None:
    assert serve_obs_page._build_obs_slot_source_url(2) == (
        "http://localhost:8777/slot/2?slot_idle=public"
    )
    assert serve_obs_page._build_obs_slot_source_url(
        2, "battle-gen9ou-live"
    ) == "http://localhost:8777/slot/2?slot_idle=public"
    assert "OBS_STALE_BATTLE_SEC" not in serve_obs_page.__dict__


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
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Expires"] == "0"
    assert "var SLOT=2;" in html
    assert "Pokemon Showdown / Ranked match 2" in html
    assert "Battle Lab" in html
    assert "Battle timeline" in html
    assert "Recent form" in html
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
        lambda active_battle: (["Turn 4", "Blissey used Seismic Toss into Great Tusk"], 4, "battle-gen9ou-active.log"),
    )
    monkeypatch.setattr(serve_obs_page, "_recent_battle_results", lambda: [])
    request = make_mocked_request("GET", "/slot/2/state", match_info={"slot": "2"})

    response = await serve_obs_page.handle_slot_state(request)
    payload = json.loads(response.text)

    assert response.status == 200
    assert payload["url"] == "http://localhost:8777/slot/2?slot_idle=public"
    assert payload["battle_lab"]["active"] is True
    assert payload["battle_lab"]["opponent"] == "Test Opponent"
    assert payload["battle_lab"]["turn"] == 4
    assert payload["battle_lab"]["elo"] == 1234
    assert payload["battle_lab"]["events"] == [
        "Turn 4",
        "Blissey used Seismic Toss into Great Tusk",
    ]
    rendered = json.dumps(payload)
    assert "battle-gen9ou-active" not in rendered
    assert "LEBOTJAMESXD00N" not in rendered
    assert "log_name" not in payload["battle_lab"]
    assert "players" not in payload["battle_lab"]


def test_public_battle_log_events_exclude_private_decisions_and_inferences() -> None:
    private_lines = (
        "INFO |/choose move recover|",
        "INFO [STRATEGIC] Archetype=STALL, Confidence=0.9",
        "INFO Win Condition: preserve Blissey and exhaust Great Tusk",
        "INFO Great Tusk already has the move Knock Off. Decrementing the PP by 1",
        "INFO Great Tusk used a status move. Adding Choice Band to impossible items",
    )

    assert all(serve_obs_page._clean_battle_log_line(line) is None for line in private_lines)
    assert serve_obs_page._clean_battle_log_line("|turn|4") == "Turn 4"
    assert serve_obs_page._clean_battle_log_line(
        "|move|p1a: Blissey|Seismic Toss|p2a: Great Tusk"
    ) == "Blissey used Seismic Toss into Great Tusk"


def test_public_battle_view_falls_back_to_latest_matching_decision_trace(tmp_path, monkeypatch) -> None:
    battle_id = "battle-gen9ou-123-private-room-token"
    trace_dir = tmp_path / "decision_traces"
    trace_dir.mkdir()
    trace = {
        "battle_tag": battle_id,
        "turn": 12,
        "timestamp": "2026-07-13T18:00:00Z",
        "format": "gen9ou",
        "choice": "move recover",
        "snapshot": {
            "weather": "raindance",
            "user": {
                "account": "DekuFoulerLab",
                "active": {
                    "name": "mrmime",
                    "hp": 120,
                    "max_hp": 240,
                    "types": ["psychic", "fairy"],
                    "moves": ["recover"],
                    "item": "leftovers",
                    "ability": "filter",
                    "tera": {"active": False, "type": "steel"},
                    "boosts": {"spa": 1},
                },
                "reserve": [],
                "side_conditions": {"reflect": 3},
            },
            "opponent": {
                "account": "Opponent",
                "active": {
                    "name": "weezinggalar",
                    "hp": 80,
                    "max_hp": 200,
                    "types": ["poison", "fairy"],
                    "tera": {"active": True, "type": "dark"},
                    "boosts": {},
                },
                "reserve": [],
                "side_conditions": {"stealthrock": 1},
            },
        },
    }
    (trace_dir / f"{battle_id}_turn12_1.json").write_text(
        json.dumps(trace), encoding="utf-8"
    )
    pokedex = tmp_path / "pokedex.json"
    pokedex.write_text(
        json.dumps(
            {
                "mrmime": {"name": "mr. mime"},
                "weezinggalar": {"name": "weezing-galar"},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        serve_obs_page, "PUBLIC_BATTLE_VIEW_PATH", trace_dir / "latest-public-battle.json"
    )
    monkeypatch.setattr(serve_obs_page, "POKEDEX_PATH", pokedex)
    monkeypatch.setattr(serve_obs_page, "_public_pokedex", None)

    public = serve_obs_page._public_battle_view({"id": battle_id})

    assert public is not None
    assert public["turn"] == 12
    assert public["match_ref"] == "Ranked ladder battle"
    assert "battle_id" not in public
    assert public["user"]["active"]["display_name"] == "Mr. Mime"
    assert public["user"]["active"]["hp_percent"] == 50.0
    assert "max_hp" not in public["user"]["active"]
    assert public["user"]["active"]["tera"]["type"] is None
    assert public["user"]["active"]["sprite_url"].endswith("/ani-back/mrmime.gif")
    assert public["user"]["active"]["sprite_urls"][1].endswith(
        "/gen5-back/mrmime.png"
    )
    assert public["opponent"]["active"]["display_name"] == "Weezing Galar"
    assert public["opponent"]["active"]["sprite_url"].endswith(
        "/ani/weezing-galar.gif"
    )
    assert public["opponent"]["active"]["sprite_urls"][1].endswith(
        "/gen5/weezing-galar.png"
    )
    assert public["opponent"]["active"]["tera"]["type"] == "dark"
    serialized = json.dumps(public)
    assert "private-room-token" not in serialized
    assert "leftovers" not in serialized
    assert "recover" not in serialized
    assert "filter" not in serialized


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

