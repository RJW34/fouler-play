"""The /idle Browser Source card must be an informative Battle Lab surface.

2026-07-21 holistic audit, OBS gap register: "Degraded Fouler state is
represented by decorative radar art and `SCANNING...`, not an intentional,
informative Battle Lab surface." These tests pin the replacement contract: the
idle card must present state, last-known ladder standing, DEKU activity, and an
honest client-clock freshness indicator — all sourced from this server's own
/state endpoint.
"""

from pathlib import Path

STREAMING = Path(__file__).resolve().parent.parent / "streaming"
IDLE = (STREAMING / "obs_idle.html").read_text(encoding="utf-8")


def test_idle_card_reads_state_from_same_origin():
    assert 'fetch("/state"' in IDLE


def test_idle_card_is_not_decoration_only():
    # The old card was a sonar animation plus the bare word SCANNING and
    # nothing else. The replacement must carry real state surfaces.
    assert "SCANNING..." not in IDLE
    for element_id in (
        "state-chip",
        "why",
        "stat-elo",
        "stat-record",
        "stat-active",
        "deku-line",
        "freshness-text",
    ):
        assert f'id="{element_id}"' in IDLE, f"missing informative element #{element_id}"


def test_idle_card_explains_degraded_states():
    # An honest degraded surface distinguishes pause, season completion,
    # matchmaking, and searching — not one generic animation for all of them.
    assert "Paused" in IDLE
    assert "Season checkpoint" in IDLE
    assert "Matchmaking" in IDLE
    assert "Searching the Pok" in IDLE


def test_idle_card_freshness_uses_client_clock():
    # The producer's "updated" field is host-local time without a timezone;
    # freshness must come from the client-side fetch clock instead.
    assert "Date.now()" in IDLE
    assert "STALE_AFTER_MS" in IDLE
    assert "reconnecting" in IDLE.lower()


def test_idle_card_never_reveals_the_elo_target():
    # Owner directive 2026-07-22: the ladder ELO may be shown, but the ELO
    # TARGET must not appear on any audience-facing surface.
    assert "1700" not in IDLE
    assert "target" not in IDLE.lower()


def test_idle_card_never_embeds_secrets_or_operator_paths():
    lowered = IDLE.lower()
    for banned in ("password", "webhook", "token", "c:\\programdata", "ssh"):
        assert banned not in lowered
