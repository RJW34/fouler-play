from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT_DIR))
sys.path.insert(0, str(ROOT_DIR / "scripts"))

from scripts import devstream_session
from scripts import devstream_health
from streaming import serve_obs_page


def test_battle_slot_polls_local_slot_state() -> None:
    html = serve_obs_page.BATTLE_SLOT_HTML.format(slot=2)

    assert "var STATE_URL='/slot/'+SLOT+'/state';" in html
    assert "battle_lab" in html
    assert "lab.battle_id" not in html
    assert "window.location.replace" not in html
    assert "play.pokemonshowdown.com" not in html
    assert "/magneton-state" not in html


def test_battle_slot_translates_runtime_details_for_viewers_without_dom_flash() -> None:
    html = serve_obs_page.BATTLE_SLOT_HTML.format(slot=2)
    lowered = html.lower()

    for forbidden in (
        "mission proof",
        "parsed battle events",
        "proof slot open",
        "archetypeenum.",
        "fat-team-",
    ):
        assert forbidden not in lowered
    assert "PUBLIC" not in html

    assert "Pokemon Showdown / Ranked match 2" in html
    assert "Battle timeline" in html
    assert "function normalizeEvent(event)" in html
    assert "Battle read: " not in html
    assert "Decision: " not in html
    assert "one use tracked" not in html
    assert "is ruled out" not in html
    assert '<strong>GEN 9 OU</strong><span>Format</span>' in html
    assert '<strong id="record">0-0</strong><span>Season</span>' in html
    assert "text('elo'" not in html
    assert "Replay ready" in html
    assert "Replay processing" in html
    assert "function formatLabel(value)" in html
    assert "gen9ou:'GEN 9 OU'" in html
    assert "prefix==='user'?'DEKU':'Opponent'" in html
    assert ".bench-fallback[hidden]" in html
    assert "function setupSpriteImage" in html
    assert "if(index<sources.length){img.src=sources[index];}" in html
    assert "function renderEvents" in html
    assert "function renderResults" in html
    assert "function renderEffects" in html
    assert "function renderBench" in html
    assert html.count("document.createDocumentFragment()") >= 4
    assert html.count("root.replaceChildren(fragment);") >= 4
    assert "root.innerHTML=''" not in html


def test_devstream_defaults_to_three_rated_showdown_battles() -> None:
    assert devstream_session.DEFAULT_MAX_CONCURRENT == 3

    command = devstream_session.shell_command_for_session(
        run_count=25,
        max_concurrent=devstream_session.DEFAULT_MAX_CONCURRENT,
        env={"PS_USERNAME": "example", "PS_PASSWORD": "unused"},
    )
    flag_index = command.index("--max-concurrent-battles")
    assert command[flag_index + 1] == "3"


def test_devstream_health_checks_all_default_battle_slot_states() -> None:
    assert "/slot/1/state" in devstream_health.ENDPOINTS
    assert "/slot/2/state" in devstream_health.ENDPOINTS
    assert "/slot/3/state" in devstream_health.ENDPOINTS
