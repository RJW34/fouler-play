#!/usr/bin/env python3
"""Read-only readiness doctor for the offline eval acceptance gate."""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Mapping

try:
    import psutil  # type: ignore
except ImportError:  # pragma: no cover - exercised on minimal environments
    psutil = None


PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "fouler-play-offline-eval-readiness/v1"
FOULER_RUNTIME_IMPORTS = ("aiohttp", "requests", "dotenv", "dateutil", "psutil", "poke_engine")
RUNNING_STATUS_STAGES = {
    "starting",
    "starting-showdown",
    "running-frozen-eval",
    "running-candidate-eval",
    "fouler-started",
    "baseline-started",
    "baseline-finished",
}
OFFLINE_STATUS_FINITE_PRECONDITIONS = [
    "an offline eval sidecar is started only with an explicit positive --battles bound",
    "the status artifact can be adopted only while its serverPid is still alive and inspectable",
    "a dead or uninspectable running status must be archived/replaced before recursive improvement starts",
    "the readiness probe is read-only and never deletes eval_results/offline status artifacts",
]


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        return int(env.get(name, str(default)))
    except (TypeError, ValueError):
        return default


def _quote_command(parts: list[str]) -> str:
    return subprocess.list2cmdline(parts)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sh_quote(value: str) -> str:
    return shlex.quote(value)


def _relative(root: Path, path: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _configured_path(root: Path, env: Mapping[str, str], name: str, default: Path) -> Path:
    raw = env.get(name)
    if not raw:
        return default
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = root / path
    return path


def eval_venv_python(root: Path) -> Path:
    windows = root / ".venv-eval" / "Scripts" / "python.exe"
    if sys.platform == "win32":
        return windows
    if windows.exists():
        return windows
    return root / ".venv-eval" / "bin" / "python"


def _split_python_command(raw: str) -> list[str]:
    return shlex.split(raw, posix=sys.platform != "win32")


def _runtime_python_candidates(root: Path, env: Mapping[str, str]) -> list[list[str]]:
    explicit = env.get("FOULER_RUNTIME_PYTHON")
    if explicit:
        return [_split_python_command(explicit)]

    candidates: list[list[str]] = [[sys.executable]]
    local_venvs = [
        root / ".venv" / "Scripts" / "python.exe",
        root / ".venv" / "bin" / "python",
    ]
    for python_path in local_venvs:
        if python_path.exists():
            candidates.append([str(python_path)])

    py_launcher = shutil.which("py")
    if py_launcher:
        candidates.append([py_launcher, "-3"])
    for command in ("python", "python3"):
        executable = shutil.which(command)
        if executable:
            candidates.append([executable])

    unique: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for candidate in candidates:
        key = tuple(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def configured_eval(root: Path, env: Mapping[str, str]) -> dict[str, object]:
    battles = _env_int(env, "IMPROVE_AGENT_EVAL_BATTLES", 200)
    baseline = env.get("IMPROVE_AGENT_EVAL_BASELINE", "simple")
    team = env.get("IMPROVE_AGENT_EVAL_TEAM", "gen9/ou/fat-team-1-stall")
    showdown_port = _env_int(env, "EVAL_SHOWDOWN_PORT", 8765)
    showdown_dir = _configured_path(root, env, "POKEMON_SHOWDOWN_DIR", root.parent / "pokemon-showdown")
    label = "candidate"
    return {
        "battles": battles,
        "baseline": baseline,
        "team": team,
        "showdownPort": showdown_port,
        "label": label,
        "showdownDir": showdown_dir,
        "showdownPackage": showdown_dir / "package.json",
        "showdownLauncher": showdown_dir / "pokemon-showdown",
        "showdownNodeModules": showdown_dir / "node_modules",
        "teamFile": root / "teams" / Path(*str(team).split("/")),
        "evalScript": root / "infrastructure" / "offline_eval.py",
        "baselineScript": root / "infrastructure" / "_offline_baseline.py",
        "requirementsEval": root / "infrastructure" / "requirements-eval.txt",
        "venvPython": eval_venv_python(root),
        "resultsDir": root / "eval_results" / "offline",
        "frozenBaseline": root / "eval_results" / "offline" / "frozen.json",
        "candidateResult": root / "eval_results" / "offline" / "candidate.json",
        "compareResult": root / "eval_results" / "offline" / "compare-frozen-vs-candidate.json",
    }


def eval_command(config: dict[str, object], *, label: str = "candidate", no_setsample: bool = False) -> list[str]:
    cmd = [
        str(config["venvPython"]),
        str(config["evalScript"]),
        "--battles",
        str(config["battles"]),
        "--team",
        str(config["team"]),
        "--baseline",
        str(config["baseline"]),
        "--label",
        label,
    ]
    if no_setsample:
        cmd.append("--no-setsample")
    return cmd


def compare_command(config: dict[str, object]) -> list[str]:
    return [str(config["venvPython"]), str(config["evalScript"]), "--compare", "frozen", "candidate"]


def _display_eval_command(
    venv_python: str,
    config: dict[str, object],
    *,
    label: str = "candidate",
    no_setsample: bool = False,
) -> str:
    cmd = [
        venv_python,
        "infrastructure/offline_eval.py",
        "--battles",
        str(config["battles"]),
        "--team",
        str(config["team"]),
        "--baseline",
        str(config["baseline"]),
        "--label",
        label,
    ]
    if no_setsample:
        cmd.append("--no-setsample")
    return _quote_command(cmd)


def provisioning_commands(root: Path, config: dict[str, object]) -> dict[str, list[str]]:
    req_windows = _relative(root, Path(config["requirementsEval"]))
    req_posix = req_windows.replace("\\", "/")
    showdown_dir = Path(config["showdownDir"])
    showdown_windows = str(showdown_dir)
    showdown_posix = str(showdown_dir).replace("\\", "/")
    showdown_url = "https://github.com/smogon/pokemon-showdown.git"
    return {
        "windows": [
            "py -3 -m venv .venv-eval",
            r".venv-eval\Scripts\python.exe -m pip install --upgrade pip",
            rf".venv-eval\Scripts\python.exe -m pip install -r {req_windows}",
            rf"if (!(Test-Path -LiteralPath {_ps_quote(showdown_windows)})) {{ git clone {showdown_url} {_ps_quote(showdown_windows)} }}",
            rf"Push-Location -LiteralPath {_ps_quote(showdown_windows)}; npm ci; Pop-Location",
            rf"Push-Location -LiteralPath {_ps_quote(showdown_windows)}; node pokemon-showdown start --no-security {config['showdownPort']}; Pop-Location",
            _display_eval_command(r".venv-eval\Scripts\python.exe", config, label="frozen", no_setsample=True),
            "python infrastructure/offline_eval_readiness.py --require-ready",
        ],
        "posix": [
            "python3 -m venv .venv-eval",
            ".venv-eval/bin/python -m pip install --upgrade pip",
            f".venv-eval/bin/python -m pip install -r {req_posix}",
            f"test -d {_sh_quote(showdown_posix)} || git clone {showdown_url} {_sh_quote(showdown_posix)}",
            f"cd {_sh_quote(showdown_posix)} && npm ci",
            f"cd {_sh_quote(showdown_posix)} && node pokemon-showdown start --no-security {config['showdownPort']}",
            _display_eval_command(".venv-eval/bin/python", config, label="frozen", no_setsample=True),
            "python3 infrastructure/offline_eval_readiness.py --require-ready",
        ],
    }


def _check_executable(command: str) -> tuple[bool, dict[str, object]]:
    executable = shutil.which(command)
    if not executable:
        return False, {"command": command, "found": False}
    probe = subprocess.run(
        [executable, "--version"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=10,
    )
    detail = {
        "command": command,
        "path": executable,
        "returncode": probe.returncode,
        "stdout": (probe.stdout or "").strip(),
        "stderr": (probe.stderr or "").strip()[-500:],
    }
    return probe.returncode == 0, detail


def _read_showdown_package(package_json: Path) -> dict[str, object]:
    if not package_json.exists():
        return {}
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"packageJsonError": str(exc)}
    return {
        "packageName": data.get("name"),
        "packageVersion": data.get("version"),
        "engines": data.get("engines"),
    }


def _check_imports(venv_python: Path) -> tuple[bool, dict[str, object]]:
    probe = subprocess.run(
        [
            str(venv_python),
            "-c",
            "import json, poke_env, websockets; "
            "print(json.dumps({'poke_env': getattr(poke_env, '__version__', 'unknown'), "
            "'websockets': getattr(websockets, '__version__', 'unknown')}))",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
    )
    if probe.returncode != 0:
        return False, {
            "returncode": probe.returncode,
            "stdout": (probe.stdout or "")[-500:],
            "stderr": (probe.stderr or "")[-500:],
        }
    try:
        return True, json.loads(probe.stdout.strip().splitlines()[-1])
    except Exception:
        return True, {"stdout": probe.stdout.strip()}


def _check_fouler_runtime_imports(root: Path, env: Mapping[str, str]) -> tuple[bool, dict[str, object]]:
    probe_code = (
        "import json, sys; "
        + "; ".join(f"import {module}" for module in FOULER_RUNTIME_IMPORTS)
        + "; print(json.dumps({'executable': sys.executable}))"
    )
    failures = []
    for command in _runtime_python_candidates(root, env):
        try:
            probe = subprocess.run(
                [*command, "-c", probe_code],
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=30,
            )
        except Exception as exc:
            detail = {"command": _quote_command(command), "error": str(exc)}
            failures.append(detail)
            if env.get("FOULER_RUNTIME_PYTHON"):
                break
            continue
        detail = {
            "command": _quote_command(command),
            "returncode": probe.returncode,
            "stdout": (probe.stdout or "").strip()[-500:],
            "stderr": (probe.stderr or "").strip()[-500:],
        }
        if probe.returncode == 0:
            try:
                detail.update(json.loads((probe.stdout or "").strip().splitlines()[-1]))
            except Exception:
                pass
            return True, detail
        failures.append(detail)
        if env.get("FOULER_RUNTIME_PYTHON"):
            break
    return False, {
        "requiredImports": list(FOULER_RUNTIME_IMPORTS),
        "failures": failures,
    }


def _check_showdown_server(port: int) -> tuple[bool, dict[str, object]]:
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=2.0):
            return True, {"host": "127.0.0.1", "port": int(port), "status": "tcp-open"}
    except Exception as exc:
        return False, {
            "host": "127.0.0.1",
            "port": int(port),
            "status": "unreachable",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _read_json_file(path: Path) -> object | None:
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    for encoding in ("utf-8-sig", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            return json.loads(raw.decode(encoding))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
    return None


def read_pid_payload(path: Path) -> dict[str, object] | int | None:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None
    if not raw:
        return None
    try:
        if raw.startswith("{"):
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else None
        return int(raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _process_state(pid: int | None) -> dict[str, object]:
    if not pid:
        return {"pid": pid, "running": False, "status": "missing-pid"}
    if psutil is None:
        return {"pid": pid, "running": None, "status": "psutil-unavailable"}
    try:
        proc = psutil.Process(int(pid))
        status = proc.status() if hasattr(proc, "status") else ""
        running = bool(proc.is_running()) and status != getattr(psutil, "STATUS_ZOMBIE", "zombie")
        return {
            "pid": int(pid),
            "running": running,
            "status": status or "running",
            "cwd": proc.cwd(),
            "commandSummary": " ".join(proc.cmdline()[:5]),
        }
    except psutil.NoSuchProcess:
        return {"pid": int(pid), "running": False, "status": "dead"}
    except psutil.AccessDenied:
        return {"pid": int(pid), "running": None, "status": "access-denied"}
    except Exception as exc:
        return {"pid": int(pid), "running": None, "status": f"{type(exc).__name__}: {exc}"}


def _pid_from_payload(payload: object) -> int | None:
    if isinstance(payload, dict):
        value = payload.get("pid")
    else:
        value = payload
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _offline_status_preconditions() -> list[str]:
    return list(OFFLINE_STATUS_FINITE_PRECONDITIONS)


def _offline_status_disposition(
    *,
    stage: str,
    running_stage: bool,
    server_stopped: bool,
    server_state: dict[str, object] | None,
) -> dict[str, object]:
    if running_stage and not server_stopped:
        if server_state and server_state.get("running") is True:
            return {
                "state": "adopted",
                "classification": "adopted-running-offline-eval",
                "proofUse": "in-progress-offline-eval-owner",
                "finiteLeasePreconditions": _offline_status_preconditions(),
            }
        if server_state and server_state.get("running") is False:
            return {
                "state": "blocked",
                "classification": "blocked-stale-running-offline-eval-status",
                "proofUse": "not-active-eval-proof",
                "finiteLeasePreconditions": _offline_status_preconditions(),
            }
        return {
            "state": "blocked",
            "classification": "blocked-uninspectable-running-offline-eval-status",
            "proofUse": "not-active-eval-proof",
            "finiteLeasePreconditions": _offline_status_preconditions(),
        }
    return {
        "state": "archived" if stage else "idle",
        "classification": "archived-offline-eval-status" if stage else "missing-offline-eval-status",
        "proofUse": "not-active-eval-proof",
        "finiteLeasePreconditions": _offline_status_preconditions(),
    }


def _check_bot_pid_lock(root: Path) -> tuple[bool, dict[str, object]]:
    path = root / ".bot.pid"
    detail: dict[str, object] = {
        "pidFile": _relative(root, path),
        "exists": path.exists(),
    }
    if not path.exists():
        detail["state"] = "absent"
        return True, detail

    raw_payload = read_pid_payload(path)
    pid = _pid_from_payload(raw_payload)
    detail["pid"] = pid
    if isinstance(raw_payload, dict):
        detail["payloadStartedAt"] = raw_payload.get("startedAt") or raw_payload.get("started_at")
        detail["payloadCommand"] = raw_payload.get("command")
    if not pid:
        detail["state"] = "stale-corrupt-pid-file"
        detail["cleanupRecommended"] = True
        return True, detail

    state = _process_state(pid)
    detail["process"] = state
    if state.get("running") is False:
        detail["state"] = "stale-dead-pid"
        detail["cleanupRecommended"] = True
        return True, detail
    if state.get("running") is None:
        detail["state"] = "uninspectable-pid"
        return False, detail

    command = str(state.get("commandSummary") or "").lower()
    cwd = str(state.get("cwd") or "")
    cwd_matches = bool(cwd) and os.path.abspath(cwd) == os.path.abspath(root)
    is_fouler_runner = cwd_matches and (
        "run.py" in command or "offline_eval_runner.py" in command
    ) and (
        "showdown" in command
        or "search_ladder" in command
        or "accept_challenge" in command
        or "challenge_user" in command
    )
    detail["cwdMatchesRepo"] = cwd_matches
    detail["isFoulerRunner"] = is_fouler_runner
    if is_fouler_runner:
        detail["state"] = "live-fouler-runner"
        return False, detail

    detail["state"] = "stale-reused-pid"
    detail["cleanupRecommended"] = True
    return True, detail


def _check_offline_status_artifacts(results_dir: Path) -> tuple[bool, dict[str, object]]:
    artifacts: list[dict[str, object]] = []
    stale_running: list[dict[str, object]] = []
    adopted_running: list[dict[str, object]] = []
    blocked_running: list[dict[str, object]] = []
    if not results_dir.exists():
        return True, {
            "resultsDir": str(results_dir),
            "exists": False,
            "artifacts": artifacts,
            "finiteLeasePreconditions": _offline_status_preconditions(),
        }

    for path in sorted(results_dir.glob("*-status.json")):
        parsed = _read_json_file(path)
        artifact: dict[str, object] = {
            "path": str(path),
            "name": path.name,
            "validJson": isinstance(parsed, dict),
        }
        if not isinstance(parsed, dict):
            artifacts.append(artifact)
            continue
        stage = str(parsed.get("stage") or "").strip()
        server_pid = _pid_from_payload(parsed.get("serverPid") or parsed.get("server_pid"))
        artifact.update({
            "stage": stage,
            "serverPid": server_pid,
            "serverStopped": parsed.get("serverStopped"),
            "updatedAt": parsed.get("updatedAt") or parsed.get("updated_at"),
        })
        running_stage = stage in RUNNING_STATUS_STAGES or stage.startswith("running-")
        server_state = None
        server_stopped = parsed.get("serverStopped") is True or parsed.get("server_stopped") is True
        if running_stage:
            server_state = _process_state(server_pid)
            artifact["serverProcess"] = server_state
            if not server_stopped and server_state.get("running") is True:
                adopted_running.append(artifact)
            elif not server_stopped and server_state.get("running") is False:
                stale_running.append(artifact)
            elif not server_stopped:
                blocked_running.append(artifact)
        artifact["disposition"] = _offline_status_disposition(
            stage=stage,
            running_stage=running_stage,
            server_stopped=server_stopped,
            server_state=server_state,
        )
        if artifact["disposition"].get("state") == "blocked":
            blocked_running.append(artifact)
        artifacts.append(artifact)

    blocking = []
    for item in [*stale_running, *adopted_running, *blocked_running]:
        if item not in blocking:
            blocking.append(item)
    return not blocking, {
        "resultsDir": str(results_dir),
        "exists": True,
        "artifacts": artifacts,
        "staleRunning": stale_running,
        "adoptedRunning": adopted_running,
        "blockingRunning": blocking,
        "finiteLeasePreconditions": _offline_status_preconditions(),
    }


def _frozen_baseline_detail(path: Path, min_battles: int) -> tuple[bool, dict[str, object]]:
    if not path.exists():
        return False, {"exists": False}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, {"exists": True, "validJson": False, "error": str(exc)}
    required = ["label", "battles", "fouler_wins", "fouler_win_rate", "fouler_wilson_lcb"]
    missing = [key for key in required if key not in data]
    try:
        battles = int(data.get("battles", 0))
    except (TypeError, ValueError):
        battles = 0
    enough_battles = battles >= min_battles
    return not missing and enough_battles, {
        "exists": True,
        "validJson": True,
        "missingFields": missing,
        "label": data.get("label"),
        "battles": battles,
        "minBattles": min_battles,
        "enoughBattles": enough_battles,
        "fouler_win_rate": data.get("fouler_win_rate"),
        "fouler_wilson_lcb": data.get("fouler_wilson_lcb"),
    }


def build_readiness_payload(
    *,
    root: Path = PROJECT_ROOT,
    env: Mapping[str, str] | None = None,
    run_import_check: bool = True,
    run_server_check: bool = True,
    run_prereq_check: bool = True,
) -> dict[str, object]:
    env = os.environ if env is None else env
    config = configured_eval(root, env)
    blockers: list[str] = []
    checks: list[dict[str, object]] = []

    def add_check(name: str, ok: bool, detail: object, remediation: str = "") -> None:
        checks.append({"name": name, "ok": ok, "detail": detail, "remediation": remediation})
        if not ok:
            blockers.append(f"{name}: {remediation or detail}")

    eval_script = Path(config["evalScript"])
    baseline_script = Path(config["baselineScript"])
    requirements_eval = Path(config["requirementsEval"])
    venv_python = Path(config["venvPython"])
    team_file = Path(config["teamFile"])
    frozen_baseline = Path(config["frozenBaseline"])
    results_dir = Path(config["resultsDir"])
    showdown_dir = Path(config["showdownDir"])
    showdown_package = Path(config["showdownPackage"])
    showdown_launcher = Path(config["showdownLauncher"])
    showdown_node_modules = Path(config["showdownNodeModules"])

    add_check(
        "offline_eval.py",
        eval_script.exists(),
        _relative(root, eval_script),
        "restore infrastructure/offline_eval.py before enabling auto-improvement",
    )
    add_check(
        "_offline_baseline.py",
        baseline_script.exists(),
        _relative(root, baseline_script),
        "restore infrastructure/_offline_baseline.py before enabling auto-improvement",
    )
    add_check(
        "requirements-eval.txt",
        requirements_eval.exists(),
        _relative(root, requirements_eval),
        "restore infrastructure/requirements-eval.txt and install it into .venv-eval",
    )
    add_check(
        "eval venv python",
        venv_python.exists(),
        _relative(root, venv_python),
        "create .venv-eval and install infrastructure/requirements-eval.txt",
    )
    add_check(
        "configured team file",
        team_file.exists(),
        _relative(root, team_file),
        f"set IMPROVE_AGENT_EVAL_TEAM to an existing team or restore {_relative(root, team_file)}",
    )
    pid_ok, pid_detail = _check_bot_pid_lock(root)
    add_check(
        "fouler bot pid lock",
        pid_ok,
        pid_detail,
        "stop the live Fouler runner or clear an uninspectable .bot.pid before running offline eval",
    )
    status_ok, status_detail = _check_offline_status_artifacts(results_dir)
    add_check(
        "offline eval status artifacts",
        status_ok,
        status_detail,
        "wait for adopted eval status to finish, or archive/replace stale running status from a fresh finite offline eval sidecar",
    )

    if run_prereq_check:
        for command, remediation in [
            ("node", "install Node.js 16+ before provisioning Pokemon Showdown"),
            ("npm", "install npm before running npm ci in the Pokemon Showdown checkout"),
            ("git", "install git or manually place a Pokemon Showdown checkout at POKEMON_SHOWDOWN_DIR"),
        ]:
            try:
                command_ok, command_detail = _check_executable(command)
            except Exception as exc:
                command_ok, command_detail = False, {"command": command, "error": str(exc)}
            add_check(f"{command} executable", command_ok, command_detail, remediation)

        package_detail = _read_showdown_package(showdown_package)
        checkout_ok = showdown_dir.exists() and showdown_package.exists() and showdown_launcher.exists()
        add_check(
            "pokemon-showdown checkout",
            checkout_ok,
            {
                "dir": str(showdown_dir),
                "dirExists": showdown_dir.exists(),
                "packageJson": str(showdown_package),
                "packageJsonExists": showdown_package.exists(),
                "launcher": str(showdown_launcher),
                "launcherExists": showdown_launcher.exists(),
                **package_detail,
            },
            "set POKEMON_SHOWDOWN_DIR to an existing checkout or run the git clone command from provisionCommands",
        )
        if checkout_ok:
            add_check(
                "pokemon-showdown dependencies",
                showdown_node_modules.exists(),
                {
                    "nodeModules": str(showdown_node_modules),
                    "nodeModulesExists": showdown_node_modules.exists(),
                    "installCommand": f"cd {showdown_dir} && npm ci",
                },
                "run npm ci in POKEMON_SHOWDOWN_DIR before starting the local no-security eval server",
            )
    else:
        add_check("showdown provisioning prerequisites", True, {"skipped": "prereq check disabled"})

    if venv_python.exists() and run_import_check:
        try:
            imports_ok, import_detail = _check_imports(venv_python)
        except Exception as exc:
            imports_ok, import_detail = False, {"error": str(exc)}
        add_check(
            "eval venv imports",
            imports_ok,
            import_detail,
            "run the pip install command from provisionCommands for .venv-eval",
        )
    elif venv_python.exists():
        add_check("eval venv imports", True, {"skipped": "import check disabled"})

    if run_import_check:
        try:
            runtime_ok, runtime_detail = _check_fouler_runtime_imports(root, env)
        except Exception as exc:
            runtime_ok, runtime_detail = False, {"error": str(exc)}
        add_check(
            "fouler runtime imports",
            runtime_ok,
            runtime_detail,
            "set FOULER_RUNTIME_PYTHON to a Python that can import requirements.txt or install requirements.txt into the selected runtime",
        )
    else:
        add_check("fouler runtime imports", True, {"skipped": "import check disabled"})

    if run_server_check:
        server_ok, server_detail = _check_showdown_server(int(config["showdownPort"]))
        add_check(
            "local showdown eval server",
            server_ok,
            server_detail,
            f"start a local no-security Pokemon Showdown server: node pokemon-showdown start --no-security {config['showdownPort']}",
        )
    else:
        add_check("local showdown eval server", True, {"skipped": "server check disabled"})

    frozen_ok, frozen_detail = _frozen_baseline_detail(frozen_baseline, int(config["battles"]))
    add_check(
        "frozen baseline proof",
        frozen_ok,
        frozen_detail,
        "run the frozen baseline command for at least IMPROVE_AGENT_EVAL_BATTLES and verify eval_results/offline/frozen.json",
    )

    ready = not blockers
    paths = {
        key: _relative(root, Path(value))
        for key, value in config.items()
        if isinstance(value, Path)
    }
    command_config = {key: value for key, value in config.items() if not isinstance(value, Path)}
    commands = {
        "candidateEval": _quote_command(eval_command(config)),
        "frozenBaseline": _quote_command(eval_command(config, label="frozen", no_setsample=True)),
        "compareFrozenVsCandidate": _quote_command(compare_command(config)),
        "readiness": "python infrastructure/offline_eval_readiness.py --require-ready",
        "showdownInstall": f"cd {showdown_dir} && npm ci",
        "showdownServerCwd": str(showdown_dir),
        "showdownServer": f"node pokemon-showdown start --no-security {config['showdownPort']}",
    }
    return {
        "schemaVersion": SCHEMA_VERSION,
        "ready": ready,
        "recursiveImprovementReady": ready,
        "blockers": blockers,
        "checks": checks,
        "configuration": command_config,
        "paths": paths,
        "commands": commands,
        "provisionCommands": provisioning_commands(root, config),
        "proofRequired": [
            "offline_eval_readiness.py --require-ready exits 0 with ready=true",
            "node, npm, and git are available for Pokemon Showdown provisioning",
            "POKEMON_SHOWDOWN_DIR or the default sibling pokemon-showdown checkout has package.json, the pokemon-showdown launcher, and installed node_modules",
            ".venv-eval python can import poke_env and websockets",
            "Fouler runtime Python can import the run.py dependencies from requirements.txt",
            "a local no-security Pokemon Showdown server is reachable on EVAL_SHOWDOWN_PORT",
            ".bot.pid is absent, stale-cleanable, or points to no live Fouler runner",
            "eval_results/offline/*-status.json is archived, adopted by a live finite sidecar, or blocked before a new eval starts",
            "infrastructure/offline_eval.py and infrastructure/_offline_baseline.py are present",
            "eval_results/offline/frozen.json exists, meets IMPROVE_AGENT_EVAL_BATTLES, and contains label, battles, fouler_wins, fouler_win_rate, and fouler_wilson_lcb",
            "after an accepted candidate run, eval_results/offline/candidate.json and compare-frozen-vs-candidate.json provide the eval verdict consumed by improve_agent",
        ],
        "note": "Read-only check. It does not start Pokemon Showdown, Discord posting, ladder battles, HERMES/DEKU, or services.",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only offline eval gate readiness doctor")
    parser.add_argument("--require-ready", action="store_true", help="exit non-zero unless recursiveImprovementReady is true")
    parser.add_argument("--skip-import-check", action="store_true", help="do not execute .venv-eval python import probe")
    parser.add_argument("--skip-server-check", action="store_true", help="do not probe the local Pokemon Showdown eval server port")
    parser.add_argument("--skip-prereq-check", action="store_true", help="do not probe Node/npm/git or Pokemon Showdown checkout metadata")
    args = parser.parse_args(argv)

    payload = build_readiness_payload(
        run_import_check=not args.skip_import_check,
        run_server_check=not args.skip_server_check,
        run_prereq_check=not args.skip_prereq_check,
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 1 if args.require_ready and not payload["recursiveImprovementReady"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
