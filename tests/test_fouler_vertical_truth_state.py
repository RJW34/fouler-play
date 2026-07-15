from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
OVERLAY = ROOT / "streaming" / "fouler_vertical.html"
HTML = OVERLAY.read_text(encoding="utf-8")
NODE = shutil.which("node")


def _javascript_function(name: str) -> str:
    start = HTML.index(f"function {name}(")
    opening_brace = HTML.index("{", start)
    depth = 0
    for index in range(opening_brace, len(HTML)):
        if HTML[index] == "{":
            depth += 1
        elif HTML[index] == "}":
            depth -= 1
            if depth == 0:
                return HTML[start : index + 1]
    raise AssertionError(f"unterminated JavaScript function: {name}")


def _run_javascript(source: str) -> dict:
    assert NODE is not None
    completed = subprocess.run(
        [NODE, "-"],
        input=source,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout)


def test_initial_surface_has_no_plausible_unverified_metrics_or_search_claim() -> None:
    assert 'id="record">--</strong>' in HTML
    assert 'id="elo">--</strong>' in HTML
    assert 'id="liveCount">-- / 3</strong>' in HTML
    assert 'var match = create("article", "match loading")' in HTML
    assert 'var scoreboardSnapshot = { record: null, elo: null, liveCount: null }' in HTML
    assert "--social-safe-top: 96px" in HTML
    assert "--social-safe-right: 120px" in HTML
    assert "--social-safe-bottom: 176px" in HTML
    assert "--social-safe-left: 56px" in HTML
    assert "padding: var(--social-safe-top) var(--social-safe-right)" in HTML
    assert 'padding: 26px 40px 24px' in HTML
    assert "font-size: 9px" not in HTML
    assert "font-size: 10px" not in HTML
    assert re.search(r"\.match-title strong\s*\{[^}]*text-overflow: ellipsis", HTML, re.DOTALL)
    assert re.search(r"\.pokemon-name\s*\{[^}]*text-overflow: ellipsis", HTML, re.DOTALL)


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute the inline overlay state machine")
def test_slot_presentation_requires_explicit_search_and_usable_battle_truth() -> None:
    source = "\n".join(
        _javascript_function(name)
        for name in (
            "escapeText",
            "titleWords",
            "usableBattleView",
            "staleBattleTruth",
            "explicitSearchActivity",
            "timerLabel",
            "inactivePresentation",
            "slotPresentation",
        )
    )
    result = _run_javascript(
        source
        + r"""
const completeView = {
  user: {active: {name: "blissey"}},
  opponent: {active: {name: "greattusk"}}
};
const staleView = JSON.parse(JSON.stringify(completeView));
staleView.stale = true;
process.stdout.write(JSON.stringify({
  idle: slotPresentation({active: false, status: "Idle"}),
  held: slotPresentation({active: false, status: "Held"}),
  ready: slotPresentation({active: false, status: "Ready"}),
  maintenance: slotPresentation({active: false, status: "Maintenance"}),
  impliedSearching: slotPresentation({active: false, status: "Searching"}),
  searching: slotPresentation({active: false, status: "Searching", search_active: true}),
  unavailable: slotPresentation({active: false, status: "Credential blocked"}),
  missingView: slotPresentation({active: true, opponent: "Test Opponent", battle_view: null}),
  incompleteView: slotPresentation({
    active: true,
    opponent: "Test Opponent",
    battle_view: {user: {}, opponent: {}}
  }),
  live: slotPresentation({
    active: true,
    opponent: "Test Opponent",
    age_label: "4m 08s",
    battle_view: completeView
  }),
  stale: slotPresentation({active: true, opponent: "Test Opponent", battle_view: staleView}),
  stalePayload: slotPresentation({
    active: false,
    stale: true,
    freshness: "stale",
    freshness_age_label: "2m 01s",
    opponent: "Test Opponent",
    battle_view: null
  })
}));
"""
    )

    assert result["idle"]["className"] == "idle"
    assert result["idle"]["state"] == "Idle"
    assert result["idle"]["opponent"] != "Finding an opponent"
    assert result["idle"]["queueTitle"] != "Finding ranked opponent"
    assert result["held"]["className"] == "idle"
    assert result["held"]["state"] == "Idle"
    assert result["held"]["opponent"] == "Battles paused"
    assert result["ready"]["className"] == "ready"
    assert result["ready"]["state"] == "Ready"
    assert result["maintenance"]["className"] == "idle"
    assert result["maintenance"]["state"] == "Idle"
    assert result["maintenance"]["opponent"] != "Finding an opponent"
    assert result["maintenance"]["queueTitle"] != "Finding ranked opponent"
    assert result["impliedSearching"]["className"] == "idle"
    assert result["impliedSearching"]["state"] == "Idle"
    assert result["searching"]["className"] == "searching"
    assert result["searching"]["state"] == "Searching"
    assert result["unavailable"]["className"] == "maintenance"
    assert result["unavailable"]["state"] == "Unavailable"

    assert result["missingView"]["className"] == "loading"
    assert result["missingView"]["state"] == "Loading"
    assert result["missingView"]["viewReady"] is False
    assert result["incompleteView"]["className"] == "loading"
    assert result["live"]["className"] == "live"
    assert result["live"]["viewReady"] is True
    assert result["live"]["footRight"] == "Battle time: 4m 08s"
    assert result["stale"]["className"] == "delayed"
    assert result["stale"]["state"] == "Stale"
    assert result["stale"]["live"] is False
    assert result["stalePayload"]["className"] == "delayed loading"
    assert result["stalePayload"]["state"] == "Stale"
    assert result["stalePayload"]["live"] is False
    assert result["stalePayload"]["footRight"] == "Last update: 2m 01s ago"

    serialized = json.dumps(result)
    for jargon in (
        "Bounded ladder runtime",
        "Runtime ready",
        "public state",
        "verified battle",
        "next batch",
    ):
        assert jargon.lower() not in serialized.lower()


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute the inline overlay state machine")
def test_poll_failures_retain_verified_scoreboard_and_mark_freshness() -> None:
    source = _javascript_function("staleBattleTruth") + "\n" + _javascript_function("updateScoreboard")
    result = _run_javascript(
        r"""
var SLOT_COUNT = 3;
var scoreboardSnapshot = {record: null, elo: null, liveCount: null};
var elements = {
  record: {textContent: ""},
  elo: {textContent: ""},
  liveCount: {textContent: ""},
  scoreboard: {className: ""},
  metricsState: {textContent: ""}
};
var document = {getElementById: function (id) { return elements[id]; }};
"""
        + source
        + r"""
function visibleState() {
  return {
    record: elements.record.textContent,
    elo: elements.elo.textContent,
    liveCount: elements.liveCount.textContent,
    scoreboardClass: elements.scoreboard.className,
    metricsState: elements.metricsState.textContent
  };
}
var successful = [
  {battle_lab: {active: true, wins: 12, losses: 8, elo: 1462}},
  {battle_lab: {active: true, wins: 12, losses: 8, elo: 1462}},
  {battle_lab: {active: false, wins: 12, losses: 8, elo: 1462}}
];
updateScoreboard(successful);
var current = visibleState();
updateScoreboard([null, null, null]);
var stale = visibleState();
updateScoreboard([successful[0], null, null]);
var partial = visibleState();
updateScoreboard([
  {battle_lab: {active: false, wins: null, losses: null, elo: null}},
  {battle_lab: {active: false, wins: null, losses: null, elo: null}},
  {battle_lab: {active: false, wins: null, losses: null, elo: null}}
]);
var missing = visibleState();
scoreboardSnapshot = {record: null, elo: null, liveCount: null};
updateScoreboard([null, null, null]);
var unavailable = visibleState();
process.stdout.write(JSON.stringify({current, stale, partial, missing, unavailable}));
"""
    )

    assert result["current"] == {
        "record": "12-8",
        "elo": 1462,
        "liveCount": "2 / 3",
        "scoreboardClass": "scoreboard metrics-current",
        "metricsState": "Scores current",
    }
    for state in ("stale", "partial", "missing"):
        assert result[state]["record"] == "12-8"
        assert result[state]["elo"] == 1462
        assert result[state]["scoreboardClass"] == "scoreboard metrics-stale"
    assert result["stale"]["liveCount"] == "2 / 3"
    assert result["partial"]["liveCount"] == "2 / 3"
    assert result["missing"]["liveCount"] == "0 / 3"
    assert result["stale"]["metricsState"] == "Score update delayed"
    assert result["partial"]["metricsState"] == "Score update delayed"
    assert result["missing"]["metricsState"] == "Score details delayed"
    assert result["unavailable"] == {
        "record": "--",
        "elo": "--",
        "liveCount": "-- / 3",
        "scoreboardClass": "scoreboard metrics-unavailable",
        "metricsState": "Scores unavailable",
    }


@pytest.mark.skipif(NODE is None, reason="Node.js is required to execute the inline overlay state machine")
def test_successful_stale_payload_excludes_slot_and_degrades_metrics() -> None:
    source = _javascript_function("staleBattleTruth") + "\n" + _javascript_function("updateScoreboard")
    result = _run_javascript(
        r"""
var SLOT_COUNT = 3;
var scoreboardSnapshot = {record: null, elo: null, liveCount: null};
var elements = {
  record: {textContent: ""},
  elo: {textContent: ""},
  liveCount: {textContent: ""},
  scoreboard: {className: ""},
  metricsState: {textContent: ""}
};
var document = {getElementById: function (id) { return elements[id]; }};
"""
        + source
        + r"""
updateScoreboard([
  {battle_lab: {active: false, stale: true, freshness: "stale", wins: 12, losses: 8, elo: 1462}},
  {battle_lab: {active: true, stale: false, freshness: "current", wins: 12, losses: 8, elo: 1462}},
  {battle_lab: {active: false, stale: false, freshness: "idle", wins: 12, losses: 8, elo: 1462}}
]);
process.stdout.write(JSON.stringify({
  record: elements.record.textContent,
  elo: elements.elo.textContent,
  liveCount: elements.liveCount.textContent,
  scoreboardClass: elements.scoreboard.className,
  metricsState: elements.metricsState.textContent
}));
"""
    )

    assert result == {
        "record": "12-8",
        "elo": 1462,
        "liveCount": "1 / 3",
        "scoreboardClass": "scoreboard metrics-stale",
        "metricsState": "1 match stale",
    }
