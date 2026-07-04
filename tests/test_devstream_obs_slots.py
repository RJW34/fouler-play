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
    assert "battle_id" in html
    assert "battle_lab" in html
    assert "window.location.replace" not in html
    assert "play.pokemonshowdown.com" not in html
    assert "/magneton-state" not in html


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
