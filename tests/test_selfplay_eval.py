"""Unit tests for the fouler self-play eval gate (the DISCRIMINATING gate's logic).

These prove the gate's decision math: it ACCEPTS a candidate iff fouler-NEW beats
fouler-OLD with a Wilson LCB > 0.50, and that ties/forfeits don't pollute the
denominator. This is the only gate that can rank engine variants.
"""
import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "selfplay_eval", ROOT / "infrastructure" / "selfplay_eval.py"
)
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)


# --- winner-log parsing -------------------------------------------------------

def test_parse_winners_extracts_tag_and_normalized_winner(tmp_path):
    log = tmp_path / "new.log"
    log.write_text(
        "some noise\n"
        "INFO Battle finished: battle-gen9ou-1 Winner: foulerNEW\n"
        "more noise\n"
        "INFO Battle finished: battle-gen9ou-2 Winner: fouler OLD\n"
        "INFO Battle finished: battle-gen9ou-3 Winner: \n"
        "INFO Battle finished: battle-gen9ou-4 Winner: tie\n",
        encoding="utf-8",
    )
    rows = sp.parse_winners(log)
    assert rows == [
        ("battle-gen9ou-1", "foulernew"),
        ("battle-gen9ou-2", "foulerold"),
        ("battle-gen9ou-3", ""),
        ("battle-gen9ou-4", ""),
    ]


def test_parse_winners_missing_file_is_empty(tmp_path):
    assert sp.parse_winners(tmp_path / "nope.log") == []


# --- tally --------------------------------------------------------------------

def test_tally_counts_new_old_and_excludes_ties():
    winners = [
        ("b1", sp._normalize("foulerNEW")),
        ("b2", sp._normalize("foulerNEW")),
        ("b3", sp._normalize("foulerOLD")),
        ("b4", ""),                      # tie -> excluded from decisive
    ]
    t = sp.tally("foulerNEW", "foulerOLD", winners)
    assert t["new_wins"] == 2
    assert t["old_wins"] == 1
    assert t["ties"] == 1
    assert t["decisive"] == 3          # ties excluded
    assert t["battles_finished"] == 4


def test_tally_dedupes_repeated_tag():
    # The same battle tag appearing twice must count once (last write wins).
    winners = [("b1", sp._normalize("foulerNEW")),
               ("b1", sp._normalize("foulerNEW"))]
    t = sp.tally("foulerNEW", "foulerOLD", winners)
    assert t["battles_finished"] == 1
    assert t["new_wins"] == 1


# --- the acceptance rule: ACCEPT iff Wilson LCB(new) > 0.50 -------------------

def test_verdict_rejects_coinflip():
    # 50/50 over a modest sample -> LCB below 0.5 -> REJECT (NEW not proven better)
    v = sp.verdict_from_counts(new_wins=25, decisive=50, label="t")
    assert v["new_win_rate"] == 0.5
    assert v["new_wilson_lcb"] < 0.5
    assert v["ACCEPT"] is False


def test_verdict_rejects_tiny_sample_even_if_swept():
    # NEW wins all 4 smoke games. NOTE: a 4/4 Wilson LCB is ~0.51, i.e. it DOES
    # squeak past LCB>0.5 -- which is exactly why a sample-size floor is required.
    # The min_decisive floor (default 30) rejects it: a 4-game smoke proves
    # RANKING but can never falsely PROMOTE a change. Only a real burst clears it.
    v = sp.verdict_from_counts(new_wins=4, decisive=4, label="t")
    assert v["new_win_rate"] == 1.0
    assert v["new_wilson_lcb"] > 0.5            # documents the LCB pitfall
    assert v["ACCEPT"] is False                 # ...but the floor saves us
    # With the floor lowered to the sample size, the same counts WOULD accept,
    # proving the floor (not the LCB) is what blocks the tiny sample.
    v2 = sp.verdict_from_counts(new_wins=4, decisive=4, label="t", min_decisive=4)
    assert v2["ACCEPT"] is True


def test_smoke_ranking_orders_variants_without_promoting():
    # On a 4-game smoke, the gate must still RANK (better NEW => higher LCB)
    # while refusing to PROMOTE either (floor not met).
    weak = sp.verdict_from_counts(2, 4, "weak")    # 50%
    strong = sp.verdict_from_counts(4, 4, "strong")  # 100%
    assert strong["new_wilson_lcb"] > weak["new_wilson_lcb"]
    assert weak["ACCEPT"] is False and strong["ACCEPT"] is False


def test_verdict_accepts_strong_edge_at_n50():
    # 68% over 50 decisive games clears LCB>0.5 -> ACCEPT.
    v = sp.verdict_from_counts(new_wins=34, decisive=50, label="t")
    assert v["new_wilson_lcb"] > 0.5
    assert v["ACCEPT"] is True


def test_verdict_ranking_is_monotonic():
    # Higher NEW win-rate at fixed n => higher LCB (the gate ranks correctly).
    lo = sp.verdict_from_counts(28, 50, "lo")["new_wilson_lcb"]
    hi = sp.verdict_from_counts(40, 50, "hi")["new_wilson_lcb"]
    assert hi > lo


def test_verdict_empty_is_reject():
    v = sp.verdict_from_counts(0, 0, "t")
    assert v["ACCEPT"] is False
    assert v["new_win_rate"] == 0.0


# --- arm-failure scanner ------------------------------------------------------
# When all arms die at the websocket handshake the per-team counts are all zero
# and verdict_from_counts says "REJECT, 0/0" — indistinguishable from a real
# loss. The scanner + failure summary preserve that signal in the JSON so the
# improve loop can tell "gate could not run" from "gate ran and NEW lost".


_REAL_404_TRACE = (
    "INFO     .env loading: success\n"
    "INFO     Discord battle reporting: ENABLED\n"
    "ERROR    Traceback (most recent call last):\n"
    "  File \"/home/x/run.py\", line 1076, in <module>\n"
    "    asyncio.run(run_foul_play())\n"
    "  File \"/x/fp/websocket_client.py\", line 149, in _connect_websocket\n"
    "    self.websocket = await websockets.connect(\n"
    "ValueError: unsupported protocol; expected HTTP/1.1: HTTP/1.0 404 not found\n"
)


def test_scan_arm_log_detects_websocket_handshake_failure(tmp_path):
    log = tmp_path / "new.log"
    log.write_text(_REAL_404_TRACE, encoding="utf-8")
    line = sp.scan_arm_log_for_failure(log)
    assert line is not None
    assert "unsupported protocol" in line.lower()


def test_scan_arm_log_returns_none_on_clean_log(tmp_path):
    log = tmp_path / "new.log"
    log.write_text(
        "INFO Logged in as foulerNEW\n"
        "INFO Battle finished: battle-gen9ou-1 Winner: foulerNEW\n"
        "INFO Battle finished: battle-gen9ou-2 Winner: foulerOLD\n",
        encoding="utf-8",
    )
    assert sp.scan_arm_log_for_failure(log) is None


def test_scan_arm_log_missing_file_is_none(tmp_path):
    assert sp.scan_arm_log_for_failure(tmp_path / "nope.log") is None


# --- gate_failure_summary aggregates across teams -----------------------------

def test_gate_failure_summary_returns_first_arm_error_when_nothing_finished():
    per_team = [
        {"team": "t1", "battles_finished": 0,
         "arm_errors": {"new": "ValueError: unsupported protocol; ...",
                        "old": "ValueError: unsupported protocol; ..."}},
        {"team": "t2", "battles_finished": 0,
         "arm_errors": {"new": "ValueError: unsupported protocol; ...",
                        "old": None}},
    ]
    s = sp.gate_failure_summary(per_team)
    assert s is not None
    assert s.startswith("t1: ")
    assert "unsupported protocol" in s


def test_gate_failure_summary_is_none_when_some_battles_finished():
    # Even one finished battle means the gate DID run; the math layer's verdict
    # is meaningful and must not be masked by an unrelated arm warning.
    per_team = [
        {"team": "t1", "battles_finished": 0,
         "arm_errors": {"new": "ConnectionRefusedError: ...",
                        "old": None}},
        {"team": "t2", "battles_finished": 3,
         "arm_errors": {"new": None, "old": None}},
    ]
    assert sp.gate_failure_summary(per_team) is None


def test_gate_failure_summary_is_none_when_no_arm_errors():
    per_team = [
        {"team": "t1", "battles_finished": 0,
         "arm_errors": {"new": None, "old": None}},
    ]
    assert sp.gate_failure_summary(per_team) is None
