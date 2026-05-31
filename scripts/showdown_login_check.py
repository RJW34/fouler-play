#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import websockets

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from streaming import state_store

ENV_FILES = [ROOT / ".env", ROOT / ".env.deku"]
DEFAULT_WS = "wss://sim3.psim.us/showdown/websocket"
LOGIN_URL = "https://play.pokemonshowdown.com/api/login"
PROOF_FILE = ROOT / "devstream" / "truth" / "showdown-login-proof.json"
OFFLINE_REHEARSAL_FILE = ROOT / "devstream" / "truth" / "offline-rehearsal.json"


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_env() -> tuple[dict[str, str], list[str]]:
    env = dict(os.environ)
    loaded: list[str] = []
    for path in ENV_FILES:
        if not path.exists():
            continue
        loaded.append(str(path))
        for raw_line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in env:
                env[key] = value
    return env, loaded


async def read_challenge(ws_url: str, timeout_seconds: int) -> tuple[str, str]:
    async with websockets.connect(ws_url, ping_interval=20, ping_timeout=20, close_timeout=10) as websocket:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while True:
            remaining = max(0.1, deadline - asyncio.get_running_loop().time())
            message = await asyncio.wait_for(websocket.recv(), timeout=remaining)
            parts = str(message).split("|")
            if len(parts) > 3 and parts[1] == "challstr":
                return parts[2], parts[3]


async def login_probe(username: str, password: str, ws_url: str, timeout_seconds: int) -> dict[str, Any]:
    client_id, challenge = await read_challenge(ws_url, timeout_seconds)
    response = requests.post(
        LOGIN_URL,
        data={
            "name": username,
            "pass": password,
            "challstr": "|".join([client_id, challenge]),
        },
        timeout=timeout_seconds,
    )
    if response.status_code != 200:
        return {"ok": False, "statusCode": response.status_code, "reason": "login endpoint returned non-200"}
    text = response.text
    if text.startswith("]"):
        text = text[1:]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {"ok": False, "reason": "login endpoint returned invalid JSON"}
    if "actionsuccess" not in data:
        return {"ok": False, "reason": "showdown rejected login", "action": data.get("action") or data.get("assertion")}
    curuser = data.get("curuser") if isinstance(data.get("curuser"), dict) else {}
    return {
        "ok": True,
        "userid": curuser.get("userid") or username.lower(),
        "named": curuser.get("name") or username,
    }


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    env, loaded = load_env()
    username = str(env.get("PS_USERNAME") or env.get("SHOWDOWN_USER_ID") or "").strip()
    password = str(env.get("PS_PASSWORD") or "").strip()
    ws_url = str(env.get("PS_WEBSOCKET_URI") or DEFAULT_WS).strip()
    payload: dict[str, Any] = {
        "schemaVersion": "fouler-play-showdown-login-check/v1",
        "checkedAt": iso_now(),
        "execute": args.execute,
        "envFilesLoaded": loaded,
        "usernamePresent": bool(username),
        "passwordPresent": bool(password),
        "websocketUri": ws_url,
        "secretValuesPrinted": False,
    }
    if args.offline_rehearsal:
        payload.update({
            "ok": True,
            "offlineRehearsal": True,
            "dryRun": True,
            "note": "Unauthenticated offline rehearsal is available; no Showdown login or battle queue was attempted.",
        })
        return payload
    if not username:
        payload.update({"ok": False, "blockers": ["PS_USERNAME or SHOWDOWN_USER_ID is missing"]})
        return payload
    if not password:
        payload.update({"ok": False, "blockers": ["PS_PASSWORD is missing"]})
        return payload
    if not args.execute:
        payload.update({"ok": True, "dryRun": True, "note": "pass --execute to perform a login-only Pokemon Showdown credential proof"})
        return payload
    try:
        result = asyncio.run(login_probe(username, password, ws_url, args.timeout_seconds))
    except Exception as exc:
        result = {"ok": False, "reason": str(exc)}
    payload["login"] = result
    payload["ok"] = bool(result.get("ok"))
    if not payload["ok"]:
        payload["blockers"] = [str(result.get("reason") or "login probe failed")]
    return payload


def write_proof(payload: dict[str, Any]) -> None:
    proof = {
        "schemaVersion": "fouler-play-showdown-login-proof/v1",
        "checkedAt": payload.get("checkedAt"),
        "execute": bool(payload.get("execute")),
        "ok": bool(payload.get("ok")),
        "usernamePresent": bool(payload.get("usernamePresent")),
        "passwordPresent": bool(payload.get("passwordPresent")),
        "loginOk": bool((payload.get("login") or {}).get("ok")),
        "blockers": payload.get("blockers") or [],
        "secretValuesPrinted": False,
    }
    PROOF_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROOF_FILE.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_offline_rehearsal(payload: dict[str, Any]) -> None:
    proof = {
        "schemaVersion": "fouler-play-offline-rehearsal/v1",
        "checkedAt": payload.get("checkedAt"),
        "ok": bool(payload.get("ok")),
        "offlineRehearsal": True,
        "secretValuesPrinted": False,
        "note": payload.get("note"),
    }
    OFFLINE_REHEARSAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    OFFLINE_REHEARSAL_FILE.write_text(json.dumps(proof, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def publish_runtime_truth(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("offlineRehearsal"):
        write_offline_rehearsal(payload)
        return state_store.write_runtime_ready_status(
            mode="offline_rehearsal",
            summary="Offline rehearsal ready; no Showdown login or ladder queue was attempted.",
        )
    if payload.get("execute") and payload.get("ok") and (payload.get("login") or {}).get("ok"):
        return state_store.write_runtime_ready_status(
            mode="login_proven",
            summary="Showdown login proof succeeded; ready for a bounded devstream batch.",
        )
    if payload.get("execute") and not payload.get("ok"):
        blockers = payload.get("blockers") if isinstance(payload.get("blockers"), list) else []
        summary = str(blockers[0] if blockers else "Showdown login proof failed.")
        lowered = summary.lower()
        code = "showdown_credential_rejected" if "reject" in lowered or "password" in lowered else "showdown_login_failed"
        return state_store.write_runtime_blocked_status(code=code, summary=summary)
    return {}


def main() -> int:
    parser = argparse.ArgumentParser(description="Perform a login-only Pokemon Showdown credential proof without queuing a battle.")
    parser.add_argument("--execute", action="store_true", help="perform the network login proof")
    parser.add_argument("--offline-rehearsal", action="store_true", help="publish fresh unauthenticated/offline rehearsal truth without touching Showdown")
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--write", action="store_true", help=f"write a secret-free proof to {PROOF_FILE.relative_to(ROOT)}")
    args = parser.parse_args()
    payload = build_payload(args)
    if args.write:
        if not payload.get("offlineRehearsal"):
            write_proof(payload)
        payload["publishedTruth"] = publish_runtime_truth(payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
