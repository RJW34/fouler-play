"""Tests for authoritative per-battle rating-transition parsing.

Regression guard for the concurrent-battle ELO-delta bug: the per-battle delta
used to be computed as (ladder_api_after - ladder_api_before), a shared lagging
aggregate that other concurrent battles moved between snapshots, collapsing the
reported delta to ~+/-1. The fix parses Showdown's authoritative end-of-battle
|raw| rating line instead, scoped to OUR account (Showdown sends one line per
player, so the opponent's line must not be picked up).
"""

import json

import pytest

import fp.run_battle as run_battle
from fp.run_battle import parse_rating_transition

OUR = "npctypebeat"


def test_parse_win_rating_transition_html_arrow():
    msg = (
        ">battle-gen9ou-2535182938\n"
        "|raw|npctypebeat's rating: 1234 &rarr; 1250<br />(+16 for winning)"
    )
    assert parse_rating_transition(msg, OUR) == (1234, 1250, 16)


def test_parse_loss_rating_transition_negative_delta():
    msg = (
        ">battle-gen9ou-2535182999\n"
        "|raw|npctypebeat's rating: 1300 &rarr; 1281<br />(-19 for losing)"
    )
    assert parse_rating_transition(msg, OUR) == (1300, 1281, -19)


def test_parse_live_wire_format_with_strong_tags_win():
    # The actual format Showdown sends on the wire wraps the new rating in
    # <strong>...</strong>. Captured live from production battle logs.
    msg = (
        ">battle-gen9ou-2622929860\n"
        "|raw|<username class=\"username\" name=\"npctypebeat\">npctypebeat</username>"
        "'s rating: 1105 &rarr; <strong>1133</strong><br />(+28 for winning)"
    )
    assert parse_rating_transition(msg, OUR) == (1105, 1133, 28)


def test_both_players_present_picks_our_loss_not_opponent_win():
    # THE regression case: Showdown emits BOTH players' rating lines in one
    # |raw| message. We lost; the opponent (timesetdia) gained +24. Picking the
    # first/opponent line would wrongly report +24 on our LOSS. Captured from
    # the live battle that exposed this bug (battle-gen9ou-2622943525).
    msg = (
        ">battle-gen9ou-2622943525\n"
        "|raw|<username class=\"username\" name=\"timesetdia\">timesetdia</username>"
        "'s rating: 1193 &rarr; <strong>1217</strong><br />(+24 for winning)<br />"
        "<username class=\"username\" name=\"npctypebeat\">npctypebeat</username>"
        "'s rating: 1221 &rarr; <strong>1197</strong><br />(-24 for losing)"
    )
    assert parse_rating_transition(msg, OUR) == (1221, 1197, -24)
    # And from the opponent's perspective the parser would return their gain.
    assert parse_rating_transition(msg, "timesetdia") == (1193, 1217, 24)


def test_unknown_account_returns_none_not_opponent_delta():
    # If our account isn't named, refuse to report someone else's delta.
    msg = "|raw|timesetdia's rating: 1193 &rarr; <strong>1217</strong><br />(+24 for winning)"
    assert parse_rating_transition(msg, OUR) is None


def test_parse_unicode_arrow():
    msg = "|raw|npctypebeat's rating: 1000 \u2192 1021<br />(+21 for winning)"
    assert parse_rating_transition(msg, OUR) == (1000, 1021, 21)


def test_parse_ascii_arrow():
    msg = "|raw|npctypebeat's rating: 1500 -> 1492"
    assert parse_rating_transition(msg, OUR) == (1500, 1492, -8)


def test_legacy_no_username_returns_first():
    # With no username filter, fall back to the first transition (legacy behaviour).
    msg = "|raw|whoever's rating: 1500 &rarr; 1492"
    assert parse_rating_transition(msg) == (1500, 1492, -8)


def test_no_rating_line_returns_none():
    assert parse_rating_transition(">battle-gen9ou-1|win|npctypebeat", OUR) is None
    assert parse_rating_transition("|turn|5", OUR) is None
    assert parse_rating_transition("", OUR) is None
    assert parse_rating_transition(None, OUR) is None


def test_extract_opponent_uses_showdown_account_aliases(monkeypatch):
    monkeypatch.setattr(run_battle.FoulPlayConfig, "username", "LEBOTJAMESXD004", raising=False)
    monkeypatch.setenv("SHOWDOWN_ACCOUNTS", "LEBOTJAMESXD00N")
    msg = (
        ">battle-gen9ou-2632180642\n"
        "|title|LEBOTJAMESXD00N vs. murdockfejao\n"
        "|player|p1|LEBOTJAMESXD00N|avatar|1000\n"
        "|player|p2|murdockfejao|avatar|1000"
    )

    assert run_battle._extract_opponent_from_message(msg) == "murdockfejao"


def test_extract_opponent_uses_websocket_username_when_config_is_stale(monkeypatch):
    monkeypatch.setattr(run_battle.FoulPlayConfig, "username", "LEBOTJAMESXD004", raising=False)
    monkeypatch.delenv("SHOWDOWN_ACCOUNTS", raising=False)
    msg = (
        ">battle-gen9ou-2632180642\n"
        "|title|murdockfejao vs. LEBOTJAMESXD00N\n"
        "|player|p1|murdockfejao|avatar|1000\n"
        "|player|p2|LEBOTJAMESXD00N|avatar|1000"
    )

    assert (
        run_battle._extract_opponent_from_message(msg, "LEBOTJAMESXD00N")
        == "murdockfejao"
    )


def test_delta_in_plausible_per_battle_range_not_one():
    # The whole point of the fix: a real win is +8..+30, never the +/-1 the
    # lagging ladder-API produced under concurrency.
    msg = "|raw|npctypebeat's rating: 1187 &rarr; <strong>1205</strong><br />(+18 for winning)"
    old, new, delta = parse_rating_transition(msg, OUR)
    assert delta == 18
    assert abs(delta) > 1


@pytest.mark.asyncio
async def test_battle_stats_enrichment_records_authoritative_rating(tmp_path):
    run_battle._battle_stats_authoritative_facts.clear()
    stats_path = tmp_path / "battle_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-2632356554",
                        "result": "win",
                        "rating": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    enriched = await run_battle._enrich_battle_stats_rating_once(
        "battle-gen9ou-2632356554",
        elo_before=1128,
        elo_after=1156,
        rating_delta=28,
        result_key="win",
        winner="LEBOTJAMESXD00N",
        opponent_name="murdockfejao",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2632356554",
        path=stats_path,
    )

    saved = json.loads(stats_path.read_text(encoding="utf-8"))
    entry = saved["battles"][0]
    assert enriched is True
    assert entry["battle_tag"] == "battle-gen9ou-2632356554"
    assert entry["result"] == "win"
    assert entry["winner"] == "LEBOTJAMESXD00N"
    assert entry["opponent"] == "murdockfejao"
    assert entry["replay_url"] == "https://replay.pokemonshowdown.com/gen9ou-2632356554"
    assert entry["rating"] == 1156.0
    assert entry["elo_before"] == 1128.0
    assert entry["elo_after"] == 1156.0
    assert entry["rating_delta"] == 28
    assert entry["rating_source"] == "showdown_raw"


@pytest.mark.asyncio
async def test_battle_stats_enrichment_marks_private_replay_pending(tmp_path):
    run_battle._battle_stats_authoritative_facts.clear()
    stats_path = tmp_path / "battle_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-2632356554-privatehash",
                        "result": "loss",
                        "rating": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    enriched = await run_battle._enrich_battle_stats_rating_once(
        "battle-gen9ou-2632356554-privatehash",
        elo_before=1128,
        elo_after=1156,
        rating_delta=28,
        result_key="win",
        winner="LEBOTJAMESXD00N",
        opponent_name="murdockfejao",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-2632356554-privatehash",
        path=stats_path,
    )

    saved = json.loads(stats_path.read_text(encoding="utf-8"))
    entry = saved["battles"][0]
    assert enriched is True
    # Unverified stays unverified -- the row must not claim "public" without a
    # probe. But the ADDRESS is recorded, with the room suffix intact, because
    # that is what lets the later reconciliation pass find the replay. This test
    # previously required the address to be withheld and the id truncated to
    # "gen9ou-2632356554"; measured against live Showdown that truncated id
    # returns HTTP 404 while the suffixed one returns 200, so withholding it
    # stranded the row with no way back to a replay that existed all along.
    assert entry["replay_status"] == "pending-public-upload"
    assert entry["public_replay_id"] == "gen9ou-2632356554-privatehash"
    assert entry["replay_url"] == (
        "https://replay.pokemonshowdown.com/gen9ou-2632356554-privatehash"
    )


@pytest.mark.asyncio
async def test_battle_stats_enrichment_reapplies_previous_authoritative_facts(tmp_path):
    run_battle._battle_stats_authoritative_facts.clear()
    stats_path = tmp_path / "battle_stats.json"
    stats_path.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-a",
                        "result": "loss",
                        "rating": None,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert await run_battle._enrich_battle_stats_rating_once(
        "battle-gen9ou-a",
        elo_before=1000,
        elo_after=1030,
        rating_delta=30,
        result_key="win",
        winner="LEBOTJAMESXD00N",
        opponent_name="first-opponent",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-a",
        path=stats_path,
    )

    # Simulate run.py saving its stale in-memory BattleStats list on the next
    # battle append, wiping the previous async enrichment fields from disk.
    stats_path.write_text(
        json.dumps(
            {
                "battles": [
                    {
                        "battle_id": "battle-gen9ou-a",
                        "result": "loss",
                        "rating": None,
                    },
                    {
                        "battle_id": "battle-gen9ou-b",
                        "result": "loss",
                        "rating": None,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert await run_battle._enrich_battle_stats_rating_once(
        "battle-gen9ou-b",
        elo_before=1030,
        elo_after=1048,
        rating_delta=18,
        result_key="win",
        winner="LEBOTJAMESXD00N",
        opponent_name="second-opponent",
        replay_url="https://replay.pokemonshowdown.com/gen9ou-b",
        path=stats_path,
    )

    saved = json.loads(stats_path.read_text(encoding="utf-8"))
    first, second = saved["battles"]
    assert first["result"] == "win"
    assert first["winner"] == "LEBOTJAMESXD00N"
    assert first["opponent"] == "first-opponent"
    assert first["replay_url"] == "https://replay.pokemonshowdown.com/gen9ou-a"
    assert first["rating"] == 1030.0
    assert first["rating_delta"] == 30
    assert second["result"] == "win"
    assert second["winner"] == "LEBOTJAMESXD00N"
    assert second["opponent"] == "second-opponent"
    assert second["replay_url"] == "https://replay.pokemonshowdown.com/gen9ou-b"
    assert second["rating"] == 1048.0
    assert second["rating_delta"] == 18


@pytest.mark.asyncio
async def test_discord_result_uses_non_opponent_winner_when_account_alias_is_stale(monkeypatch):
    sent_payloads: list[dict] = []
    fetched_users: list[str] = []

    class FakeResponse:
        status = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json, timeout):
            sent_payloads.append(json)
            return FakeResponse()

    async def replay_missing(*args, **kwargs):
        return False

    async def fake_fetch_elo(username, fmt="gen9ou"):
        fetched_users.append(username)
        return (1043, None)

    monkeypatch.setattr(run_battle.FoulPlayConfig, "username", "LEBOTJAMESXD004", raising=False)
    monkeypatch.setenv("SHOWDOWN_ACCOUNTS", "LEBOTJAMESXD004")
    monkeypatch.setenv("DISCORD_BATTLES_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token")
    monkeypatch.setattr(run_battle, "_fetch_elo", fake_fetch_elo)
    monkeypatch.setattr(run_battle, "_replay_exists", replay_missing)
    monkeypatch.setattr(run_battle.aiohttp, "ClientSession", lambda: FakeSession())

    elo_after = await run_battle._post_battle_to_discord(
        battle_tag="battle-gen9ou-2632180642",
        winner="LEBOTJAMESXD00N",
        opponent_name="murdockfejao",
        our_player_name="LEBOTJAMESXD004",
        elo_before=1000,
        turn_count=21,
    )

    assert fetched_users == ["LEBOTJAMESXD00N"]
    assert elo_after == 1043
    assert sent_payloads == []


@pytest.mark.asyncio
async def test_discord_result_preserves_terminal_loss_when_rating_gain_contradicts(monkeypatch):
    sent_payloads: list[dict] = []

    class FakeResponse:
        status = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json, timeout):
            sent_payloads.append(json)
            return FakeResponse()

    async def replay_missing(*args, **kwargs):
        return False

    monkeypatch.setattr(run_battle.FoulPlayConfig, "username", "currentbot", raising=False)
    monkeypatch.setenv("SHOWDOWN_ACCOUNTS", "npctypebeat")
    monkeypatch.setenv("DISCORD_BATTLES_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token")
    monkeypatch.setattr(run_battle, "_replay_exists", replay_missing)
    monkeypatch.setattr(run_battle.aiohttp, "ClientSession", lambda: FakeSession())

    elo_after = await run_battle._post_battle_to_discord(
        battle_tag="battle-gen9ou-2632180642",
        winner="murdockfejao",
        opponent_name="murdockfejao",
        our_player_name="currentbot",
        turn_count=21,
        rating_delta=(1000, 1043, 43),
    )

    assert elo_after == 1043
    assert sent_payloads == []


@pytest.mark.asyncio
async def test_discord_result_preserves_terminal_win_when_rating_drop_contradicts(monkeypatch):
    sent_payloads: list[dict] = []

    class FakeResponse:
        status = 204

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def post(self, url, json, timeout):
            sent_payloads.append(json)
            return FakeResponse()

    async def replay_missing(*args, **kwargs):
        return False

    monkeypatch.setattr(run_battle.FoulPlayConfig, "username", "currentbot", raising=False)
    monkeypatch.setenv("DISCORD_BATTLES_WEBHOOK_URL", "https://discord.com/api/webhooks/test/token")
    monkeypatch.setattr(run_battle, "_replay_exists", replay_missing)
    monkeypatch.setattr(run_battle.aiohttp, "ClientSession", lambda: FakeSession())

    elo_after = await run_battle._post_battle_to_discord(
        battle_tag="battle-gen9ou-2632180947",
        winner="currentbot",
        opponent_name="slyddvicious",
        our_player_name="currentbot",
        turn_count=21,
        rating_delta=(1084, 1056, -28),
    )

    assert elo_after == 1056
    assert sent_payloads == []


def test_battle_result_does_not_relabel_explicit_winner_from_rating_delta():
    assert (
        run_battle._battle_result_from_evidence(
            "murdockfejao",
            "LEBOTJAMESXD00N",
            opponent_name="murdockfejao",
            elo_delta=43,
        )
        == "loss"
    )
    assert (
        run_battle._battle_result_from_evidence(
            "LEBOTJAMESXD00N",
            "LEBOTJAMESXD00N",
            opponent_name="murdockfejao",
            elo_delta=-28,
        )
        == "win"
    )
