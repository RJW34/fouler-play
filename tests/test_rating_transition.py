"""Tests for authoritative per-battle rating-transition parsing.

Regression guard for the concurrent-battle ELO-delta bug: the per-battle delta
used to be computed as (ladder_api_after - ladder_api_before), a shared lagging
aggregate that other concurrent battles moved between snapshots, collapsing the
reported delta to ~+/-1. The fix parses Showdown's authoritative end-of-battle
|raw| rating line instead, scoped to OUR account (Showdown sends one line per
player, so the opponent's line must not be picked up).
"""

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
    msg = "|raw|npctypebeat's rating: 1000 → 1021<br />(+21 for winning)"
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


def test_delta_in_plausible_per_battle_range_not_one():
    # The whole point of the fix: a real win is +8..+30, never the +/-1 the
    # lagging ladder-API produced under concurrency.
    msg = "|raw|npctypebeat's rating: 1187 &rarr; <strong>1205</strong><br />(+18 for winning)"
    old, new, delta = parse_rating_transition(msg, OUR)
    assert delta == 18
    assert abs(delta) > 1
