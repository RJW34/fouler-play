from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from infrastructure import local_rehearsal
from infrastructure import local_rehearsal_opponent
from infrastructure import offline_eval_runner


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def test_rehearsal_urls_require_explicit_loopback_origins() -> None:
    urls = local_rehearsal.validate_rehearsal_urls(
        "ws://127.0.0.1:8765/showdown/websocket",
        "http://127.0.0.1:8765/action.php?",
        "http://127.0.0.1:8877",
    )

    assert urls["showdownPort"] == 8765
    assert urls["showdownBindHost"] == "127.0.0.1"
    assert urls["overlayPort"] == 8877

    with pytest.raises(ValueError, match="loopback"):
        local_rehearsal.validate_rehearsal_urls(
            "ws://sim3.psim.us:443/showdown/websocket",
            "http://127.0.0.1:443/action.php?",
            "http://127.0.0.1:8877",
        )
    with pytest.raises(ValueError, match="credentials"):
        local_rehearsal.validate_rehearsal_urls(
            "ws://user:pass@127.0.0.1:8765/showdown/websocket",
            "http://127.0.0.1:8765/action.php?",
            "http://127.0.0.1:8877",
        )
    with pytest.raises(ValueError, match="127.0.0.1 loopback"):
        local_rehearsal.validate_rehearsal_urls(
            "ws://[::1]:8765/showdown/websocket",
            "http://[::1]:8765/action.php?",
            "http://127.0.0.1:8877",
        )
    with pytest.raises(ValueError, match="127.0.0.1 loopback"):
        local_rehearsal.validate_rehearsal_urls(
            "ws://127.0.0.1:8765/showdown/websocket",
            "http://127.0.0.1:8765/action.php?",
            "http://127.0.0.2:8877",
        )


def test_rehearsal_root_must_not_overlap_source_or_production() -> None:
    with pytest.raises(ValueError, match="overlaps"):
        local_rehearsal.validate_rehearsal_root(
            local_rehearsal.PROJECT_ROOT / ".local-rehearsal"
        )


def test_commands_lock_real_run_py_ladder_shape(tmp_path: Path) -> None:
    layout = local_rehearsal.build_layout(tmp_path / "rehearsal")
    fouler = local_rehearsal.build_fouler_command(
        ["runtime-python"],
        websocket_url=local_rehearsal.DEFAULT_SHOWDOWN_WS_URL,
        search_time_ms=100,
    )
    opponent = local_rehearsal.build_opponent_command(
        ["opponent-python"],
        layout,
        websocket_url=local_rehearsal.DEFAULT_SHOWDOWN_WS_URL,
        authentication_url=local_rehearsal.DEFAULT_SHOWDOWN_AUTH_URL,
        baseline="simple",
        timeout_seconds=7200,
    )

    assert str(local_rehearsal.OFFLINE_RUNNER) in fouler
    assert "run.py" in fouler
    assert fouler[fouler.index("--bot-mode") + 1] == "search_ladder"
    assert fouler[fouler.index("--run-count") + 1] == "30"
    assert fouler[fouler.index("--max-concurrent-battles") + 1] == "3"
    assert fouler[fouler.index("--search-parallelism") + 1] == "2"
    assert fouler[fouler.index("--team-names") + 1].split(",") == list(
        local_rehearsal.LOCKED_TEAM_NAMES
    )
    assert fouler[fouler.index("--save-replay") + 1] == "never"
    assert str(local_rehearsal.OPPONENT_RUNNER) in opponent
    assert "--battles" not in opponent
    assert "--concurrency" not in opponent


def test_rehearsal_environment_isolates_all_writes_and_secrets(tmp_path: Path) -> None:
    layout = local_rehearsal.build_layout(tmp_path / "rehearsal")
    env = local_rehearsal.build_rehearsal_env(
        layout,
        overlay_port=8877,
        authentication_url=local_rehearsal.DEFAULT_SHOWDOWN_AUTH_URL,
        base={
            "PATH": "test-path",
            "PS_PASSWORD": "live-password",
            "OPENAI_API_KEY": "live-key",
            "DISCORD_WEBHOOK_URL": "live-webhook",
            "FOULER_RUNTIME_LEASE_PATH": r"C:\production\lease.json",
            "FOULER_RUNTIME_RESERVATION_ID": "reservation",
        },
    )

    assert env["FOULER_OFFLINE_REHEARSAL"] == "1"
    assert env["FOULER_OBS_OFFLINE_REHEARSAL"] == "1"
    assert env["FOULER_NO_SECURITY_LOGIN"] == "1"
    assert env["PS_PASSWORD"] == ""
    assert env["OPENAI_API_KEY"] == ""
    assert env["DISCORD_WEBHOOK_URL"] == ""
    assert env["FOULER_RUNTIME_LEASE_PATH"] == ""
    assert env["FOULER_RUNTIME_RESERVATION_ID"] == ""
    assert env["FOULER_RUNTIME_STATE_ROOT"] == str(layout.state)
    assert env["FOULER_RUNTIME_LOG_ROOT"] == str(layout.logs)
    assert env["FOULER_RUNTIME_CACHE_ROOT"] == str(layout.cache)
    assert env["FOULER_RUNTIME_TEMP_ROOT"] == str(layout.temp)
    assert env["FOULER_FILE_LOG_LEVEL"] == "INFO"
    assert env["FOULER_WORKER_LOG_LEVEL"] == "INFO"
    assert env["FOULER_BATTLE_STATS_PATH"] == str(layout.battle_stats)
    assert env["FOULER_OFFLINE_ACTIVE_BATTLES_FILE"] == str(layout.active_battles)
    assert env["FOULER_OFFLINE_NETWORK_AUDIT_FILE"] == str(layout.fouler_network_audit)
    assert env["TMP"] == str(layout.temp)


def _valid_evidence() -> dict[str, object]:
    rows = []
    battle_ids = set()
    index = 0
    for team in local_rehearsal.LOCKED_TEAM_BASENAMES:
        for _ in range(10):
            index += 1
            battle_id = f"battle-gen9ou-{index}"
            battle_ids.add(battle_id)
            rows.append(
                {
                    "battle_id": battle_id,
                    "team_file": team,
                    "result": "win" if index % 2 else "loss",
                }
            )
    log_text = "\n".join(
        (
            "Starting 3 battle worker(s)",
            "Per-worker quotas: [10, 10, 10] (total=30)",
            *tuple(
                f"Worker {worker} -> {team}"
                for worker, team in enumerate(local_rehearsal.LOCKED_TEAM_NAMES)
            ),
        )
    )
    clean_audit = {
        "blockedExternalAttemptCount": 0,
        "suppressedOfflineOperations": {},
    }
    fouler_audit = {
        "blockedExternalAttemptCount": 0,
        "suppressedOfflineOperations": {
            "battle-chat": 30,
            "replay-upload-command": 30,
            "public-elo-probe": 30,
        },
    }
    return {
        "battleStats": {"battles": rows},
        "opponentResult": {
            "ok": True,
            "requestedBattles": 30,
            "finishedBattles": 30,
            "decisiveBattles": 30,
            "ties": 0,
            "maxConcurrentBattles": 3,
            "observedPeakActiveBattles": 3,
        },
        "filePeakActiveBattles": 3,
        "overlayPeakActiveBattles": 3,
        "overlaySamples": 10,
        "fileObservedBattleIds": battle_ids,
        "overlayObservedBattleIds": battle_ids,
        "overlayDecisionBattleIds": battle_ids,
        "overlayDecisionSlots": {1, 2, 3},
        "traceBattleIds": battle_ids,
        "finalActiveBattles": {"battles": []},
        "foulerNetworkAudit": fouler_audit,
        "opponentNetworkAudit": clean_audit,
        "showdownNetworkAudits": [
            {
                "loopbackOnly": True,
                "noFilesystemWrites": True,
                "configIntercepted": True,
                "skipBuildInjected": True,
                "blockedExternalAttemptCount": 0,
            }
        ],
        "externalConnections": [],
        "replayArtifacts": [],
        "orphanProcessIdentities": [],
        "listenersAfterCleanup": [],
        "productionPathChanges": [],
        "foulerReturnCode": 0,
        "opponentReturnCode": 0,
        "foulerLogText": log_text,
    }


def test_verifier_requires_exact_30_three_peak_and_ten_per_team() -> None:
    evidence = _valid_evidence()
    assert local_rehearsal.verify_rehearsal(evidence) == []

    evidence["filePeakActiveBattles"] = 2
    evidence["battleStats"]["battles"][0]["team_file"] = "wrong-team"
    blockers = local_rehearsal.verify_rehearsal(evidence)

    assert any("filePeakActiveBattles" in blocker for blocker in blockers)
    assert any("team distribution" in blocker for blocker in blockers)


def test_default_main_is_doctor_only(monkeypatch: pytest.MonkeyPatch) -> None:
    doctor = {"ok": True, "checks": [], "plan": {}}
    monkeypatch.setattr(local_rehearsal, "run_doctor", lambda _args: doctor)

    def unexpected_execute(*_args, **_kwargs):
        raise AssertionError("default invocation must not execute")

    monkeypatch.setattr(local_rehearsal, "execute_rehearsal", unexpected_execute)
    assert local_rehearsal.main([]) == 0


def test_doctor_does_not_create_rehearsal_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "not-created-by-doctor"
    showdown_port = _free_port()
    overlay_port = _free_port()
    while overlay_port == showdown_port:
        overlay_port = _free_port()
    args = local_rehearsal.parse_args(
        [
            "--doctor",
            "--rehearsal-root",
            str(root),
            "--websocket-url",
            f"ws://127.0.0.1:{showdown_port}/showdown/websocket",
            "--authentication-url",
            f"http://127.0.0.1:{showdown_port}/action.php?",
            "--overlay-url",
            f"http://127.0.0.1:{overlay_port}",
        ]
    )
    monkeypatch.setattr(
        local_rehearsal,
        "resolve_python",
        lambda **_kwargs: (["python"], [{"command": "python"}]),
    )

    doctor = local_rehearsal.run_doctor(args)

    assert doctor["plan"]["startsProcesses"] is False
    assert not root.exists()


def test_opponent_rejects_public_server_url(tmp_path: Path) -> None:
    args = argparse.Namespace(
        websocket_url="ws://sim3.psim.us:443/showdown/websocket",
        authentication_url="http://127.0.0.1:443/action.php?",
        username="FoulerLocalOpp",
        fouler_username="FoulerRehearsal",
        team_file=str(local_rehearsal.LOCKED_TEAM_FILES[1]),
        result_file=str(tmp_path / "result.json"),
        network_audit_file=str(tmp_path / "audit.json"),
        timeout_seconds=60,
        baseline="simple",
    )

    with pytest.raises(ValueError, match="loopback"):
        local_rehearsal_opponent.validate_args(args)


@pytest.mark.asyncio
async def test_runtime_guards_suppress_chat_replay_and_public_probes() -> None:
    sent: list[tuple[str, list[str]]] = []

    class FakeWebsocketClient:
        async def send_message(self, room_name, messages):
            sent.append((room_name, list(messages)))

    battle_module = SimpleNamespace()
    audit = offline_eval_runner.LoopbackNetworkGuard()
    offline_eval_runner.configure_offline_runtime_guards(
        battle_module,
        FakeWebsocketClient,
        audit=audit,
    )
    client = FakeWebsocketClient()

    await battle_module._send_battle_chat(client, "battle-local", ["hf"])
    await client.send_message(
        "battle-local",
        ["plain chat", "/choose move 1", "/savereplay"],
    )
    assert await battle_module._fetch_elo("FoulerRehearsal") == (None, None)
    assert await battle_module._replay_exists("gen9ou-1") is False

    assert sent == [("battle-local", ["/choose move 1"])]
    snapshot = audit.snapshot()
    assert snapshot["blockedExternalAttemptCount"] == 0
    assert snapshot["suppressedOfflineOperations"]["battle-chat"] == 2
    assert snapshot["suppressedOfflineOperations"]["replay-upload-command"] == 1
    assert snapshot["suppressedOfflineOperations"]["public-elo-probe"] == 1
    assert snapshot["suppressedOfflineOperations"]["public-replay-probe"] == 1


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is required")
def test_showdown_preload_injects_no_write_config_and_blocks_public_dns(
    tmp_path: Path,
) -> None:
    layout = local_rehearsal.build_layout(tmp_path / "rehearsal")
    layout.showdown_network_audit_dir.mkdir(parents=True)
    local_rehearsal.write_showdown_preload(layout)
    showdown_dir = tmp_path / "pokemon-showdown"
    (showdown_dir / "config").mkdir(parents=True)
    fake_launcher = tmp_path / "fake-launcher" / "pokemon-showdown"
    fake_launcher.parent.mkdir()
    fake_launcher.write_text(
        """
const dns = require("dns");
const path = require("path");
const config = require(path.join(process.env.FOULER_SHOWDOWN_REHEARSAL_DIR, "config", "config.js"));
dns.lookup("127.attacker.invalid", error => {
  process.stdout.write(JSON.stringify({
    argv: process.argv.slice(2),
    blocked: error && error.code,
    loginserver: config.loginserver,
    nofswriting: config.nofswriting,
  }));
});
""".strip(),
        encoding="utf-8",
    )
    env = {
        **os.environ,
        "NODE_OPTIONS": local_rehearsal.node_require_option(layout.showdown_preload),
        "FOULER_SHOWDOWN_REHEARSAL_DIR": str(showdown_dir),
        "FOULER_SHOWDOWN_REHEARSAL_AUDIT_DIR": str(
            layout.showdown_network_audit_dir
        ),
        "PSBINDADDR": "127.0.0.1",
    }

    result = subprocess.run(
        [shutil.which("node"), str(fake_launcher)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["argv"] == ["--skip-build"]
    assert payload["blocked"] == "FOULER_OFFLINE_NETWORK_BLOCKED"
    assert payload["loginserver"] == "http://127.0.0.1:1/"
    assert payload["nofswriting"] is True
    audits = local_rehearsal._read_showdown_network_audits(layout)
    assert len(audits) == 1
    assert audits[0]["configIntercepted"] is True
    assert audits[0]["skipBuildInjected"] is True
    assert audits[0]["blockedExternalAttemptCount"] == 1


def test_showdown_staging_builds_external_copy_without_mutating_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "sibling-showdown"
    (source / "config" / "ladders").mkdir(parents=True)
    (source / "tools").mkdir()
    (source / "node_modules").mkdir()
    for relative, content in (
        ("pokemon-showdown", "launcher"),
        ("build", "builder"),
        ("package.json", "{}"),
        ("tools/build-utils.js", "module.exports = {};"),
        ("config/config-example.js", "exports.port = 8000;"),
        ("config/ladders/gen9ou.tsv", "production ladder"),
        ("server/ip-tools.ts", "export default {};\nvoid IPTools.updateTorRanges();\n"),
        ("server/chat-plugins/seasons.ts", "export function rollTimer() {}\nrollTimer();\n"),
    ):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    before = local_rehearsal._path_fingerprint(source)
    layout = local_rehearsal.build_layout(tmp_path / "rehearsal")
    layout.logs.mkdir(parents=True)
    layout.showdown_network_audit_dir.mkdir(parents=True)
    local_rehearsal.write_showdown_preload(layout)
    captured: dict[str, object] = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        captured["env"] = kwargs["env"]
        target = Path(kwargs["cwd"])
        for relative in ("dist/server/index.js", "dist/sim/dex.js"):
            path = target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("compiled", encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(local_rehearsal.shutil, "which", lambda _name: "node")
    monkeypatch.setattr(local_rehearsal.subprocess, "run", fake_run)

    staged = local_rehearsal.stage_showdown_runtime(
        layout,
        source,
        timeout_seconds=30,
    )

    assert staged == layout.showdown_runtime
    assert (staged / "dist" / "server" / "index.js").is_file()
    assert not (staged / "node_modules").exists()
    assert not (staged / "config" / "ladders").exists()
    assert "nofswriting: true" in (staged / "config" / "config.js").read_text(
        encoding="utf-8"
    )
    assert "foulerofflinerehearsal: true" in (
        staged / "config" / "config.js"
    ).read_text(encoding="utf-8")
    assert "if (!Config.foulerofflinerehearsal) void IPTools.updateTorRanges();" in (
        staged / "server" / "ip-tools.ts"
    ).read_text(encoding="utf-8")
    assert "if (!Config.foulerofflinerehearsal) rollTimer();" in (
        staged / "server" / "chat-plugins" / "seasons.ts"
    ).read_text(encoding="utf-8")
    patch_proof = json.loads(
        (layout.proof / "showdown-staging-patches.json").read_text(encoding="utf-8")
    )
    assert [row["file"] for row in patch_proof["patches"]] == [
        "server/ip-tools.ts",
        "server/chat-plugins/seasons.ts",
    ]
    assert captured["cwd"] == str(staged)
    assert captured["env"]["NODE_PATH"] == str(source / "node_modules")
    assert local_rehearsal._path_fingerprint(source) == before
