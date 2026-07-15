#!/usr/bin/env python3
"""Create, judge, or inspect the current immutable Fouler deployment activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.deployment_state import (  # noqa: E402
    activation_receipt_blockers,
    activation_receipt_path,
    build_activation_receipt,
    build_judgment_receipt,
    current_deployment_context,
    default_state_root,
    deployment_battles,
    judgment_receipt_blockers,
    judgment_receipt_path,
    load_current_activation,
    performance_snapshot,
    read_battle_rows,
    write_current_activation,
    write_immutable_receipt,
)


def _baseline_from_current(
    *,
    state_root: Path,
    battle_stats_path: Path,
) -> dict:
    current, blockers = load_current_activation(
        state_root=state_root,
        verify_checkout=False,
        battle_stats_path=battle_stats_path,
        verify_observation=False,
    )
    if blockers or not current:
        return {}
    rows = deployment_battles(read_battle_rows(battle_stats_path), current, decisive_only=True)
    snapshot = performance_snapshot(rows, sample_size=30)
    if not snapshot.get("decisiveBattles"):
        return {}
    return {
        **snapshot,
        "activationId": current.get("activationId"),
        "deploymentId": current.get("deploymentId"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--activate", action="store_true")
    actions.add_argument("--ensure-activation", action="store_true")
    actions.add_argument("--judge", action="store_true")
    actions.add_argument("--status", action="store_true")
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--state-root", default=str(default_state_root()))
    parser.add_argument("--deployment-receipt")
    parser.add_argument("--runtime-lease")
    parser.add_argument("--battle-stats", default=str(ROOT / "battle_stats.json"))
    parser.add_argument("--min-battles", type=int, default=30)
    parser.add_argument("--max-elo-drop", type=float, default=50.0)
    parser.add_argument("--max-glicko-deviation", type=float, default=50.0)
    args = parser.parse_args()

    root = Path(args.root).expanduser().resolve()
    state_root = Path(args.state_root).expanduser().resolve()
    battle_stats_path = Path(args.battle_stats).expanduser().resolve()
    payload: dict
    return_code = 0

    if args.activate or args.ensure_activation:
        if not args.deployment_receipt or not args.runtime_lease:
            parser.error("activation requires --deployment-receipt and --runtime-lease")
        deployment_path = Path(args.deployment_receipt).expanduser().resolve()
        lease_path = Path(args.runtime_lease).expanduser().resolve()
        current, current_blockers = load_current_activation(
            state_root=state_root,
            verify_checkout=True,
            battle_stats_path=battle_stats_path,
            verify_observation=True,
        )
        current_matches = bool(
            current
            and not current_blockers
            and current.get("deploymentReceiptPath") == str(deployment_path)
            and lease_path.is_file()
            and not lease_path.is_symlink()
            and current.get("runtimeLeaseSha256") == hashlib.sha256(lease_path.read_bytes()).hexdigest()
        )
        if args.ensure_activation and current_matches:
            payload = {
                "schemaVersion": "fouler-deployment-activation-command/v1",
                "ok": True,
                "status": "active",
                "activationReceiptPath": str(
                    activation_receipt_path(current["activationId"], state_root)
                ),
                "activation": current,
                "blockers": [],
                "reused": True,
            }
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        baseline = _baseline_from_current(
            state_root=state_root,
            battle_stats_path=battle_stats_path,
        )
        try:
            receipt = build_activation_receipt(
                root=root,
                deployment_receipt_path=deployment_path,
                runtime_lease_path=lease_path,
                battle_stats_path=battle_stats_path,
                baseline=baseline,
            )
            receipt_path = activation_receipt_path(receipt["activationId"], state_root)
            if not receipt_path.exists():
                write_immutable_receipt(receipt_path, receipt)
            _verified, blockers = activation_receipt_blockers(
                receipt_path,
                verify_checkout=True,
                battle_stats_path=battle_stats_path,
                verify_observation=True,
            )
            if blockers:
                raise ValueError("; ".join(blockers))
            pointer_path = write_current_activation(
                receipt_path,
                state_root=state_root,
                verify_checkout=True,
                battle_stats_path=battle_stats_path,
            )
            payload = {
                "schemaVersion": "fouler-deployment-activation-command/v1",
                "ok": True,
                "status": "active",
                "activationReceiptPath": str(receipt_path),
                "currentPointerPath": str(pointer_path),
                "activation": receipt,
                "blockers": [],
            }
        except Exception as exc:
            waiting = args.ensure_activation and "no completed battle row proves" in str(exc)
            payload = {
                "schemaVersion": "fouler-deployment-activation-command/v1",
                "ok": waiting,
                "status": "waiting-for-first-battle" if waiting else "blocked",
                "blockers": [] if waiting else [str(exc)],
            }
            return_code = 0 if waiting else 2
    elif args.judge:
        activation, blockers = load_current_activation(
            state_root=state_root,
            verify_checkout=True,
            battle_stats_path=battle_stats_path,
            verify_observation=True,
        )
        if blockers or not activation:
            payload = {
                "schemaVersion": "fouler-deployment-judgment-command/v1",
                "ok": False,
                "blockers": blockers or ["current activation is missing"],
            }
            return_code = 2
        else:
            receipt_path = judgment_receipt_path(activation["activationId"], state_root)
            rows = read_battle_rows(battle_stats_path)
            if receipt_path.exists():
                judgment, judgment_blockers = judgment_receipt_blockers(
                    receipt_path,
                    activation=activation,
                    battle_rows=rows,
                )
            else:
                try:
                    judgment = build_judgment_receipt(
                        activation=activation,
                        battle_rows=rows,
                        min_battles=args.min_battles,
                        max_elo_drop=args.max_elo_drop,
                        max_glicko_deviation=args.max_glicko_deviation,
                    )
                    try:
                        write_immutable_receipt(receipt_path, judgment)
                    except FileExistsError:
                        pass
                    judgment, judgment_blockers = judgment_receipt_blockers(
                        receipt_path,
                        activation=activation,
                        battle_rows=rows,
                    )
                except Exception as exc:
                    judgment = {}
                    judgment_blockers = [str(exc)]
            payload = {
                "schemaVersion": "fouler-deployment-judgment-command/v1",
                "ok": not judgment_blockers,
                "judgmentReceiptPath": str(receipt_path),
                "judgment": judgment,
                "blockers": judgment_blockers,
            }
            return_code = 0 if not judgment_blockers else 2
    else:
        payload = {
            "schemaVersion": "fouler-deployment-state-status/v1",
            **current_deployment_context(
                battle_stats_path=battle_stats_path,
                state_root=state_root,
                verify_checkout=True,
            ),
        }
        return_code = 0 if payload["ok"] else 2

    print(json.dumps(payload, indent=2, sort_keys=True))
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
