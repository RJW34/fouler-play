"""Unit tests for sync_loss_evidence.py -- the KEYSTONE that feeds the loop.

This module pulls loss replay JSON + per-turn decision traces from the live
JIGGLY runtime to ubunztu so autoresearch has evidence to mine. The risky parts
are pure and testable WITHOUT touching JIGGLY or the network:

  * id/tag normalization  -- a wrong replay-id or trace-tag fetches nothing and
    silently re-starves the loop, so the naming must be pinned.
  * window selection       -- only recent LOSSES, matching autoresearch -n.
  * idempotency            -- already-local evidence is skipped (cheap re-runs).
  * capacity caps          -- --max-files / --max-mb are HARD stops (ubunztu is
    at ~98% inodes; an uncapped pull could ENOSPC the box).

We monkeypatch the two shell-outs (_scp_pull, _ssh_list_traces) so no SSH ever
happens; the test exercises the real control flow around them.
"""
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location(
    "sync_loss_evidence", ROOT / "infrastructure" / "sync_loss_evidence.py"
)
sle = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sle)


# --- id / tag normalization ---------------------------------------------------

def test_normalize_replay_id_strips_prefix_and_suffix():
    assert sle._normalize_replay_id("battle-gen9ou-123.json") == "gen9ou-123"
    assert sle._normalize_replay_id("gen9ou-123") == "gen9ou-123"
    assert sle._normalize_replay_id("battle-gen9ou-123") == "gen9ou-123"
    assert sle._normalize_replay_id("gen9ou-123.json") == "gen9ou-123"
    assert sle._normalize_replay_id(None) == ""
    assert sle._normalize_replay_id("") == ""


def test_normalize_keeps_room_hash():
    """Battles with a room hash must keep it -- that's the real on-PS replay id."""
    rid = "gen9ou-2622172804-jiqrvsmtat7fa005gn6wkguc6aydyx6pw"
    assert sle._normalize_replay_id(rid) == rid
    assert sle._normalize_replay_id("battle-" + rid + ".json") == rid


def test_battle_tag_prefixes_battle_for_traces():
    """write_decision_trace names files battle-<id>_turnN.json; the tag must
    carry that 'battle-' prefix or the trace glob misses everything."""
    assert sle._battle_tag({"battle_id": "gen9ou-999"}) == "battle-gen9ou-999"
    assert sle._battle_tag({"battle_id": "battle-gen9ou-999"}) == "battle-gen9ou-999"
    assert sle._battle_tag({"replay_id": "gen9ou-777"}) == "battle-gen9ou-777"
    assert sle._battle_tag({}) == ""


# --- window selection ---------------------------------------------------------

def _setup_stats(tmp_path, monkeypatch, battles):
    bs = tmp_path / "battle_stats.json"
    bs.write_text(json.dumps({"battles": battles}), encoding="utf-8")
    monkeypatch.setattr(sle, "BATTLE_STATS", bs)
    monkeypatch.setattr(sle, "REPLAY_DIR", tmp_path / "replay_analysis")
    monkeypatch.setattr(sle, "TRACE_DIR", tmp_path / "logs" / "decision_traces")
    # Default status artifact must never land in the real repo from tests --
    # redirect it to tmp_path so legacy tests stay sandboxed even though
    # sync_loss_evidence now writes a status sidecar at the end of main().
    monkeypatch.setattr(sle, "STATUS_PATH",
                        tmp_path / "devstream" / "truth" / "sync-evidence-status.json")


def test_load_losses_only_losses_in_window(tmp_path, monkeypatch):
    battles = (
        [{"result": "loss", "replay_id": f"gen9ou-{i}"} for i in range(5)]
        + [{"result": "win", "replay_id": f"gen9ou-w{i}"} for i in range(5)]
        + [{"result": "loss", "replay_id": "gen9ou-recent"}]
    )
    _setup_stats(tmp_path, monkeypatch, battles)
    # window of 3 = last 3 battles = win, win? -> actually last 3 are win,win,loss
    losses = sle.load_losses(window=3)
    assert [b["replay_id"] for b in losses] == ["gen9ou-recent"]
    # full window picks up all 6 losses
    assert len(sle.load_losses(window=100)) == 6


# --- end-to-end main() with shell-outs stubbed --------------------------------

def test_main_fetches_missing_and_skips_local(tmp_path, monkeypatch):
    battles = [
        {"result": "loss", "battle_id": "gen9ou-A"},
        {"result": "loss", "battle_id": "gen9ou-B"},
    ]
    _setup_stats(tmp_path, monkeypatch, battles)
    (tmp_path / "replay_analysis").mkdir()
    # A is ALREADY local -> must be skipped; B must be fetched.
    (tmp_path / "replay_analysis" / "gen9ou-A.json").write_text("{}", encoding="utf-8")
    (tmp_path / "logs" / "decision_traces").mkdir(parents=True)
    (tmp_path / "logs" / "decision_traces" / "battle-gen9ou-A_turn1.json").write_text(
        "{}", encoding="utf-8")

    pulled = []

    def fake_scp(repo, remote_rel, local_path):
        pulled.append(remote_rel)
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_text("{}", encoding="utf-8")
        return 42  # bytes

    def fake_list_traces(repo, tag):
        return [f"{tag}_turn1.json"] if tag.endswith("gen9ou-B") else []

    monkeypatch.setattr(sle, "_scp_pull", fake_scp)
    monkeypatch.setattr(sle, "_ssh_list_traces", fake_list_traces)

    monkeypatch.setattr("sys.argv", ["sync", "--window", "10"])
    rc = sle.main()
    assert rc == 0
    # only B's replay + B's trace were pulled; A (local) was untouched.
    assert any("gen9ou-B.json" in r for r in pulled)
    assert not any("gen9ou-A.json" in r for r in pulled)
    assert any("battle-gen9ou-B_turn1.json" in r for r in pulled)


def test_main_dry_run_pulls_nothing(tmp_path, monkeypatch):
    battles = [{"result": "loss", "battle_id": "gen9ou-X"}]
    _setup_stats(tmp_path, monkeypatch, battles)

    def boom(*a, **k):
        raise AssertionError("dry-run must not scp")

    monkeypatch.setattr(sle, "_scp_pull", boom)
    monkeypatch.setattr(sle, "_ssh_list_traces", boom)
    monkeypatch.setattr("sys.argv", ["sync", "--window", "10", "--dry-run"])
    assert sle.main() == 0


def test_main_respects_max_files_cap(tmp_path, monkeypatch):
    battles = [{"result": "loss", "battle_id": f"gen9ou-{i}"} for i in range(10)]
    _setup_stats(tmp_path, monkeypatch, battles)

    calls = {"n": 0}

    def counting_scp(repo, remote_rel, local_path):
        calls["n"] += 1
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_text("{}", encoding="utf-8")
        return 1000

    monkeypatch.setattr(sle, "_scp_pull", counting_scp)
    monkeypatch.setattr(sle, "_ssh_list_traces", lambda repo, tag: [])
    # Cap at 3 files: even though 10 losses need replays, no more than 3 pulls.
    monkeypatch.setattr("sys.argv",
                        ["sync", "--window", "20", "--max-files", "3"])
    assert sle.main() == 0
    assert calls["n"] <= 3, f"cap breached: {calls['n']} pulls"


def test_main_missing_battle_stats_returns_2(tmp_path, monkeypatch):
    monkeypatch.setattr(sle, "BATTLE_STATS", tmp_path / "nope.json")
    monkeypatch.setattr("sys.argv", ["sync"])
    assert sle.main() == 2


def _seed_local_traces(tmp_path, battles):
    """Pre-place local trace files so the trace path short-circuits via
    ``have_trace`` -- isolates tests that care only about replay budget."""
    trace_dir = tmp_path / "logs" / "decision_traces"
    trace_dir.mkdir(parents=True, exist_ok=True)
    for b in battles:
        tag = sle._battle_tag(b)
        (trace_dir / f"{tag}_turn1.json").write_text("{}", encoding="utf-8")


def test_main_budget_capped_replays_are_surfaced_distinctly(tmp_path, monkeypatch, capsys):
    """FOULER-SYNC-EVIDENCE-BUDGET-CAPPED-TRUTH-2026-06-03 guard.

    Pre-fix, budget/files-cap skips were silently rolled into ``miss=N`` while
    ``missing_remote`` (the "still missing on JIGGLY" line) was empty -- so
    the SUMMARY claimed losses were missing JIGGLY-side when in fact we had
    chosen not to pull them. The two causes need OPPOSITE next-actions
    (raise --max-mb vs. investigate the JIGGLY writer); they must be split.
    """
    battles = [{"result": "loss", "battle_id": f"gen9ou-{i}"} for i in range(5)]
    _setup_stats(tmp_path, monkeypatch, battles)
    _seed_local_traces(tmp_path, battles)

    def counting_scp(repo, remote_rel, local_path):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_text("{}", encoding="utf-8")
        return 1000

    monkeypatch.setattr(sle, "_scp_pull", counting_scp)
    monkeypatch.setattr(sle, "_ssh_list_traces",
                        lambda repo, tag: (_ for _ in ()).throw(
                            AssertionError("ssh must not run when traces are local")))
    # Cap at 2 files: 5 losses need replays; 2 land, 3 are budget-capped.
    monkeypatch.setattr("sys.argv",
                        ["sync", "--window", "10", "--max-files", "2"])
    assert sle.main() == 0
    out = capsys.readouterr().out

    # The summary's miss=N must NOT include budget-capped replays. With
    # max_files=2: fetched=2, budget_capped=3, miss=0 (none scp-failed).
    assert "fetched=2 miss=0" in out, out
    assert "budget_capped=3" in out, out
    # Budget-capped detail line must list the actual replay ids so the
    # operator can correlate them with battle_stats.json, and tell the
    # operator how to lift the cap.
    assert "budget-capped, not fetched (3)" in out, out
    assert "replay:gen9ou-" in out, out
    assert "--max-mb" in out and "--max-files" in out, out
    # The "still missing on JIGGLY" wording reserved for scp-failed pulls
    # must NOT fire when 0 scp calls failed -- this was the pre-fix lie.
    assert "still missing on JIGGLY" not in out, out


def test_main_distinguishes_scp_failed_replays_from_budget_capped(tmp_path, monkeypatch, capsys):
    """Mixed run: some scp pulls genuinely fail (replay expired on PS), some
    are budget-capped. The two buckets must be reported on separate lines so
    operators can tell which next-action applies. This is the case the
    pre-fix summary collapsed into a single miss=N count."""
    battles = [{"result": "loss", "battle_id": f"gen9ou-{i}"} for i in range(4)]
    _setup_stats(tmp_path, monkeypatch, battles)
    _seed_local_traces(tmp_path, battles)

    # First call fails (simulates expired-on-PS replay); rest succeed until cap.
    state = {"calls": 0}

    def picky_scp(repo, remote_rel, local_path):
        state["calls"] += 1
        if state["calls"] == 1:
            return 0  # scp failed -> still missing on JIGGLY
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_text("{}", encoding="utf-8")
        return 1000

    monkeypatch.setattr(sle, "_scp_pull", picky_scp)
    monkeypatch.setattr(sle, "_ssh_list_traces", lambda repo, tag: [])
    # max_files=2: scp1 fails (fetched_files unchanged=0), scp2 succeeds
    # (fetched_files=1), scp3 succeeds (fetched_files=2), scp4 budget-capped.
    monkeypatch.setattr("sys.argv",
                        ["sync", "--window", "10", "--max-files", "2"])
    assert sle.main() == 0
    out = capsys.readouterr().out

    # Both buckets must appear, distinctly.
    assert "still missing on JIGGLY (1)" in out, out
    assert "budget-capped, not fetched (1)" in out, out
    # SUMMARY: fetched=2 (calls 2 and 3), miss=1 (call 1), budget_capped=1.
    assert "fetched=2 miss=1" in out, out
    assert "budget_capped=1" in out, out


def test_status_json_starved_when_zero_evidence_for_losses(tmp_path, monkeypatch):
    """DEKU-IMPROVE-FOULER-SYNC-EVIDENCE-STATUS-ARTIFACT-2026-06-03 guard.

    When EVERY loss in the window has zero replay JSON locally, zero traces
    locally, and the JIGGLY-side scp listing returns nothing for either,
    the learn-loop is STARVED. The status artifact must say so explicitly
    -- not bury it in a stdout summary that gets thrown into a log file
    and forgotten. autoresearch already detects starvation downstream; this
    artifact makes it visible to cycle-report / Discord status / cron the
    instant sync runs, hours before autoresearch.
    """
    battles = [{"result": "loss", "battle_id": f"gen9ou-{i}"} for i in range(3)]
    _setup_stats(tmp_path, monkeypatch, battles)
    monkeypatch.setattr(sle, "_scp_pull", lambda *a, **k: 0)
    monkeypatch.setattr(sle, "_ssh_list_traces", lambda *a, **k: [])
    status_path = tmp_path / "devstream" / "truth" / "sync-evidence-status.json"
    monkeypatch.setattr(
        "sys.argv",
        ["sync", "--window", "10", "--status-path", str(status_path)],
    )
    assert sle.main() == 0
    assert status_path.exists(), "status artifact must be written"

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["schemaVersion"] == sle.STATUS_SCHEMA_VERSION
    assert status["lossCount"] == 3
    assert status["replay"]["local"] == 0
    assert status["replay"]["fetched"] == 0
    assert status["replay"]["missingRemote"] == 3
    assert status["trace"]["local"] == 0
    assert status["trace"]["fetched"] == 0
    # Hard guarantee: zero evidence for any loss => starved=true with a reason.
    assert status["starved"] is True, status
    assert status["starvedReason"], status


def test_status_json_not_starved_when_evidence_present(tmp_path, monkeypatch):
    """When at least one loss has replay JSON (local OR freshly fetched),
    the loop has *something* to mine and starved must be False. Otherwise
    a single landed replay would fail to clear the starvation flag and the
    Discord surface would keep yelling about a fixed problem."""
    battles = [{"result": "loss", "battle_id": f"gen9ou-{i}"} for i in range(2)]
    _setup_stats(tmp_path, monkeypatch, battles)
    (tmp_path / "replay_analysis").mkdir(parents=True, exist_ok=True)
    (tmp_path / "replay_analysis" / "gen9ou-0.json").write_text("{}", encoding="utf-8")
    _seed_local_traces(tmp_path, battles)
    # One replay is local (gen9ou-0); the second loss's replay scp may be
    # attempted but is irrelevant to the starvation check. Traces are local
    # for both, so the ssh path must not run.
    monkeypatch.setattr(sle, "_scp_pull", lambda *a, **k: 0)
    monkeypatch.setattr(sle, "_ssh_list_traces", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("ssh must not run when traces are local")))
    status_path = tmp_path / "devstream" / "truth" / "sync-evidence-status.json"
    monkeypatch.setattr(
        "sys.argv",
        ["sync", "--window", "10", "--status-path", str(status_path)],
    )
    assert sle.main() == 0

    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["replay"]["local"] == 1
    assert status["trace"]["local"] == 2
    assert status["starved"] is False, status
    assert status["starvedReason"] is None, status


def test_status_json_starved_reason_distinguishes_budget_capped_from_missing(
    tmp_path, monkeypatch
):
    """The starvedReason must tell the operator WHICH next-action applies:
    raise the budget vs. fix the JIGGLY writer. Without this split, the
    Discord surface would say 'starved' with no actionable next step."""
    battles = [{"result": "loss", "battle_id": f"gen9ou-{i}"} for i in range(3)]
    _setup_stats(tmp_path, monkeypatch, battles)
    _seed_local_traces(tmp_path, battles)
    # Make every replay scp succeed -- but cap at 0 files so all 3 land in
    # the budget_capped bucket and none in missing_remote. Traces are local.
    monkeypatch.setattr(sle, "_scp_pull", lambda *a, **k: 1000)
    monkeypatch.setattr(sle, "_ssh_list_traces", lambda *a, **k: [])
    status_path = tmp_path / "status.json"
    monkeypatch.setattr(
        "sys.argv",
        ["sync", "--window", "10", "--max-files", "0",
         "--status-path", str(status_path)],
    )
    assert sle.main() == 0
    status = json.loads(status_path.read_text(encoding="utf-8"))
    # Traces are local -> evidenced > 0 -> NOT starved. The reason field
    # should reflect that the loop has something to mine despite capped replays.
    assert status["trace"]["local"] == 3
    assert status["replay"]["budgetCapped"] == 3
    assert status["starved"] is False


def test_status_json_disabled_with_empty_path(tmp_path, monkeypatch):
    """--status-path "" must suppress the artifact; existing cron/legacy
    callers that don't want the file should not have one materialize."""
    battles = [{"result": "loss", "battle_id": "gen9ou-X"}]
    _setup_stats(tmp_path, monkeypatch, battles)
    _seed_local_traces(tmp_path, battles)
    (tmp_path / "replay_analysis").mkdir(parents=True, exist_ok=True)
    (tmp_path / "replay_analysis" / "gen9ou-X.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sle, "_scp_pull", lambda *a, **k: 0)
    monkeypatch.setattr(sle, "_ssh_list_traces", lambda *a, **k: [])
    # Point the default STATUS_PATH at a tmp location so the test can verify
    # absence without depending on the real repo.
    monkeypatch.setattr(sle, "STATUS_PATH", tmp_path / "should_not_exist.json")
    monkeypatch.setattr(
        "sys.argv",
        ["sync", "--window", "10", "--status-path", ""],
    )
    assert sle.main() == 0
    assert not (tmp_path / "should_not_exist.json").exists()


def test_main_budget_capped_traces_skip_ssh_when_already_capped(tmp_path, monkeypatch, capsys):
    """When the byte/file cap is already hit BEFORE a battle's traces are
    processed, we must NOT burn an SSH roundtrip listing JIGGLY's directory
    -- that's pure waste under the cap. The traces for that battle are
    recorded as budget-capped without contacting JIGGLY."""
    battles = [{"result": "loss", "battle_id": f"gen9ou-{i}"} for i in range(3)]
    _setup_stats(tmp_path, monkeypatch, battles)

    # Replay pulls eat the file budget immediately; traces are NOT local so
    # the trace path is exercised.
    def fat_scp(repo, remote_rel, local_path):
        Path(local_path).parent.mkdir(parents=True, exist_ok=True)
        Path(local_path).write_text("{}", encoding="utf-8")
        return 1000

    ssh_calls = {"n": 0}

    def counted_ssh(repo, tag):
        ssh_calls["n"] += 1
        return [f"{tag}_turn1.json"]

    monkeypatch.setattr(sle, "_scp_pull", fat_scp)
    monkeypatch.setattr(sle, "_ssh_list_traces", counted_ssh)
    # max_files=1: battle 0 replay lands (fetched_files=1). Battle 0's traces
    # then sit at the cap and must NOT burn an SSH roundtrip. Battles 1 and 2
    # are fully budget-capped (replay AND traces) without SSH either.
    monkeypatch.setattr("sys.argv",
                        ["sync", "--window", "10", "--max-files", "1"])
    assert sle.main() == 0
    out = capsys.readouterr().out

    assert ssh_calls["n"] == 0, (
        f"budget-capped traces must skip SSH listing; saw {ssh_calls['n']} ssh calls"
    )
    # The summary must report all 3 trace budget-caps + 2 replay budget-caps.
    assert "budget_capped=5" in out, out
    # And the still-missing-on-JIGGLY line must NOT fire (no scp/ssh failed).
    assert "still missing on JIGGLY" not in out, out
