#!/usr/bin/env python3
"""
Batch-triggered coding agent — the recursive improvement step.

Called after each batch completes.  Reads the latest autoresearch report
(with grounding blocks from PokedexOracle), picks the top issue, asks
Claude to write ONE targeted fix, applies it, runs tests, and commits
if passing.  The ELO watchdog reverts if the fix hurts.

DORMANT (2026-07-15): this engine self-improvement loop remains explicit opt-in
and has no scheduled servicer. A discriminating candidate-vs-frozen gate is now
installed, but automatic operation stays parked until that harness passes its
operational smoke proof and the owner intentionally enables the supervisor
sentinel. Per-battle "replay review required" prompts remain suppressed (see
IMPROVE_LOOP_PARKED_NOTE in infrastructure/discord_reporting.py).

Usage:
    python infrastructure/improve_agent.py --enable-auto-improve
    python infrastructure/improve_agent.py --dry-run  # show what would change, don't apply
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.devstream_runtime_lease import (  # noqa: E402
    RUNTIME_LEASE_PATH_ENV,
    validate_runtime_lease,
)
from infrastructure.deployment_state import current_deployment_context  # noqa: E402
from infrastructure.head_to_head_authority import (  # noqa: E402
    DEFAULT_AUTHORITY_PATH,
    consume_improve_authorization,
    load_ledger_authority,
)
from infrastructure.head_to_head_proof import load_latest_proof  # noqa: E402
from infrastructure.offline_eval import resolve_fouler_python  # noqa: E402

try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except ImportError:
    pass

AUTORESEARCH_PATH = PROJECT_ROOT / "replay_analysis" / "autoresearch_latest.json"
GUARDRAILS_PATH = PROJECT_ROOT / "infrastructure" / "guardrails.json"

# Files the agent is allowed to modify
ALLOWED_TARGETS = (
    "fp/search/main.py",
    "fp/search/eval.py",
    "fp/search/forced_lines.py",
    "fp/search/endgame.py",
    "fp/playstyle_config.py",
    "fp/team_analysis.py",
    "fp/opponent_model.py",
)

# Max lines of code context to send (keep prompt focused)
MAX_CODE_LINES = 500

MODEL = os.getenv("IMPROVE_AGENT_MODEL", "claude-sonnet-4-20250514")
BATTLE_ID_RE = re.compile(r"\b(?:battle-)?gen\d+[a-z0-9]*-\d+(?:-[a-z0-9]+)?\b", re.IGNORECASE)
MECHANICS_TERMS_RE = re.compile(
    r"\b(type|types|ability|abilities|move|moves|damage|weak|resist|immune|immunity|speed|hazard|terrain|weather)\b",
    re.IGNORECASE,
)
TRUSTED_GROUNDING_SOURCE_RE = re.compile(
    r"\b(showdown|replay|trace|pokedex|oracle|poke[-_ ]?engine|smogon|protocol|team\s*file|data/)\b",
    re.IGNORECASE,
)
UNTRUSTED_GROUNDING_SOURCE_RE = re.compile(
    r"\b(llm|model|claude|chatgpt|memory|assumption|prose|opinion|guess)\b",
    re.IGNORECASE,
)
TRACE_ONLY_DECISION_RE = re.compile(
    r"\b(decision[_ -]?instability|decision trace|fallback|timeout|repeated same action|loop)\b",
    re.IGNORECASE,
)
MECHANICS_OR_MATCHUP_RE = re.compile(
    r"\b(type|ability|damage|weak|resist|immune|immunity|terrain|weather|tera|hazard pressure|speed tier|coverage)\b",
    re.IGNORECASE,
)
SOURCE_POLICY_TARGET_RE = re.compile(
    r"^(?:fp/(?:search|eval|policy)/|fp/(?:hybrid_policy|run_battle)\.py)",
    re.IGNORECASE,
)
REPLAY_PROTOCOL_EVIDENCE_RE = re.compile(
    r"(\|request\||\|move\||\|switch\||\|turn\||\|win\||showdown[-_ ]?request|showdown[-_ ]?protocol|replay[_ -]?json|requesthash)",
    re.IGNORECASE,
)
LEGAL_OPTION_EVIDENCE_RE = re.compile(
    r"(\|request\||showdown[-_ ]?request|battle[_ -]?request|requesthash|legal[_ -]?options?|legal[_ -]?moves?|legal[_ -]?switch(?:es)?|candidate[_ -]?set)",
    re.IGNORECASE,
)
REQUEST_HASH_RE = re.compile(r"\brequestHash=([a-f0-9]{64})\b", re.IGNORECASE)
LEGAL_COUNT_RE = re.compile(r"\blegal(?:Moves|Switches)=(\d+)\b")

# --- Accepted-change and deployment lineage ---
# A commit is not a deployment. Accepted candidates carry their H2H proof identity
# in Git trailers and an immutable receipt. Deploy spacing continues to read only
# immutable activation and judgment receipts written by the runtime operator.
BATTLE_STATS_PATH = PROJECT_ROOT / "battle_stats.json"
IMPROVE_LEDGER_PATH = PROJECT_ROOT / "eval_results" / "improve_ledger.jsonl"
IMPROVE_LOCK_PATH = PROJECT_ROOT / ".pids" / "improve-agent.lock"
IMPROVE_RECOVERY_BLOCK_PATH = PROJECT_ROOT / ".pids" / "improve-agent-recovery-block.json"
ACCEPTED_COMMIT_RECEIPT_ROOT_ENV = "FOULER_ACCEPTED_COMMIT_RECEIPT_ROOT"
ACCEPTED_COMMIT_RECEIPT_ROOT = Path(
    os.getenv(
        ACCEPTED_COMMIT_RECEIPT_ROOT_ENV,
        str(Path.home() / ".deku" / "state" / "fouler" / "accepted-commits"),
    )
).expanduser()
AUTO_IMPROVE_SENTINEL = "FOULER_PLAY_ENABLE_AUTO_IMPROVE"
AUTO_PUSH_SENTINEL = "FOULER_PLAY_ENABLE_AUTO_PUSH"
PUSH_REMOTE_ENV = "IMPROVE_AGENT_PUSH_REMOTE"
PUSH_BRANCH_ENV = "IMPROVE_AGENT_PUSH_BRANCH"
TRUTHY_ENV_VALUES = {"1", "true", "yes", "on"}
DEPLOY_WIN_RATE_SAMPLE_BATTLES = 30
CONTROL_PLANE_NAMES = ("deku", "ubunztu")
REQUIRED_NEVER_MODIFY = frozenset(
    {
        "config.py",
        "run.py",
        ".env",
        "CREDENTIALS.md",
        "teams/**/*",
    }
)
MIN_EVAL_SEARCH_TIME_MS = 1200
MIN_EVAL_PER_BATTLE_TIMEOUT_SECONDS = 240.0
IMMUTABLE_JIGGLY_RELEASE_RE = re.compile(
    r"(?i)(?:^|[\\/])releases[\\/]fouler-play[\\/][0-9a-f]{40,64}(?:$|[\\/])"
)


def env_flag_enabled(name: str) -> bool:
    return str(os.getenv(name, "")).strip().lower() in TRUTHY_ENV_VALUES


def auto_improve_enabled(cli_enabled: bool = False) -> bool:
    return bool(cli_enabled or env_flag_enabled(AUTO_IMPROVE_SENTINEL))


def auto_push_enabled(cli_enabled: bool = False) -> bool:
    return bool(cli_enabled or env_flag_enabled(AUTO_PUSH_SENTINEL))


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_json_bytes(payload: dict) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _write_immutable_json(path: Path, payload: dict) -> None:
    """Create a receipt once; never replace an existing lineage artifact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    try:
        with os.fdopen(descriptor, "wb", closefd=True) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        path.unlink(missing_ok=True)
        raise
    try:
        os.chmod(path, 0o444)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def write_improve_recovery_block(reason: str, detail: dict | None = None) -> dict:
    payload = {
        "schemaVersion": "fouler-improve-recovery-block/v1",
        "blockedAt": datetime.now(timezone.utc).isoformat(),
        "reason": reason,
        "detail": detail or {},
        "requiredAction": "inspect the candidate checkout and explicitly clear this block before any live battle start",
    }
    _atomic_write_json(IMPROVE_RECOVERY_BLOCK_PATH, payload)
    return payload


def improvement_checkout_guard() -> dict:
    """Fail closed when an interrupted improvement may own or have changed engine code."""
    blockers: list[str] = []
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", "fp"],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        status = None
        blockers.append(f"engine checkout status failed: {type(exc).__name__}: {exc}")
    dirty_engine = []
    if status is not None:
        dirty_engine = [line for line in status.stdout.splitlines() if line.strip()]
        if status.returncode:
            blockers.append("engine checkout status command failed")
        elif dirty_engine:
            blockers.append("engine checkout has uncommitted or untracked changes")
    if IMPROVE_LOCK_PATH.exists():
        blockers.append("improve-agent lock exists")
    if IMPROVE_RECOVERY_BLOCK_PATH.exists():
        blockers.append("improve-agent recovery block exists")
    patch_artifact = PROJECT_ROOT / ".agent_diff.patch"
    if patch_artifact.exists():
        blockers.append("stale improve-agent patch artifact exists")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "dirtyEngineEntries": dirty_engine,
        "lockPath": str(IMPROVE_LOCK_PATH),
        "lockExists": IMPROVE_LOCK_PATH.exists(),
        "recoveryBlockPath": str(IMPROVE_RECOVERY_BLOCK_PATH),
        "recoveryBlockExists": IMPROVE_RECOVERY_BLOCK_PATH.exists(),
        "patchArtifactExists": patch_artifact.exists(),
    }


def acquire_improve_lock(target_file: str) -> tuple[str | None, dict]:
    token = uuid.uuid4().hex
    payload = {
        "schemaVersion": "fouler-improve-lock/v1",
        "pid": os.getpid(),
        "token": token,
        "targetFile": target_file.replace("\\", "/"),
        "acquiredAt": datetime.now(timezone.utc).isoformat(),
    }
    IMPROVE_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(IMPROVE_LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return None, {"error": "improve_agent_lock_exists", "path": str(IMPROVE_LOCK_PATH)}
    except OSError as exc:
        return None, {"error": "improve_agent_lock_failed", "detail": str(exc)}
    try:
        os.write(descriptor, (json.dumps(payload, sort_keys=True) + "\n").encode("utf-8"))
    finally:
        os.close(descriptor)
    return token, payload


def release_improve_lock(token: str) -> bool:
    try:
        payload = json.loads(IMPROVE_LOCK_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        write_improve_recovery_block("improve lock could not be verified during release", {"detail": str(exc)})
        return False
    if payload.get("token") != token:
        write_improve_recovery_block(
            "improve lock ownership changed during candidate transaction",
            {"expectedToken": token, "observedToken": payload.get("token")},
        )
        return False
    try:
        IMPROVE_LOCK_PATH.unlink()
    except OSError as exc:
        write_improve_recovery_block("improve lock could not be released", {"detail": str(exc)})
        return False
    return True


def explicit_push_target(push_remote: str | None = None, push_branch: str | None = None) -> tuple[str, str]:
    remote = (push_remote or os.getenv(PUSH_REMOTE_ENV) or "").strip()
    branch = (push_branch or os.getenv(PUSH_BRANCH_ENV) or "").strip()
    if not remote or not branch:
        raise ValueError(
            f"git push requires explicit --push-remote/--push-branch or "
            f"{PUSH_REMOTE_ENV}/{PUSH_BRANCH_ENV}; no default push target is allowed"
        )
    if remote.lower() == "origin" and branch.lower() == "master":
        raise ValueError("refusing unsafe push target origin master")
    return remote, branch


def load_guardrails() -> dict:
    try:
        return json.loads(GUARDRAILS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _normalized_target(target_file: str) -> str:
    raw = str(target_file or "").strip().replace("\\", "/")
    path = PurePosixPath(raw)
    if not raw or path.is_absolute() or ".." in path.parts or raw != path.as_posix():
        raise ValueError("candidate target must be a normalized repository-relative path")
    return raw


def _guardrail_match(path: str, pattern: object) -> bool:
    normalized = str(pattern or "").strip().replace("\\", "/")
    return bool(normalized) and (path == normalized or PurePosixPath(path).match(normalized))


def mutation_policy_blockers(target_file: str) -> list[str]:
    """Return immutable policy blockers for every autonomous source mutation."""

    try:
        target = _normalized_target(target_file)
    except ValueError as exc:
        return [str(exc)]
    blockers: list[str] = []
    if target not in ALLOWED_TARGETS:
        blockers.append(f"candidate target is outside the autonomous allowlist: {target}")

    guardrails = load_guardrails()
    allowed = guardrails.get("allowed_modify") if isinstance(guardrails, dict) else None
    denied = guardrails.get("never_modify") if isinstance(guardrails, dict) else None
    safety = guardrails.get("safety") if isinstance(guardrails, dict) else None
    if not isinstance(allowed, list) or not any(_guardrail_match(target, item) for item in allowed):
        blockers.append(f"candidate target is not allowed by {GUARDRAILS_PATH.name}: {target}")
    if not isinstance(denied, list):
        blockers.append("guardrail never_modify policy is missing")
    else:
        normalized_denied = {
            str(item).strip().replace("\\", "/")
            for item in denied
            if str(item).strip()
        }
        missing_denials = sorted(REQUIRED_NEVER_MODIFY - normalized_denied)
        if missing_denials:
            blockers.append(
                "guardrail never_modify policy is weakened; missing: "
                + ", ".join(missing_denials)
            )
        if any(_guardrail_match(target, item) for item in denied):
            blockers.append(f"candidate target is protected by never_modify: {target}")
    if not isinstance(safety, dict):
        blockers.append("guardrail safety policy is missing")
    else:
        if safety.get("require_test_pass") is not True:
            blockers.append("guardrail test gate must remain explicitly enabled")
        if safety.get("require_syntax_check") is not True:
            blockers.append("guardrail syntax gate must remain explicitly enabled")
        try:
            minimum_games = int(safety.get("min_games_between_deploys"))
        except (TypeError, ValueError):
            minimum_games = 0
        if minimum_games < 30:
            blockers.append("guardrail deployment spacing must remain at least 30 decisive battles")
        try:
            maximum_elo_drop = float(safety.get("max_elo_drop_before_revert"))
        except (TypeError, ValueError):
            maximum_elo_drop = 0.0
        if not 0 < maximum_elo_drop <= 50.0:
            blockers.append("guardrail ELO stop-loss must remain within (0, 50]")

    root_text = str(PROJECT_ROOT.resolve(strict=False))
    if IMMUTABLE_JIGGLY_RELEASE_RE.search(root_text):
        blockers.append("immutable JIGGLYPUFF release checkout cannot be mutated")
    candidate_path = PROJECT_ROOT / target
    full_path = candidate_path.resolve(strict=False)
    try:
        full_path.relative_to(PROJECT_ROOT.resolve(strict=False))
    except ValueError:
        blockers.append("candidate target escapes the control checkout")
    path_cursor = PROJECT_ROOT
    linked_component = False
    for part in PurePosixPath(target).parts:
        path_cursor /= part
        linked_component = linked_component or path_cursor.is_symlink()
    if linked_component:
        blockers.append("candidate target and its parent directories must not be symlinks")
    return list(dict.fromkeys(blockers))


def _git_identity(root: Path) -> tuple[str, str, str]:
    values: list[str] = []
    for arguments in (("rev-parse", "--show-toplevel"), ("rev-parse", "HEAD"), ("rev-parse", "HEAD^{tree}")):
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if result.returncode:
            raise RuntimeError(f"git {' '.join(arguments)} failed: {(result.stderr or result.stdout).strip()}")
        values.append(result.stdout.strip())
    return values[0], values[1], values[2]


def _is_deku_control_name(value: object) -> bool:
    normalized = str(value or "").strip().lower().rstrip(".")
    first_label = normalized.split(".", 1)[0]
    return first_label in CONTROL_PLANE_NAMES


def improvement_control_checkout_guard(lease_guard: dict, *, requested_max_cycles: int) -> dict:
    """Bind mutation to one clean mutable DEKU checkout, never the JIGGLY runtime."""

    blockers: list[str] = []
    lease = lease_guard.get("lease") if isinstance(lease_guard.get("lease"), dict) else {}
    if not lease_guard.get("ok"):
        blockers.append("runtime lease validation did not succeed")
    if requested_max_cycles != 1 or lease.get("maxCycles") != 1:
        blockers.append("improve authorization must cover exactly one cycle")
    for field in ("machine", "hostName"):
        value = lease.get(field)
        if not value or not _is_deku_control_name(value):
            blockers.append(f"improve authorization {field} must bind the DEKU/ubunztu control plane")

    root = PROJECT_ROOT.resolve(strict=False)
    if IMMUTABLE_JIGGLY_RELEASE_RE.search(str(root)):
        blockers.append("improve agent refuses to run inside an immutable JIGGLYPUFF release")
    try:
        git_root, head, tree = _git_identity(root)
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        git_root, head, tree = "", "", ""
        blockers.append(f"DEKU control checkout identity is unavailable: {exc}")
    if git_root and Path(git_root).resolve(strict=False) != root:
        blockers.append("improve agent PROJECT_ROOT is not the Git control checkout root")
    if head and lease.get("sourceCommit") != head:
        blockers.append("improve authorization sourceCommit does not match DEKU control HEAD")
    if tree and lease.get("sourceTree") != tree:
        blockers.append("improve authorization sourceTree does not match DEKU control tree")
    if not os.access(root, os.W_OK):
        blockers.append("DEKU control checkout is not mutable by the improve agent")
    return {
        "ready": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
        "controlCheckout": str(root),
        "controlHead": head or None,
        "controlTree": tree or None,
        "leaseId": lease.get("id"),
        "authorizationDigest": lease.get("authorizationSha256"),
    }


def min_games_between_deploys() -> int:
    safety = load_guardrails().get("safety", {})
    try:
        return int(safety.get("min_games_between_deploys", 15))
    except (TypeError, ValueError):
        return 15


def _load_battles() -> list:
    try:
        data = json.loads(BATTLE_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, dict):
        data = data.get("battles", [])
    return data if isinstance(data, list) else []


def deployment_spacing_status() -> dict:
    """Return exact-identity activation/judgment state; missing proof fails closed."""
    context = current_deployment_context(
        battle_stats_path=BATTLE_STATS_PATH,
        verify_checkout=True,
        expected_runtime_identity={
            "sourceCommit": os.getenv("FOULER_SOURCE_COMMIT", ""),
            "changeId": os.getenv("FOULER_CHANGE_ID", ""),
            "deploymentId": os.getenv("FOULER_DEPLOYMENT_ID", ""),
            "runtimeLeaseId": os.getenv("FOULER_RUNTIME_LEASE_ID", ""),
            "runtimeAuthorizationSha256": os.getenv("FOULER_RUNTIME_AUTHORIZATION_SHA256", ""),
            "sessionId": os.getenv("FOULER_SESSION_ID", ""),
        },
    )
    minimum = min_games_between_deploys()
    games = int(context.get("gamesSinceActivation") or 0)
    blockers = list(context.get("blockers") or [])
    if not context.get("readyForImprovement"):
        blockers.append(
            "current deployment lacks a verified passing judgment receipt"
        )
    if games < minimum:
        blockers.append(
            f"current deployment has only {games}/{minimum} exact-identity decisive battles"
        )
    return {
        **context,
        "minimumGames": minimum,
        "gamesSinceActivation": games,
        "ready": not blockers,
        "blockers": list(dict.fromkeys(blockers)),
    }


def games_since_last_deploy() -> int:
    """Compatibility helper backed only by the verified current activation."""
    status = deployment_spacing_status()
    return int(status.get("gamesSinceActivation") or 0) if status.get("activation") else 0


def current_win_rate_snapshot(battles: list, sample_size: int = DEPLOY_WIN_RATE_SAMPLE_BATTLES) -> tuple[float | None, int]:
    """Return a recent decisive-battle win-rate snapshot for deploy attribution."""
    decisive = [b for b in battles if isinstance(b, dict) and b.get("result") in ("win", "loss")]
    if sample_size > 0:
        decisive = decisive[-sample_size:]
    if not decisive:
        return None, 0
    wins = sum(1 for b in decisive if b.get("result") == "win")
    return wins / len(decisive), len(decisive)


def _json_safe(value: object, *, max_chars: int = 4000) -> object:
    try:
        text = json.dumps(value, sort_keys=True, default=str)
    except TypeError:
        return str(value)[:max_chars]
    if len(text) <= max_chars:
        return value
    return {"truncated": True, "preview": text[:max_chars]}


def _ladder_snapshot() -> dict:
    battles = _load_battles()
    decisive = [b for b in battles if isinstance(b, dict) and b.get("result") in ("win", "loss")]
    recent = decisive[-20:]
    last = decisive[-1] if decisive else {}
    return {
        "latest_battle_id": last.get("battle_id") or last.get("battle_tag") or last.get("replay_id"),
        "current_elo": last.get("elo_after", last.get("rating")),
        "recent_sample": len(recent),
        "recent_wins": sum(1 for b in recent if b.get("result") == "win"),
        "recent_losses": sum(1 for b in recent if b.get("result") == "loss"),
    }


def append_improve_ledger(
    outcome: str,
    *,
    issue: str | None = None,
    target_file: str | None = None,
    detail: dict | None = None,
    returncode: int | None = None,
) -> None:
    """Record every real improve attempt so HERMES can distinguish no-op from silence."""
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "issue": issue,
        "target_file": target_file,
        "source": "improve_agent",
        "auto_improve_enabled": auto_improve_enabled(False),
        "returncode": returncode,
        "detail": _json_safe(detail or {}),
        "ladder": _ladder_snapshot(),
    }
    IMPROVE_LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    with IMPROVE_LEDGER_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")


def record_deploy(pre_commit: str, post_commit: str) -> None:
    """Reject the legacy mutable writer; activation is proven by runtime evidence."""
    raise RuntimeError(
        "record_deploy is retired: create an immutable deployment activation receipt "
        "after an exact-identity battle row is observed"
    )


def validated_promotion_artifact_context(
    promotion_proof: dict,
    *,
    result_pointer_path: Path | None = None,
    project_root: Path | None = None,
) -> dict:
    """Revalidate and pin the canonical H2H artifact before creating a commit."""
    pointer_path = result_pointer_path or HEAD_TO_HEAD_RESULT_PATH
    canonical_proof, blockers = load_latest_proof(
        pointer_path,
        project_root=(project_root or PROJECT_ROOT),
    )
    if blockers:
        raise ValueError(f"promotion proof no longer validates: {'; '.join(blockers)}")

    identity_fields = ("runId", "baselineCommit", "candidateFile", "candidatePatchSha256")
    mismatched = [field for field in identity_fields if promotion_proof.get(field) != canonical_proof.get(field)]
    promotion_lineage = promotion_proof.get("lineage") or {}
    canonical_lineage = canonical_proof.get("lineage") or {}
    if promotion_lineage.get("changeId") != canonical_lineage.get("changeId"):
        mismatched.append("lineage.changeId")
    if mismatched:
        raise ValueError(f"promotion proof changed before commit: {', '.join(mismatched)}")

    pointer_bytes = pointer_path.read_bytes()
    pointer = json.loads(pointer_bytes)
    result_relative = str(pointer.get("resultRelativePath") or "").replace("\\", "/")
    result_path = pointer_path.parent / result_relative
    result_bytes = result_path.read_bytes()
    result_sha256 = hashlib.sha256(result_bytes).hexdigest()
    if result_sha256 != pointer.get("resultSha256"):
        raise ValueError("canonical promotion result changed after independent validation")
    return {
        "pointerPath": str(pointer_path),
        "pointerSha256": hashlib.sha256(pointer_bytes).hexdigest(),
        "resultPath": str(result_path),
        "resultRelativePath": result_relative,
        "resultSha256": result_sha256,
        "runId": canonical_proof.get("runId"),
        "changeId": canonical_lineage.get("changeId"),
        "autoresearchSha256": canonical_lineage.get("autoresearchSha256"),
    }


def promotion_artifact_unchanged(context: dict) -> bool:
    try:
        pointer_path = Path(str(context["pointerPath"]))
        result_path = Path(str(context["resultPath"]))
        return (
            not pointer_path.is_symlink()
            and not result_path.is_symlink()
            and _file_sha256(pointer_path) == context["pointerSha256"]
            and _file_sha256(result_path) == context["resultSha256"]
        )
    except (KeyError, OSError, TypeError):
        return False


def record_accepted_commit(
    *,
    issue_title: str,
    target_file: str,
    pre_commit: str,
    post_commit: str,
    promotion_proof: dict,
    artifact_context: dict,
    commit_provenance: dict,
) -> dict:
    """Write an immutable receipt that binds proof, candidate bytes, and commit."""
    policy_blockers = mutation_policy_blockers(target_file)
    if policy_blockers:
        raise ValueError("accepted commit target violates mutation policy: " + "; ".join(policy_blockers))
    normalized_target = _normalized_target(target_file)
    blob = subprocess.run(
        ["git", "show", f"{post_commit}:{normalized_target}"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        timeout=60,
        check=True,
    ).stdout
    tree = subprocess.run(
        ["git", "rev-parse", f"{post_commit}^{{tree}}"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    ).stdout.strip()
    lineage = promotion_proof.get("lineage") or {}
    payload = {
        "schemaVersion": "fouler-accepted-commit/v1",
        "recordedAt": datetime.now(timezone.utc).isoformat(),
        "issueTitle": issue_title,
        "changeId": lineage.get("changeId"),
        "candidate": {
            "baselineCommit": pre_commit,
            "postCommit": post_commit,
            "commitTree": tree,
            "file": normalized_target,
            "blobSha256": hashlib.sha256(blob).hexdigest(),
            "patchSha256": promotion_proof.get("candidatePatchSha256"),
            "changedFiles": commit_provenance.get("changedFiles"),
        },
        "proof": {
            "runId": artifact_context.get("runId"),
            "resultRelativePath": artifact_context.get("resultRelativePath"),
            "resultSha256": artifact_context.get("resultSha256"),
            "pointerSha256": artifact_context.get("pointerSha256"),
            "autoresearchSha256": artifact_context.get("autoresearchSha256"),
        },
    }
    payload["receiptSha256"] = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    receipt_path = ACCEPTED_COMMIT_RECEIPT_ROOT / f"{post_commit}.json"
    _write_immutable_json(receipt_path, payload)
    return {**payload, "receiptPath": str(receipt_path)}


def load_autoresearch() -> dict:
    if not AUTORESEARCH_PATH.exists():
        return {}
    return json.loads(AUTORESEARCH_PATH.read_text(encoding="utf-8"))


def text_list(value: object) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := str(item).strip())]


def top_issue_evidence(top_issue: dict) -> list[str]:
    return text_list(top_issue.get("proof") or top_issue.get("evidence"))


def battle_ids_from_evidence(evidence: list[str]) -> list[str]:
    seen: set[str] = set()
    battle_ids: list[str] = []
    for item in evidence:
        for match in BATTLE_ID_RE.finditer(item):
            battle_id = match.group(0)
            if battle_id.lower() in seen:
                continue
            seen.add(battle_id.lower())
            battle_ids.append(battle_id)
    return battle_ids


def has_replay_protocol_evidence(report: dict, proof: list[str]) -> bool:
    """Return true only when the report includes falsifiable Showdown request/protocol/replay truth."""
    for key in ("request", "battle_request", "battleRequest", "replay_json", "replayJson"):
        if report.get(key):
            return True
    evidence_blob = "\n".join(proof)
    for key in ("protocol_lines", "protocolLines", "showdown_protocol", "showdownProtocol", "request", "battle_request", "battleRequest", "replay_json", "replayJson"):
        value = report.get(key)
        if value:
            evidence_blob += "\n" + json.dumps(value, sort_keys=True)
    grounded = report.get("grounded_context") if isinstance(report.get("grounded_context"), dict) else {}
    evidence_blob += "\n" + str(grounded.get("source") or "")
    return bool(REPLAY_PROTOCOL_EVIDENCE_RE.search(evidence_blob))


def has_request_legal_option_evidence(report: dict, proof: list[str]) -> bool:
    """Return true only when current Showdown request/legal-option evidence bounds policy edits."""
    def positive_int(value: object) -> bool:
        try:
            return int(value) > 0
        except (TypeError, ValueError):
            return False

    def raw_showdown_request_has_legal_options(value: dict) -> bool:
        active = value.get("active") if isinstance(value.get("active"), list) else []
        legal_move_count = 0
        for request in active:
            if not isinstance(request, dict):
                continue
            moves = request.get("moves") if isinstance(request.get("moves"), list) else []
            legal_move_count += sum(1 for move in moves if isinstance(move, dict) and move.get("disabled") is not True)
        side = value.get("side") if isinstance(value.get("side"), dict) else {}
        side_pokemon = side.get("pokemon") if isinstance(side.get("pokemon"), list) else []
        legal_switch_count = sum(
            1
            for mon in side_pokemon
            if isinstance(mon, dict)
            and mon.get("active") is not True
            and not str(mon.get("condition") or "").startswith("0 fnt")
        )
        return bool(active or side_pokemon) and (
            legal_move_count > 0
            or legal_switch_count > 0
            or "forceSwitch" in value
            or "wait" in value
        )

    def structured(value: object) -> bool:
        if isinstance(value, dict):
            if raw_showdown_request_has_legal_options(value):
                return True
            request_hash = value.get("requestHash")
            has_request_hash = isinstance(request_hash, str) and re.fullmatch(r"[a-f0-9]{64}", request_hash, re.IGNORECASE)
            legal_moves = value.get("legalMoves") or value.get("legal_moves")
            legal_switches = value.get("legalSwitches") or value.get("legal_switches")
            candidate_bounded = value.get("candidateSetBounded") is True or value.get("candidate_set_bounded") is True
            if has_request_hash and candidate_bounded and (
                (isinstance(legal_moves, list) and bool(legal_moves))
                or (isinstance(legal_switches, list) and bool(legal_switches))
                or value.get("forceSwitch") is not None
                or value.get("wait") is not None
            ):
                return True
            return any(structured(child) for child in value.values())
        if isinstance(value, list):
            return any(structured(child) for child in value)
        return False

    def text_has_showdown_request_protocol(text: str) -> bool:
        for line in text.splitlines():
            if "|request|" not in line:
                continue
            raw = line.split("|request|", 1)[1].strip()
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict) and raw_showdown_request_has_legal_options(payload):
                return True
        return False

    for key in ("request", "battle_request", "battleRequest", "legal_options", "legalOptions", "candidate_set", "candidateSet"):
        if structured(report.get(key)):
            return True
    for key in ("protocol_lines", "protocolLines", "showdown_protocol", "showdownProtocol"):
        value = report.get(key)
        if isinstance(value, list) and any(text_has_showdown_request_protocol(str(item)) for item in value):
            return True
        if isinstance(value, str) and text_has_showdown_request_protocol(value):
            return True
    integrity = report.get("evidence_integrity") if isinstance(report.get("evidence_integrity"), dict) else {}
    if not positive_int(integrity.get("losses_with_request_legal_options")):
        return False
    evidence_blob = "\n".join(proof)
    for key in (
        "protocol_lines",
        "protocolLines",
        "showdown_protocol",
        "showdownProtocol",
        "request",
        "battle_request",
        "battleRequest",
        "legal_options",
        "legalOptions",
        "legal_moves",
        "legalMoves",
        "legal_switches",
        "legalSwitches",
        "candidate_set",
        "candidateSet",
    ):
        value = report.get(key)
        if value:
            evidence_blob += "\n" + json.dumps(value, sort_keys=True)
    if not LEGAL_OPTION_EVIDENCE_RE.search(evidence_blob):
        return False
    if not REQUEST_HASH_RE.search(evidence_blob):
        return False
    return any(int(match.group(1)) > 0 for match in LEGAL_COUNT_RE.finditer(evidence_blob))


def validate_autoresearch_for_improvement(report: dict) -> list[str]:
    """Require replay/protocol-grounded evidence before a coding agent can patch."""
    blockers: list[str] = []
    top = report.get("top_issue", {})
    if not isinstance(top, dict) or not top:
        return ["autoresearch report has no top_issue"]
    proof = top_issue_evidence(top)
    battle_ids = battle_ids_from_evidence(proof)
    batch = report.get("batch") if isinstance(report.get("batch"), dict) else {}
    grounded = report.get("grounded_context") if isinstance(report.get("grounded_context"), dict) else {}
    source_contract = str(grounded.get("source") or "")
    evidence_integrity = report.get("evidence_integrity") if isinstance(report.get("evidence_integrity"), dict) else {}
    mechanics_text = "\n".join([
        str(top.get("key") or ""),
        str(top.get("title") or ""),
        str(top.get("summary") or ""),
        str(top.get("recommendation") or ""),
        "\n".join(proof),
    ])
    trace_only_issue = bool(TRACE_ONLY_DECISION_RE.search(mechanics_text)) and not bool(MECHANICS_OR_MATCHUP_RE.search(mechanics_text))
    if not proof:
        blockers.append("top_issue has no proof/evidence strings")
    if proof and not battle_ids:
        blockers.append("top_issue proof is not linked to Showdown battle ids")
    if not report.get("generated_at") and not report.get("generatedAt"):
        blockers.append("autoresearch report has no generated_at timestamp")
    if not batch.get("id"):
        blockers.append("autoresearch report has no batch id")
    if source_contract and UNTRUSTED_GROUNDING_SOURCE_RE.search(source_contract):
        blockers.append("grounded_context.source is not a trusted non-LLM authority")
    if MECHANICS_TERMS_RE.search(mechanics_text):
        if not source_contract:
            blockers.append("mechanics-adjacent issue lacks grounded_context.source")
        elif not TRUSTED_GROUNDING_SOURCE_RE.search(source_contract):
            blockers.append("mechanics-adjacent issue lacks trusted Showdown/oracle/engine source")
        if not has_replay_protocol_evidence(report, proof):
            blockers.append("mechanics/policy issue lacks replay/protocol evidence")
    if report.get("unsupported_mechanics_claims"):
        blockers.append("autoresearch contains unsupported mechanics claims")
    if evidence_integrity.get("claims_without_evidence") and not trace_only_issue:
        blockers.append("evidence_integrity reports claims without replay/trace evidence")
    target_file = pick_target_file(report)
    if trace_only_issue and SOURCE_POLICY_TARGET_RE.search(target_file) and not has_request_legal_option_evidence(report, proof):
        blockers.append(
            f"trace-only decision issue cannot target {target_file} without current Showdown request-backed legal-option evidence"
        )
    if any(
        isinstance(item, dict) and str(item.get("status") or "").lower() == "rejected"
        for item in report.get("mechanics_claims", [])
    ):
        blockers.append("autoresearch contains rejected mechanics claims")
    return blockers


def pick_target_file(report: dict) -> str:
    """Pick which code file to send based on the top issue."""
    top = report.get("top_issue", {})
    key = top.get("key", "")
    # Route issues to the most relevant file
    if key in ("hazard_pressure", "early_bleeding"):
        return "fp/search/eval.py"
    if key == "endgame_conversion":
        return "fp/search/endgame.py"
    # Default: the penalty pipeline
    return "fp/search/main.py"


FUNC_NAME_RE = re.compile(r"\b([a-z_][a-z0-9_]{3,})\s*\(", re.IGNORECASE)
DEFAULT_SYMBOLS_BY_ISSUE = {
    "decision_instability": (
        "_recent_action_history",
        "break_repeated_decision",
        "_apply_hard_legality_and_safety",
        "_choose_mcts_only",
        "select_move_from_eval_scores",
        "_get_fallback_move",
        "find_best_move",
    ),
}


def _implicated_symbols(report: dict) -> list[str]:
    """
    Extract candidate function/symbol names the report implicates, so we can send
    the agent the SPECIFIC functions instead of a blind 500-line tail. Looks at the
    top issue title/proof and any explicit `target_symbols`/`functions` fields.
    """
    top = report.get("top_issue", {}) if isinstance(report, dict) else {}
    names: list[str] = []
    for key in ("target_symbols", "functions", "implicated_functions"):
        val = report.get(key) or top.get(key)
        if isinstance(val, (list, tuple)):
            names.extend(str(v) for v in val)
        elif isinstance(val, str):
            names.append(val)
    issue_blob = " ".join([str(top.get("key", "")), str(top.get("title", ""))]).lower()
    for issue_key, default_names in DEFAULT_SYMBOLS_BY_ISSUE.items():
        if issue_key in issue_blob:
            names.extend(default_names)
    blob = " ".join(
        [str(top.get("title", "")), " ".join(text_list(top.get("proof") or top.get("evidence")))]
    )
    # snake_case identifiers that look like function calls
    for m in FUNC_NAME_RE.finditer(blob):
        cand = m.group(1)
        if "_" in cand or cand.islower():
            names.append(cand)
    # dedupe preserving order
    seen: set[str] = set()
    out: list[str] = []
    for n in names:
        nl = n.strip()
        if nl and nl.lower() not in seen:
            seen.add(nl.lower())
            out.append(nl)
    return out


def _extract_functions_from_source(source: str, wanted: list[str]) -> str:
    """Return the source of the named top-level/methods functions (with a little
    surrounding context), using AST line spans. Empty string if none matched."""
    import ast

    try:
        tree = ast.parse(source)
    except SyntaxError:
        return ""
    lines = source.splitlines()
    wanted_set = {w.lower() for w in wanted}
    spans: list[tuple[int, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.lower() in wanted_set:
                start = max(0, (node.lineno - 1) - 2)  # include 2 lines of context
                end = getattr(node, "end_lineno", node.lineno)
                spans.append((start, end, node.name))
    if not spans:
        return ""
    spans.sort()
    chunks: list[str] = []
    for start, end, name in spans:
        body = "\n".join(lines[start:end])
        chunks.append(f"# ---- function: {name} (lines {start + 1}-{end}) ----\n{body}")
    return "\n\n".join(chunks)


def _prompt_code_view(rel_path: str, source: str, report: dict | None = None) -> str:
    """
    Build prompt context from an already captured complete source snapshot.

    For small files, return the whole file. For large files (e.g. the 7k-line
    fp/search/main.py), extract the SPECIFIC functions implicated by the report
    instead of blindly tailing MAX_CODE_LINES (which sent the agent the wrong
    region -- the penalty pipeline tail -- regardless of the actual issue).
    Falls back to the tail only if no implicated function is found.
    """
    lines = source.splitlines()
    if len(lines) <= MAX_CODE_LINES:
        return source

    if report is not None:
        wanted = _implicated_symbols(report)
        extracted = _extract_functions_from_source(source, wanted)
        if extracted:
            header = (
                f"# NOTE: {rel_path} is {len(lines)} lines. Showing the functions "
                f"implicated by the top issue ({', '.join(wanted[:6])}). Edit ONLY "
                f"these unless you have strong evidence the fix belongs elsewhere.\n"
            )
            # Guard prompt size: cap extracted region.
            ex_lines = extracted.splitlines()
            if len(ex_lines) > MAX_CODE_LINES * 3:
                ex_lines = ex_lines[: MAX_CODE_LINES * 3]
                extracted = "\n".join(ex_lines) + "\n# ...(truncated)..."
            return header + extracted

    # Fallback: last MAX_CODE_LINES.
    return "\n".join(lines[-MAX_CODE_LINES:])


def capture_target_snapshot(rel_path: str, *, root: Path | None = None) -> dict[str, Any]:
    """Capture the immutable full-source precondition independently of prompt truncation."""

    policy_blockers = mutation_policy_blockers(rel_path)
    if policy_blockers:
        raise ValueError("candidate source violates mutation policy: " + "; ".join(policy_blockers))
    normalized = _normalized_target(rel_path)
    project_root = (root or PROJECT_ROOT).resolve()
    full_path = project_root / normalized
    if not full_path.is_file() or full_path.is_symlink():
        raise FileNotFoundError(full_path)
    source_bytes = full_path.read_bytes()
    try:
        source_text = source_bytes.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"candidate source is not UTF-8: {normalized}") from exc
    return {
        "targetFile": normalized,
        "path": full_path,
        "sourceBytes": source_bytes,
        "sourceText": source_text,
        "sourceSha256": hashlib.sha256(source_bytes).hexdigest(),
    }


def read_code_file(rel_path: str, report: dict | None = None) -> str:
    """Read one exact source snapshot and derive only its prompt representation."""

    snapshot = capture_target_snapshot(rel_path)
    return _prompt_code_view(str(snapshot["targetFile"]), str(snapshot["sourceText"]), report)


def build_prompt(report: dict, code: str, target_file: str) -> str:
    """Build the focused prompt for the coding agent."""
    top_issue = report.get("top_issue", {})
    grounded = report.get("grounded_context", {})
    opponents = report.get("top_opponent_pokemon", [])

    # Build opponent grounding section
    opponent_section = ""
    for opp in opponents[:3]:
        g = opp.get("grounding", {})
        if "error" in g:
            continue
        matchups = opp.get("matchups", {})
        opponent_section += f"\n### {g.get('pokemon', opp['pokemon'])} (seen in {opp['count']} losses)\n"
        opponent_section += f"Types: {g.get('types', [])}\n"
        opponent_section += f"Abilities: {g.get('abilities', {})}\n"
        moves_str = ", ".join(
            f"{m['name']} ({m.get('type','?')}/{m.get('basePower',0)}bp/{m.get('category','?')}, {m.get('usage_pct',0)}%)"
            for m in g.get("common_moves", [])[:6]
        )
        opponent_section += f"Common moves: {moves_str}\n"
        for team_name, mu in matchups.items():
            opponent_section += f"  vs {team_name}: walls={mu.get('walls',[])}, checks={mu.get('checks',[])}, threatened={mu.get('threatened',[])}\n"

    # Build our teams section
    teams_section = ""
    for team_name, mons in grounded.get("our_teams", {}).items():
        teams_section += f"\n### {team_name}\n"
        for mon in mons:
            moves = ", ".join(mon.get("moves", []))
            teams_section += f"- {mon['name']} ({'/'.join(mon.get('types',[]))}) [{mon.get('ability','')}] @ {mon.get('item','')}: {moves}\n"

    return textwrap.dedent(f"""\
    You are improving a competitive Pokemon gen9ou battle bot.
    The bot plays fat/stall teams on Pokemon Showdown ladder.
    Current ELO: ~1359, target: 1700.

    ## Autoresearch Report (latest batch)
    Record: {report.get('wins',0)}-{report.get('losses',0)} ({report.get('win_rate',0):.1%} WR)

    ### Top Issue: {top_issue.get('title', 'none')}
    {top_issue.get('summary', '')}
    Recommendation: {top_issue.get('recommendation', '')}
    Evidence:
    {chr(10).join('- ' + p for p in top_issue_evidence(top_issue)[:5])}

    ## Grounded Opponent Data (from pokedex.json + moves.json + Smogon stats)
    {opponent_section}

    ## Our Teams (from team files)
    {teams_section}

    ## Code to modify: {target_file}
    ```python
    {code}
    ```

    ## Your task
    Make ONE targeted change to the code above that addresses the top issue.
    The change should be small, focused, and testable.

    CRITICAL RULES:
    - Use ONLY the Pokemon data provided above. Do NOT use your own knowledge of Pokemon types, abilities, or moves.
    - The type chart, move effects, and ability interactions above are from the authoritative data files.
    - Treat prose recommendations as advisory. The replay/trace proof and local oracle data are the only sources of truth.
    - Output ONLY a unified diff (--- a/{target_file} / +++ b/{target_file}).
    - Do not add new files. Do not modify files outside {target_file}.
    - Keep changes under 50 lines.
    - Penalties, not blocks — reduce move weights, never remove options entirely.
    - The bot must play fat/stall faithfully, not cheese.

    Output the diff and nothing else.
    """)


def _find_claude_cli() -> str | None:
    """Locate the `claude` CLI (Max OAuth path). No ANTHROPIC_API_KEY needed."""
    override = os.getenv("IMPROVE_AGENT_CLAUDE_CLI")
    if override and Path(override).exists():
        return override
    from shutil import which
    found = which("claude")
    if found:
        return found
    # Common per-user install locations on the devstream hosts.
    candidates = [
        Path.home() / ".local" / "bin" / "claude.exe",   # JIGGLYPUFF (Windows)
        Path.home() / ".local" / "bin" / "claude",        # ubunztu / DEKU (Linux)
        Path("/usr/bin/claude"),
        Path("/usr/local/bin/claude"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return None


def _call_claude_cli(prompt: str, cli_path: str) -> str:
    """Drive the `claude` CLI in headless print mode (uses the host's Max OAuth login).

    This is the autonomous path on the devstream hosts: no ANTHROPIC_API_KEY is
    present, but `claude -p` authenticates via the already-installed Max OAuth
    credentials that HERMES uses. The prompt is fed on stdin so it never hits
    argv length limits.
    """
    cli_model = os.getenv("IMPROVE_AGENT_CLI_MODEL", "sonnet")
    cmd = [cli_path, "-p", "--model", cli_model]
    result = subprocess.run(
        cmd,
        input=prompt,
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=int(os.getenv("IMPROVE_AGENT_CLI_TIMEOUT", "300")),
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"claude CLI exited {result.returncode}: {result.stderr.strip()[:500]}"
        )
    out = (result.stdout or "").strip()
    if not out:
        raise RuntimeError("claude CLI returned empty output")
    return out


def _call_claude_sdk(prompt: str) -> str:
    """API-key path. Only used when ANTHROPIC_API_KEY is explicitly set."""
    import anthropic
    client = anthropic.Anthropic()
    message = client.messages.create(
        model=MODEL,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )
    return message.content[0].text


def call_claude(prompt: str) -> str:
    """Return Claude's response text via whichever auth path is available.

    Order of preference:
      1. `claude` CLI subprocess (Max OAuth) — the autonomous path on the
         devstream hosts; needs NO ANTHROPIC_API_KEY. This is what lets the
         self-improvement loop run unattended on JIGGLYPUFF/DEKU.
      2. anthropic SDK — only when ANTHROPIC_API_KEY is actually set.

    The CLI is preferred so the loop works on machines that have a Max login
    but no API key. If both paths are unavailable we raise a clear, actionable
    error instead of crashing on a bare `import anthropic`.
    """
    prefer_sdk = bool(os.getenv("ANTHROPIC_API_KEY")) and os.getenv(
        "IMPROVE_AGENT_PREFER_SDK", ""
    ).lower() in ("1", "true", "yes")

    cli_path = _find_claude_cli()
    if cli_path and not prefer_sdk:
        try:
            return _call_claude_cli(prompt, cli_path)
        except Exception as cli_err:
            print(f"[AGENT] claude CLI path failed ({cli_err}); trying SDK fallback.")
            if os.getenv("ANTHROPIC_API_KEY"):
                return _call_claude_sdk(prompt)
            raise

    if os.getenv("ANTHROPIC_API_KEY"):
        return _call_claude_sdk(prompt)

    if cli_path:
        # prefer_sdk was set but no key; fall back to the CLI anyway.
        return _call_claude_cli(prompt, cli_path)

    raise RuntimeError(
        "No LLM path available: the `claude` CLI was not found on PATH and "
        "ANTHROPIC_API_KEY is not set. Install/login the Claude CLI "
        "(`claude` on PATH, Max OAuth) or set ANTHROPIC_API_KEY."
    )


def extract_diff(response: str) -> str:
    """Extract the unified diff from Claude's response.

    Robust against the model wrapping the diff in a fenced code block: a
    closing ``` fence used to be swallowed into the patch body, producing
    "corrupt patch" errors. We start at the first diff header and stop at the
    closing fence or the first prose line after the body. Blank context lines
    are normalized to a single space so git apply accepts them.
    """
    lines = response.strip().splitlines()
    diff_lines: list[str] = []
    in_diff = False
    for line in lines:
        if line.startswith("---") or line.startswith("+++") or line.startswith("@@"):
            in_diff = True
            diff_lines.append(line)
            continue
        if not in_diff:
            continue
        if line.strip().startswith("```"):
            break
        if line == "":
            diff_lines.append(" ")
            continue
        if line[0] in (" ", "+", "-", chr(92)):
            diff_lines.append(line)
            continue
        break
    text = "\n".join(diff_lines)
    return text + "\n" if text else ""


def _diff_header_path(line: str) -> str | None:
    match = re.match(r"^(?:---|\+\+\+) (?:[ab]/)?(.+)$", line.strip())
    if not match:
        return None
    path = match.group(1).strip()
    if path == "/dev/null":
        return path
    return path.replace("\\", "/")


def validate_diff_scope(diff_text: str, target_file: str) -> list[str]:
    """Fail closed unless a unified diff only modifies target_file."""
    try:
        target = _normalized_target(target_file)
    except ValueError as exc:
        return [str(exc)]
    blockers: list[str] = mutation_policy_blockers(target)
    paths: set[str] = set()
    changed_lines = 0
    has_hunk = False
    for line in diff_text.splitlines():
        if line.startswith(("diff --git", "new file mode", "deleted file mode", "rename from", "rename to", "Binary files ")):
            blockers.append(f"unsupported diff metadata: {line[:80]}")
            continue
        header_path = _diff_header_path(line)
        if header_path:
            paths.add(header_path)
            if header_path == "/dev/null":
                blockers.append("diff creates or deletes files; only in-place target edits are allowed")
            continue
        if line.startswith("@@"):
            has_hunk = True
            continue
        if line.startswith(("+", "-")) and not line.startswith(("+++", "---")):
            changed_lines += 1
    if not has_hunk:
        blockers.append("diff has no unified hunk")
    unexpected = sorted(path for path in paths if path != target)
    if unexpected:
        blockers.append(f"diff touches paths outside target {target}: {', '.join(unexpected)}")
    if target not in paths:
        blockers.append(f"diff does not explicitly target {target}")
    if changed_lines > 50:
        blockers.append(f"diff changes {changed_lines} lines; limit is 50")
    return blockers


def source_precondition_blockers(
    snapshot: dict[str, Any],
    *,
    transaction_head: str,
    autoresearch_sha256: str,
) -> list[str]:
    """Revalidate the exact full-source bytes and transaction inputs."""

    blockers: list[str] = []
    try:
        current = capture_target_snapshot(str(snapshot["targetFile"]))
    except (OSError, TypeError, ValueError) as exc:
        return [f"candidate source precondition could not be read: {type(exc).__name__}: {exc}"]
    if current["sourceBytes"] != snapshot.get("sourceBytes"):
        blockers.append("candidate full-source bytes changed after prompt construction")
    if current["sourceSha256"] != snapshot.get("sourceSha256"):
        blockers.append("candidate full-source SHA-256 changed after prompt construction")
    if _git_head() != transaction_head:
        blockers.append("candidate checkout HEAD changed after prompt construction")
    if not AUTORESEARCH_PATH.is_file() or _file_sha256(AUTORESEARCH_PATH) != autoresearch_sha256:
        blockers.append("autoresearch evidence changed after prompt construction")
    return list(dict.fromkeys(blockers))


def generate_authorized_response(
    *,
    prompt: str,
    snapshot: dict[str, Any],
    transaction_head: str,
    autoresearch_sha256: str,
    consume_authorization: Callable[[], dict[str, Any]],
    model_call: Callable[[str], str] = call_claude,
) -> dict[str, Any]:
    """Consume one authorization and call the model only for exact source bytes."""

    blockers = source_precondition_blockers(
        snapshot,
        transaction_head=transaction_head,
        autoresearch_sha256=autoresearch_sha256,
    )
    if blockers:
        return {"generated": False, "authorizationConsumed": False, "blockers": blockers}
    consumption = consume_authorization()
    if not consumption.get("consumed"):
        return {
            "generated": False,
            "authorizationConsumed": False,
            "blockers": [str(consumption.get("blocker") or "improve authorization was not consumed")],
            "consumption": consumption,
        }
    blockers = source_precondition_blockers(
        snapshot,
        transaction_head=transaction_head,
        autoresearch_sha256=autoresearch_sha256,
    )
    if blockers:
        return {
            "generated": False,
            "authorizationConsumed": True,
            "blockers": blockers,
            "consumption": consumption,
        }
    response = model_call(prompt)
    return {
        "generated": True,
        "authorizationConsumed": True,
        "response": response,
        "consumption": consumption,
    }


@contextmanager
def prepared_candidate_workspace(
    diff_text: str,
    target_file: str,
    *,
    source_snapshot: dict[str, Any],
    transaction_head: str,
) -> Iterator[dict[str, Any]]:
    """Apply a candidate only inside a unique detached worktree."""

    scope_blockers = validate_diff_scope(diff_text, target_file)
    if scope_blockers:
        raise ValueError("diff scope validation failed: " + "; ".join(scope_blockers))
    normalized_target = _normalized_target(target_file)
    if normalized_target != source_snapshot.get("targetFile"):
        raise ValueError("candidate source snapshot does not match the diff target")
    temporary_parent = Path(tempfile.mkdtemp(prefix="fouler-improve-candidate-"))
    workspace = temporary_parent / f"worktree-{uuid.uuid4().hex}"
    diff_path = temporary_parent / f"candidate-{uuid.uuid4().hex}.patch"
    worktree_added = False
    try:
        add = subprocess.run(
            ["git", "worktree", "add", "--detach", str(workspace), transaction_head],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        if add.returncode:
            raise RuntimeError(f"candidate worktree creation failed: {(add.stderr or add.stdout).strip()}")
        worktree_added = True
        baseline_path = workspace / normalized_target
        baseline_bytes = baseline_path.read_bytes()
        if baseline_bytes != source_snapshot.get("sourceBytes"):
            raise RuntimeError("detached candidate baseline does not match the full-source precondition")
        diff_path.write_bytes(diff_text.encode("utf-8"))
        apply_flags = ["--recount", "--whitespace=nowarn"]
        result = subprocess.run(
            ["git", "apply", "--check", *apply_flags, str(diff_path)],
            cwd=str(workspace),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "apply", "--check", *apply_flags, "-C1", str(diff_path)],
                cwd=str(workspace),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if result.returncode != 0:
                raise RuntimeError(f"candidate diff does not apply cleanly: {result.stderr.strip()}")
            apply_flags = [*apply_flags, "-C1"]
        subprocess.run(
            ["git", "apply", *apply_flags, str(diff_path)],
            cwd=str(workspace),
            capture_output=True,
            check=True,
        )
        candidate_bytes = baseline_path.read_bytes()
        if candidate_bytes == baseline_bytes:
            raise RuntimeError("candidate diff did not change target bytes")
        yield {
            "root": workspace,
            "targetFile": normalized_target,
            "baselineSha256": hashlib.sha256(baseline_bytes).hexdigest(),
            "candidateSha256": hashlib.sha256(candidate_bytes).hexdigest(),
            "candidateBytes": candidate_bytes,
            "transactionHead": transaction_head,
        }
    finally:
        diff_path.unlink(missing_ok=True)
        cleanup_error = ""
        if worktree_added:
            remove = subprocess.run(
                ["git", "worktree", "remove", "--force", str(workspace)],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
                check=False,
            )
            if remove.returncode:
                cleanup_error = (remove.stderr or remove.stdout).strip()
        shutil.rmtree(temporary_parent, ignore_errors=True)
        if workspace.exists() or temporary_parent.exists() or cleanup_error:
            write_improve_recovery_block(
                "private candidate worktree cleanup failed",
                {"workspace": str(workspace), "detail": cleanup_error},
            )
            raise RuntimeError("private candidate worktree cleanup could not be proven")


def apply_diff(diff_text: str, target_file: str) -> bool:
    """The legacy shared-checkout patch writer is intentionally retired."""

    del diff_text, target_file
    raise RuntimeError("apply_diff is retired; use prepared_candidate_workspace")


def restore_file_snapshot(target_file: str, snapshot: str, expected_candidate_sha256: str) -> bool:
    """Restore only when the target still contains the candidate bytes this process wrote."""
    full_path = PROJECT_ROOT / target_file
    try:
        observed_sha = _file_sha256(full_path)
    except OSError as exc:
        write_improve_recovery_block(
            "candidate target could not be read for compare-and-swap recovery",
            {"targetFile": target_file, "detail": str(exc)},
        )
        return False
    if observed_sha != expected_candidate_sha256:
        write_improve_recovery_block(
            "candidate target changed outside the improve-agent transaction",
            {
                "targetFile": target_file,
                "expectedCandidateSha256": expected_candidate_sha256,
                "observedSha256": observed_sha,
            },
        )
        return False
    temporary = full_path.with_name(f".{full_path.name}.restore-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(snapshot, encoding="utf-8")
        os.replace(temporary, full_path)
    except OSError as exc:
        write_improve_recovery_block(
            "candidate target restore failed",
            {"targetFile": target_file, "detail": str(exc)},
        )
        return False
    finally:
        temporary.unlink(missing_ok=True)
    return True


def run_owned_command(command: list[str], *, timeout: float, cwd: Path | None = None) -> dict:
    """Run a child in an owned process group and prove its tree is gone on timeout."""
    options: dict = {}
    if os.name == "nt":
        options["creationflags"] = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    else:
        options["start_new_session"] = True
    process: subprocess.Popen | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd or PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **options,
        )
        stdout, stderr = process.communicate(timeout=timeout)
        return {
            "returncode": process.returncode,
            "stdout": stdout or "",
            "stderr": stderr or "",
            "timedOut": False,
        }
    except subprocess.TimeoutExpired as exc:
        from infrastructure.offline_eval import _terminate_process_tree

        cleanup = (
            _terminate_process_tree(process, reason="improve-agent-owned-command-timeout")
            if process is not None
            else {"returncodeAfter": None, "method": "process-not-started"}
        )
        remainder_stdout = ""
        remainder_stderr = ""
        if process is not None:
            try:
                remainder_stdout, remainder_stderr = process.communicate(timeout=5)
            except (OSError, subprocess.SubprocessError):
                pass
        if cleanup.get("returncodeAfter") is None:
            write_improve_recovery_block(
                "timed-out improve-agent child process tree could not be proven stopped",
                {"command": command, "cleanup": cleanup},
            )
        return {
            "returncode": None,
            "stdout": _output_text(exc.stdout) + _output_text(remainder_stdout),
            "stderr": _output_text(exc.stderr) + _output_text(remainder_stderr),
            "timedOut": True,
            "timeout": exc.timeout,
            "processTreeCleanup": cleanup,
        }
    except Exception as exc:
        cleanup = None
        if process is not None and process.poll() is None:
            from infrastructure.offline_eval import _terminate_process_tree

            cleanup = _terminate_process_tree(process, reason="improve-agent-owned-command-error")
        return {
            "returncode": None,
            "stdout": "",
            "stderr": "",
            "timedOut": False,
            "error": f"{type(exc).__name__}: {exc}",
            "processTreeCleanup": cleanup,
        }


def _output_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def run_tests(*, project_root: Path | None = None) -> bool:
    """Run the test suite. Returns True if all pass."""
    try:
        test_python = resolve_fouler_python()
    except Exception as exc:
        print(f"[AGENT] Tests could not resolve a complete Fouler runtime Python: {exc}")
        return False
    result = run_owned_command(
        [*test_python, "-m", "pytest", "tests/", "-q", "--tb=short"],
        timeout=int(os.getenv("IMPROVE_AGENT_TEST_TIMEOUT_SECONDS", "360")),
        cwd=project_root,
    )
    if result.get("timedOut"):
        print(f"[AGENT] Tests timed out after {result.get('timeout')}s; candidate is rejected.")
        return False
    if result.get("error"):
        print(f"[AGENT] Tests could not complete: {result['error']}")
        return False
    stdout = str(result.get("stdout") or "")
    print(f"[AGENT] Tests: {stdout.strip().splitlines()[-1] if stdout.strip() else 'no output'}")
    return result.get("returncode") == 0


EVAL_GATE_ENABLED = str(os.getenv("IMPROVE_AGENT_EVAL_GATE", "1")).lower() in {
    "1", "true", "yes", "on",
}
EVAL_GATE_BATTLES = int(os.getenv("IMPROVE_AGENT_EVAL_BATTLES", "60"))
EVAL_GATE_SHOWDOWN_PORT = int(os.getenv("IMPROVE_AGENT_EVAL_SHOWDOWN_PORT", "8791"))
EVAL_GATE_SEARCH_TIME_MS = int(os.getenv("IMPROVE_AGENT_EVAL_SEARCH_TIME_MS", "1200"))
EVAL_GATE_PER_BATTLE_TIMEOUT = float(os.getenv("IMPROVE_AGENT_EVAL_PER_BATTLE_TIMEOUT", "240"))
EVAL_GATE_TEAMS = os.getenv(
    "IMPROVE_AGENT_EVAL_TEAMS",
    "gen9/ou/fat-team-1-stall,gen9/ou/fat-team-2-balance,gen9/ou/fat-team-3-dondozo",
)
HEAD_TO_HEAD_RESULT_PATH = PROJECT_ROOT / "eval_results" / "head_to_head" / "latest.json"


def offline_eval_gate(
    target_file: str | None = None,
    autoresearch_sha256: str | None = None,
    *,
    project_root: Path | None = None,
) -> tuple[bool, dict]:
    """Run the symmetric candidate-vs-frozen gate and fail closed on every gap."""
    if not target_file:
        return False, {"error": "candidate_target_missing", "promotionAllowed": False}
    policy_blockers = mutation_policy_blockers(target_file)
    if policy_blockers:
        return False, {
            "error": "candidate_target_policy_blocked",
            "blockers": policy_blockers,
            "promotionAllowed": False,
        }
    target_file = _normalized_target(target_file)
    if not EVAL_GATE_ENABLED:
        return False, {"error": "head_to_head_gate_disabled", "promotionAllowed": False}

    root = (project_root or PROJECT_ROOT).resolve()
    eval_script = root / "infrastructure" / "head_to_head_eval.py"
    result_pointer_path = root / "eval_results" / "head_to_head" / "latest.json"
    if not eval_script.exists():
        return False, {
            "error": "eval_harness_unavailable",
            "eval_script": str(eval_script),
            "readiness_command": f"{sys.executable} infrastructure/head_to_head_eval.py --help",
        }
    if EVAL_GATE_BATTLES < 60 or EVAL_GATE_BATTLES % 12:
        return False, {
            "error": "invalid_head_to_head_matrix_size",
            "battles": EVAL_GATE_BATTLES,
            "requirement": "a multiple of 12 and at least 60",
        }
    if (
        EVAL_GATE_SEARCH_TIME_MS < MIN_EVAL_SEARCH_TIME_MS
        or EVAL_GATE_PER_BATTLE_TIMEOUT < MIN_EVAL_PER_BATTLE_TIMEOUT_SECONDS
    ):
        return False, {
            "error": "weakened_head_to_head_runtime_limits",
            "searchTimeMs": EVAL_GATE_SEARCH_TIME_MS,
            "minimumSearchTimeMs": MIN_EVAL_SEARCH_TIME_MS,
            "perBattleTimeoutSeconds": EVAL_GATE_PER_BATTLE_TIMEOUT,
            "minimumPerBattleTimeoutSeconds": MIN_EVAL_PER_BATTLE_TIMEOUT_SECONDS,
            "promotionAllowed": False,
        }
    configured_teams = tuple(team.strip().replace("\\", "/") for team in EVAL_GATE_TEAMS.split(",") if team.strip())
    required_teams = {
        "gen9/ou/fat-team-1-stall",
        "gen9/ou/fat-team-2-balance",
        "gen9/ou/fat-team-3-dondozo",
    }
    if len(configured_teams) != 3 or set(configured_teams) != required_teams:
        return False, {
            "error": "invalid_head_to_head_team_matrix",
            "configuredTeams": list(configured_teams),
            "requiredTeams": sorted(required_teams),
        }

    try:
        result_pointer_path.unlink(missing_ok=True)
    except OSError as exc:
        return False, {"error": "stale_head_to_head_result_cannot_be_removed", "detail": str(exc)}
    try:
        eval_python = resolve_fouler_python()
    except Exception as exc:
        return False, {"error": "fouler_runtime_python_unavailable", "detail": str(exc)}
    command = [
        *eval_python,
        str(eval_script),
        "--candidate-file",
        target_file,
        "--battles",
        str(EVAL_GATE_BATTLES),
        "--teams",
        EVAL_GATE_TEAMS,
        "--showdown-port",
        str(EVAL_GATE_SHOWDOWN_PORT),
        "--search-time-ms",
        str(EVAL_GATE_SEARCH_TIME_MS),
        "--per-battle-timeout",
        str(EVAL_GATE_PER_BATTLE_TIMEOUT),
        "--require-promotion",
    ]
    evidence_sha = autoresearch_sha256 or (
        _file_sha256(AUTORESEARCH_PATH) if AUTORESEARCH_PATH.is_file() else ""
    )
    if not re.fullmatch(r"[0-9a-f]{64}", evidence_sha):
        return False, {"error": "autoresearch_digest_missing", "promotionAllowed": False}
    command.extend(["--autoresearch-sha256", evidence_sha])
    print(
        "[AGENT] Running symmetric candidate-vs-frozen gate: "
        f"{EVAL_GATE_BATTLES} battles across three teams and both challenge roles."
    )
    process = run_owned_command(
        command,
        timeout=EVAL_GATE_BATTLES * EVAL_GATE_PER_BATTLE_TIMEOUT + 1800,
        cwd=root,
    )
    if process.get("timedOut"):
        return False, {
            "error": "head_to_head_eval_timed_out",
            "timeout": process.get("timeout"),
            "processTreeCleanup": process.get("processTreeCleanup"),
        }
    if process.get("error"):
        return False, {
            "error": "head_to_head_eval_process_failed",
            "detail": process.get("error"),
            "processTreeCleanup": process.get("processTreeCleanup"),
        }
    if not result_pointer_path.exists():
        return False, {
            "error": "head_to_head_result_missing",
            "returncode": process.get("returncode"),
            "stdoutTail": str(process.get("stdout") or "")[-1000:],
            "stderrTail": str(process.get("stderr") or "")[-1000:],
        }

    result, proof_blockers = load_latest_proof(result_pointer_path, project_root=root)
    if proof_blockers:
        return False, {
            **result,
            "error": "head_to_head_result_failed_independent_validation",
            "independentValidationBlockers": proof_blockers,
            "promotionAllowed": False,
        }
    current_head = _git_head(root)
    try:
        patch = subprocess.run(
            ["git", "diff", "--binary", "--", target_file],
            cwd=str(root),
            capture_output=True,
            check=True,
            timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        return False, {"error": "candidate_patch_hash_failed", "detail": str(exc)}
    if not patch:
        return False, {"error": "candidate_patch_empty", "promotionAllowed": False}
    patch_sha = hashlib.sha256(patch).hexdigest()
    exact = (
        result.get("baselineCommit") == current_head
        and result.get("candidatePatchSha256") == patch_sha
        and str(result.get("candidateFile") or "").replace("\\", "/") == target_file.replace("\\", "/")
    )
    if not exact:
        return False, {
            "error": "head_to_head_provenance_mismatch",
            "expectedBaselineCommit": current_head,
            "reportedBaselineCommit": result.get("baselineCommit"),
            "expectedCandidatePatchSha256": patch_sha,
            "reportedCandidatePatchSha256": result.get("candidatePatchSha256"),
            "expectedCandidateFile": target_file.replace("\\", "/"),
            "reportedCandidateFile": result.get("candidateFile"),
        }
    accepted = process.get("returncode") == 0 and not proof_blockers
    return accepted, result


def syntax_check(target_file: str, *, project_root: Path | None = None) -> bool:
    """AST parse check on the modified file."""
    import ast
    try:
        policy_blockers = mutation_policy_blockers(target_file)
        if policy_blockers:
            print(f"[AGENT] Syntax gate target blocked: {'; '.join(policy_blockers)}")
            return False
        target_file = _normalized_target(target_file)
        full_path = (project_root or PROJECT_ROOT) / target_file
        ast.parse(full_path.read_text(encoding="utf-8"))
        return True
    except SyntaxError as e:
        print(f"[AGENT] Syntax error: {e}")
        return False


def _git_head(root: Path | None = None) -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(root or PROJECT_ROOT), capture_output=True, text=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def committed_candidate_provenance(
    *,
    target_file: str,
    pre_commit: str,
    post_commit: str,
    promotion_proof: dict,
) -> tuple[bool, dict]:
    """Verify that Git committed exactly the candidate bytes that won the gate."""
    try:
        normalized_target = _normalized_target(target_file)
    except ValueError as exc:
        return False, {"ok": False, "blockers": [str(exc)], "candidateFile": str(target_file)}
    blockers: list[str] = mutation_policy_blockers(normalized_target)
    if promotion_proof.get("baselineCommit") != pre_commit:
        blockers.append("promotion baseline does not match pre-commit HEAD")
    if str(promotion_proof.get("candidateFile") or "").replace("\\", "/") != normalized_target:
        blockers.append("promotion candidate file does not match commit target")

    parent = subprocess.run(
        ["git", "rev-parse", f"{post_commit}^"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if parent.returncode or parent.stdout.strip() != pre_commit:
        blockers.append("candidate commit is not a single child of the proven baseline")

    changed = subprocess.run(
        ["git", "diff", "--name-only", pre_commit, post_commit],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    changed_files = [line.replace("\\", "/") for line in changed.stdout.splitlines() if line]
    if changed.returncode or changed_files != [normalized_target]:
        blockers.append("candidate commit does not change exactly the proven target file")

    patch = subprocess.run(
        ["git", "diff", "--binary", pre_commit, post_commit, "--", normalized_target],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        timeout=60,
        check=False,
    )
    actual_patch_sha = hashlib.sha256(patch.stdout).hexdigest() if patch.returncode == 0 else ""
    expected_patch_sha = str(promotion_proof.get("candidatePatchSha256") or "")
    if patch.returncode or actual_patch_sha != expected_patch_sha:
        blockers.append("candidate commit patch SHA-256 does not match the promotion proof")

    tracked_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=no"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if tracked_status.returncode or tracked_status.stdout.strip():
        blockers.append("tracked checkout is dirty after candidate commit")
    engine_status = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all", "--", "fp"],
        cwd=str(PROJECT_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if engine_status.returncode or engine_status.stdout.strip():
        blockers.append("engine checkout has uncommitted or untracked files after candidate commit")
    return not blockers, {
        "ok": not blockers,
        "blockers": blockers,
        "preCommit": pre_commit,
        "postCommit": post_commit,
        "candidateFile": normalized_target,
        "changedFiles": changed_files,
        "expectedCandidatePatchSha256": expected_patch_sha,
        "actualCandidatePatchSha256": actual_patch_sha or None,
    }


def commit_and_push(
    target_file: str,
    issue_title: str,
    *,
    candidate_root: Path | None = None,
    source_snapshot: dict[str, Any] | None = None,
    transaction_head: str | None = None,
    push_enabled: bool = False,
    push_remote: str | None = None,
    push_branch: str | None = None,
    promotion_proof: dict | None = None,
) -> bool:
    """Commit from a detached candidate tree without writing the control checkout."""

    candidate_ref: str | None = None
    post_commit: str | None = None
    try:
        policy_blockers = mutation_policy_blockers(target_file)
        if policy_blockers:
            print(f"[AGENT] Git commit blocked by mutation policy: {'; '.join(policy_blockers)}")
            return False
        target_file = _normalized_target(target_file)
        push_target = explicit_push_target(push_remote, push_branch) if push_enabled else None
        if candidate_root is None or source_snapshot is None or not transaction_head:
            print("[AGENT] Git commit blocked: private candidate workspace provenance is missing.")
            return False
        candidate_root = candidate_root.resolve()
        pre_commit = _git_head(candidate_root)
        if pre_commit != transaction_head:
            print("[AGENT] Git commit blocked: private candidate parent differs from the source transaction.")
            return False
        current_source = capture_target_snapshot(target_file)
        if (
            _git_head() != transaction_head
            or current_source["sourceBytes"] != source_snapshot.get("sourceBytes")
            or current_source["sourceSha256"] != source_snapshot.get("sourceSha256")
        ):
            print("[AGENT] Git commit blocked: control source changed during private candidate evaluation.")
            return False
        if not isinstance(promotion_proof, dict):
            print("[AGENT] Git commit blocked: candidate promotion proof is missing.")
            return False
        artifact_context = validated_promotion_artifact_context(
            promotion_proof,
            result_pointer_path=candidate_root / "eval_results" / "head_to_head" / "latest.json",
            project_root=candidate_root,
        )
        lineage = promotion_proof.get("lineage") or {}
        msg = (
            f"auto: {issue_title[:60]}\n\n"
            f"Automated fix from improve_agent.py based on autoresearch report.\n"
            f"Target: {target_file}\n"
            f"Timestamp: {datetime.now().isoformat()}\n\n"
            f"Fouler-Change-Id: {lineage.get('changeId')}\n"
            f"Fouler-H2H-Run-Id: {artifact_context.get('runId')}\n"
            f"Fouler-H2H-Result-SHA256: {artifact_context.get('resultSha256')}\n"
            f"Fouler-Candidate-Patch-SHA256: {promotion_proof.get('candidatePatchSha256')}\n"
            f"Fouler-Autoresearch-SHA256: {artifact_context.get('autoresearchSha256')}\n\n"
            f"Co-Authored-By: Claude Sonnet 4 <noreply@anthropic.com>"
        )
        # The full suite and head-to-head gate already ran against these exact
        # bytes. Commit hooks are bypassed so they cannot rewrite the candidate
        # after proof; the commit diff is hashed again immediately below.
        subprocess.run(
            ["git", "commit", "--no-verify", "--only", "-m", msg, "--", target_file],
            cwd=str(candidate_root),
            check=True,
        )
        post_commit = _git_head(candidate_root)
        provenance_ok, provenance = committed_candidate_provenance(
            target_file=target_file,
            pre_commit=pre_commit,
            post_commit=post_commit,
            promotion_proof=promotion_proof,
        )
        if not provenance_ok:
            print(f"[AGENT] Committed candidate failed exact-proof verification: {json.dumps(provenance)}")
            return False
        if not promotion_artifact_unchanged(artifact_context):
            print("[AGENT] Canonical H2H artifact changed during commit; candidate is rejected.")
            return False
        change_id = str(lineage.get("changeId") or "")
        if not re.fullmatch(r"[0-9a-f]{64}", change_id):
            print("[AGENT] Candidate change ID is malformed; local candidate ref was not created.")
            return False
        candidate_ref = f"refs/heads/auto-improve/{change_id}"
        create_ref = subprocess.run(
            ["git", "update-ref", candidate_ref, post_commit, "0" * 40],
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
        )
        if create_ref.returncode:
            print(f"[AGENT] Candidate ref creation failed closed: {(create_ref.stderr or create_ref.stdout).strip()}")
            candidate_ref = None
            return False
        try:
            receipt = record_accepted_commit(
                issue_title=issue_title,
                target_file=target_file,
                pre_commit=pre_commit,
                post_commit=post_commit,
                promotion_proof=promotion_proof,
                artifact_context=artifact_context,
                commit_provenance=provenance,
            )
        except (OSError, subprocess.SubprocessError, ValueError, TypeError) as exc:
            print(f"[AGENT] Accepted-commit receipt failed: {type(exc).__name__}: {exc}")
            subprocess.run(
                ["git", "update-ref", "-d", candidate_ref, post_commit],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                check=False,
            )
            candidate_ref = None
            return False
        print(
            f"[AGENT] Recorded accepted commit receipt "
            f"{receipt['receiptSha256'][:12]} for {post_commit[:12]}; runtime is not deployed."
        )
        if push_target is None:
            print(
                f"[AGENT] Recorded local candidate {candidate_ref}. Git push disabled; set {AUTO_PUSH_SENTINEL}=1 "
                f"or pass --enable-git-push with an explicit push target."
            )
            return True
        remote, branch = push_target
        subprocess.run(
            ["git", "push", remote, f"{post_commit}:{branch}"],
            cwd=str(PROJECT_ROOT),
            check=True,
        )
        print(f"[AGENT] Committed and pushed candidate {post_commit} to {remote} {branch}.")
        return True
    except ValueError as e:
        print(f"[AGENT] Git push blocked: {e}")
        return False
    except subprocess.CalledProcessError as e:
        print(f"[AGENT] Git failed: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Batch-triggered coding agent")
    parser.add_argument("--dry-run", action="store_true", help="Show diff but don't apply")
    parser.add_argument(
        "--enable-auto-improve",
        action="store_true",
        help=f"Allow this agent to mutate files and commit. Alternative: {AUTO_IMPROVE_SENTINEL}=1.",
    )
    parser.add_argument(
        "--max-cycles",
        type=int,
        default=1,
        help="Single recursive-improvement cycle covered by one single-use DEKU authorization.",
    )
    parser.add_argument(
        "--runtime-lease",
        help=f"Path to proof-window runtime lease JSON. Default: {RUNTIME_LEASE_PATH_ENV} or devstream/truth/runtime-lease.json.",
    )
    parser.add_argument(
        "--enable-git-push",
        action="store_true",
        help=f"Allow git push after a successful local commit. Alternative: {AUTO_PUSH_SENTINEL}=1.",
    )
    parser.add_argument(
        "--push-remote",
        help=f"Explicit git remote for push; alternatively set {PUSH_REMOTE_ENV}.",
    )
    parser.add_argument(
        "--push-branch",
        help=f"Explicit git branch for push; alternatively set {PUSH_BRANCH_ENV}.",
    )
    args = parser.parse_args()

    print(f"[AGENT] {datetime.now().isoformat()} — Starting improvement cycle")

    if not args.dry_run and not auto_improve_enabled(args.enable_auto_improve):
        print(
            f"[AGENT] BLOCKED: auto-improvement is disabled. Set {AUTO_IMPROVE_SENTINEL}=1 "
            f"or pass --enable-auto-improve to allow mutation."
        )
        append_improve_ledger(
            "blocked",
            detail={"reason": "auto_improve_disabled", "sentinel": AUTO_IMPROVE_SENTINEL},
            returncode=2,
        )
        return 2
    lease_guard: dict[str, Any] = {}
    control_guard: dict[str, Any] = {}
    lease: dict[str, Any] = {}
    if not args.dry_run:
        if args.max_cycles != 1:
            print("[AGENT] BLOCKED: each signed improve authorization is single-use and covers exactly one cycle.")
            append_improve_ledger(
                "blocked",
                detail={"reason": "improve_authorization_not_single_cycle", "requestedMaxCycles": args.max_cycles},
                returncode=2,
            )
            return 2
        lease_guard = validate_runtime_lease(
            purpose="improve-agent",
            lease_path=args.runtime_lease,
            requested_run_count=EVAL_GATE_BATTLES,
            requested_max_cycles=args.max_cycles,
            requested_max_concurrent_battles=int(os.getenv("IMPROVE_AGENT_MAX_CONCURRENT_BATTLES", "1")),
            requested_account=(
                os.getenv("IMPROVE_AGENT_ACCOUNT")
                or os.getenv("FOULER_ACTIVE_ACCOUNT")
                or os.getenv("PS_USERNAME")
                or None
            ),
            requested_replay_behavior="never",
            require_run_count=True,
            require_max_cycles=True,
            require_max_concurrent_battles=True,
            require_replay_behavior=True,
            require_deployment_receipt=True,
            verify_deployment_checkout=True,
        )
        if not lease_guard.get("ok"):
            print("[AGENT] BLOCKED: runtime lease/proof window is required for recursive improvement.")
            for blocker in lease_guard.get("blockers") or []:
                print(f"[AGENT] BLOCKER: {blocker}")
            append_improve_ledger(
                "blocked",
                detail={"reason": "runtime_lease_invalid", "lease_guard": lease_guard},
                returncode=2,
            )
            return 2

        control_guard = improvement_control_checkout_guard(
            lease_guard,
            requested_max_cycles=args.max_cycles,
        )
        if not control_guard["ready"]:
            print("[AGENT] BLOCKED: recursive improvement must run from the mutable DEKU control checkout.")
            for blocker in control_guard["blockers"]:
                print(f"[AGENT] BLOCKER: {blocker}")
            append_improve_ledger(
                "blocked",
                detail={"reason": "deku_control_checkout_invalid", "controlGuard": control_guard},
                returncode=2,
            )
            return 2

        lease = lease_guard.get("lease") if isinstance(lease_guard.get("lease"), dict) else {}

    # 1. Load autoresearch report
    report = load_autoresearch()
    if not report or not report.get("top_issue"):
        print("[AGENT] No autoresearch report or no issues found. Skipping.")
        if not args.dry_run:
            append_improve_ledger(
                "no_change",
                detail={"reason": "no_autoresearch_top_issue"},
                returncode=0,
            )
        return 0

    blockers = validate_autoresearch_for_improvement(report)
    if blockers:
        print("[AGENT] Autoresearch is not promotable. Skipping.")
        for blocker in blockers:
            print(f"[AGENT] BLOCKER: {blocker}")
        if not args.dry_run:
            append_improve_ledger(
                "no_change",
                issue=(report.get("top_issue") or {}).get("title"),
                detail={"reason": "autoresearch_not_promotable", "blockers": blockers},
                returncode=0,
            )
        return 0
    autoresearch_sha256 = _file_sha256(AUTORESEARCH_PATH)

    # Deploy-spacing gate: don't ship another change until the previous one has had
    # enough live games to be judged by elo_watchdog. Prevents unvalidated changes
    # from stacking (the root of "edits constantly but ELO never climbs").
    spacing = deployment_spacing_status()
    min_games = int(spacing["minimumGames"])
    since = int(spacing["gamesSinceActivation"])
    if not spacing["ready"]:
        print(
            f"[AGENT] Deferring: deployment transaction is not judged and ready "
            f"({since}/{min_games} exact-identity games)."
        )
        for blocker in spacing["blockers"]:
            print(f"[AGENT] BLOCKER: {blocker}")
        if not args.dry_run:
            append_improve_ledger(
                "no_change",
                issue=(report.get("top_issue") or {}).get("title"),
                detail={
                    "reason": "deployment_not_judged",
                    "games_since_activation": since,
                    "min_games_between_deploys": min_games,
                    "judgment_status": spacing.get("judgmentStatus"),
                    "blockers": spacing["blockers"],
                },
                returncode=0,
            )
        return 0

    top = report["top_issue"]
    print(f"[AGENT] Top issue: {top['title']}")
    evidence = top_issue_evidence(top)
    battle_ids = battle_ids_from_evidence(evidence)
    print(f"[AGENT] Evidence: {len(battle_ids)} battle(s), {len(evidence)} evidence item(s)")

    # 2. Pick target file and load code
    target_file = pick_target_file(report)
    print(f"[AGENT] Target file: {target_file}")
    try:
        source_snapshot = capture_target_snapshot(target_file)
    except (OSError, TypeError, ValueError) as exc:
        print(f"[AGENT] BLOCKED: candidate source snapshot failed: {type(exc).__name__}: {exc}")
        if not args.dry_run:
            append_improve_ledger(
                "blocked",
                issue=top.get("title"),
                target_file=target_file,
                detail={"reason": "candidate_snapshot_failed", "detail": str(exc)},
                returncode=2,
            )
        return 2
    prompt_transaction_head = _git_head()
    code = _prompt_code_view(target_file, str(source_snapshot["sourceText"]), report)

    # 3. Build prompt and call Claude
    prompt = build_prompt(report, code, target_file)
    print(f"[AGENT] Prompt built ({len(prompt)} chars). Calling {MODEL}...")

    if args.dry_run:
        print("[AGENT] DRY RUN — would send prompt to Claude. Exiting.")
        print(f"[AGENT] Prompt preview (first 500 chars):\n{prompt[:500]}")
        return 0

    checkout_guard = improvement_checkout_guard()
    if not checkout_guard["ready"]:
        print("[AGENT] BLOCKED: candidate checkout is not clean and exclusively available.")
        for blocker in checkout_guard["blockers"]:
            print(f"[AGENT] BLOCKER: {blocker}")
        append_improve_ledger(
            "blocked",
            issue=top.get("title"),
            target_file=target_file,
            detail={"reason": "candidate_checkout_guard", "guard": checkout_guard},
            returncode=2,
        )
        return 2

    lock_token, lock_detail = acquire_improve_lock(target_file)
    if not lock_token:
        print(f"[AGENT] BLOCKED: {json.dumps(lock_detail)}")
        append_improve_ledger(
            "blocked",
            issue=top.get("title"),
            target_file=target_file,
            detail={"reason": "candidate_lock_failed", "lock": lock_detail},
            returncode=2,
        )
        return 2

    transaction_head = prompt_transaction_head
    outcome_status = "blocked"
    outcome_detail: dict = {"reason": "candidate_transaction_not_completed"}
    outcome_returncode = 2

    def consume_once() -> dict[str, Any]:
        try:
            ledger_authority = load_ledger_authority(DEFAULT_AUTHORITY_PATH)
            return consume_improve_authorization(
                ledger_path=ledger_authority.ledger_path,
                ledger_id=ledger_authority.ledger_id,
                authority=ledger_authority,
                authorization_digest=str(lease.get("authorizationSha256") or ""),
                lease_id=str(lease.get("id") or ""),
                source_commit=str(lease.get("sourceCommit") or ""),
                source_tree=str(lease.get("sourceTree") or ""),
                change_id=str(lease.get("changeId") or ""),
                deployment_id=str(lease.get("deploymentId") or ""),
                session_id=str(lease.get("sessionId") or ""),
                account=str(lease.get("account") or ""),
                control_checkout=str(control_guard["controlCheckout"]),
                control_head=str(control_guard["controlHead"] or ""),
                control_tree=str(control_guard["controlTree"] or ""),
                max_cycles=args.max_cycles,
            )
        except Exception as exc:
            return {
                "consumed": False,
                "blocker": f"external DEKU improve authorization store failed: {type(exc).__name__}: {exc}",
            }

    try:
        generation = generate_authorized_response(
            prompt=prompt,
            snapshot=source_snapshot,
            transaction_head=transaction_head,
            autoresearch_sha256=autoresearch_sha256,
            consume_authorization=consume_once,
        )
        if not generation.get("generated"):
            generation_blockers = [str(item) for item in generation.get("blockers") or []]
            for blocker in generation_blockers:
                print(f"[AGENT] BLOCKER: {blocker}")
            if generation.get("authorizationConsumed"):
                write_improve_recovery_block(
                    "candidate inputs changed after signed authorization was consumed",
                    {"targetFile": target_file, "transactionHead": transaction_head, "blockers": generation_blockers},
                )
            outcome_detail = {
                "reason": (
                    "candidate_source_precondition_failed"
                    if not generation.get("consumption")
                    else "improve_authorization_or_post_consumption_precondition_failed"
                ),
                "blockers": generation_blockers,
                "authorizationConsumed": bool(generation.get("authorizationConsumed")),
            }
        else:
            response = str(generation.get("response") or "")
            print(f"[AGENT] Got response ({len(response)} chars)")
            diff_text = extract_diff(response)
            if not diff_text:
                print("[AGENT] No valid diff in response. Skipping.")
                print(f"[AGENT] Response preview: {response[:300]}")
                outcome_status = "no_change"
                outcome_detail = {"reason": "no_valid_diff", "response_length": len(response)}
                outcome_returncode = 0
            else:
                post_generation_blockers = source_precondition_blockers(
                    source_snapshot,
                    transaction_head=transaction_head,
                    autoresearch_sha256=autoresearch_sha256,
                )
                if post_generation_blockers:
                    write_improve_recovery_block(
                        "candidate checkout changed while the coding response was generated",
                        {
                            "targetFile": target_file,
                            "transactionHead": transaction_head,
                            "blockers": post_generation_blockers,
                        },
                    )
                    outcome_detail = {
                        "reason": "candidate_checkout_changed_during_generation",
                        "blockers": post_generation_blockers,
                    }
                else:
                    print(f"[AGENT] Diff extracted ({len(diff_text.splitlines())} lines)")
                    with prepared_candidate_workspace(
                        diff_text,
                        target_file,
                        source_snapshot=source_snapshot,
                        transaction_head=transaction_head,
                    ) as candidate:
                        candidate_root = Path(candidate["root"])
                        if not syntax_check(target_file, project_root=candidate_root):
                            print("[AGENT] Syntax check failed in the private candidate workspace.")
                            outcome_status = "rejected"
                            outcome_detail = {"reason": "syntax_check_failed"}
                            outcome_returncode = 1
                        elif not run_tests(project_root=candidate_root):
                            print("[AGENT] Tests failed or timed out in the private candidate workspace.")
                            outcome_status = "rejected"
                            outcome_detail = {"reason": "tests_failed_or_timed_out"}
                            outcome_returncode = 1
                        else:
                            try:
                                accepted, eval_detail = offline_eval_gate(
                                    target_file,
                                    autoresearch_sha256=autoresearch_sha256,
                                    project_root=candidate_root,
                                )
                            except Exception as exc:
                                accepted = False
                                eval_detail = {
                                    "error": "head_to_head_gate_exception",
                                    "detail": f"{type(exc).__name__}: {exc}",
                                }
                            print(
                                f"[AGENT] Eval gate verdict: ACCEPT={accepted} :: "
                                f"{json.dumps(eval_detail)[:600]}"
                            )
                            if not accepted:
                                print("[AGENT] Symmetric head-to-head gate rejected the candidate.")
                                outcome_status = "rejected"
                                outcome_detail = {
                                    "reason": "head_to_head_eval_rejected",
                                    "eval_detail": eval_detail,
                                }
                                outcome_returncode = 1
                            else:
                                push_requested = auto_push_enabled(args.enable_git_push)
                                committed = commit_and_push(
                                    target_file,
                                    top["title"],
                                    candidate_root=candidate_root,
                                    source_snapshot=source_snapshot,
                                    transaction_head=transaction_head,
                                    push_enabled=push_requested,
                                    push_remote=args.push_remote,
                                    push_branch=args.push_branch,
                                    promotion_proof=eval_detail,
                                )
                                if committed:
                                    outcome_status = "accepted"
                                    outcome_detail = {
                                        "reason": "head_to_head_eval_accepted",
                                        "eval_detail": eval_detail,
                                        "push_requested": push_requested,
                                    }
                                    outcome_returncode = 0
                                    destination = "committed and pushed" if push_requested else "recorded locally"
                                    print(f"[AGENT] Successfully {destination} fix for: {top['title']}")
                                else:
                                    print("[AGENT] Candidate commit or requested push failed; activation is blocked.")
                                    outcome_status = "blocked"
                                    outcome_detail = {
                                        "reason": "commit_or_push_failed",
                                        "push_requested": push_requested,
                                        "controlHead": _git_head(),
                                    }
                                    outcome_returncode = 2
    except Exception as exc:
        print(f"[AGENT] Candidate transaction failed closed: {type(exc).__name__}: {exc}")
        outcome_status = "blocked"
        outcome_detail = {
            "reason": "candidate_transaction_exception",
            "detail": f"{type(exc).__name__}: {exc}",
        }
        outcome_returncode = 2
    finally:
        if not release_improve_lock(lock_token):
            outcome_status = "blocked"
            outcome_detail = {**outcome_detail, "lockRelease": "failed"}
            outcome_returncode = 2

    append_improve_ledger(
        outcome_status,
        issue=top.get("title"),
        target_file=target_file,
        detail=outcome_detail,
        returncode=outcome_returncode,
    )
    return outcome_returncode


if __name__ == "__main__":
    raise SystemExit(main())
