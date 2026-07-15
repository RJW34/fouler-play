#!/usr/bin/env python3
"""Create or verify an immutable Fouler deployment receipt without starting runtime."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.deployment_lineage import (  # noqa: E402
    build_deployment_receipt,
    deployment_receipt_blockers,
    write_immutable_receipt,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(ROOT))
    parser.add_argument("--receipt", required=True)
    parser.add_argument(
        "--machine",
        default="",
        help="Compatibility hint only; physical host identity is read from the executing OS.",
    )
    parser.add_argument("--change-id")
    parser.add_argument(
        "--authorization-type",
        choices=("owner-approved-release", "accepted-change"),
    )
    parser.add_argument("--approval-ref", default="")
    parser.add_argument("--accepted-commit-receipt")
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    receipt_path = Path(args.receipt).expanduser().resolve()
    if args.create:
        if not args.authorization_type or not args.change_id:
            parser.error("--create requires --authorization-type and --change-id")
        accepted_path = (
            Path(args.accepted_commit_receipt).expanduser().resolve()
            if args.accepted_commit_receipt
            else None
        )
        accepted_hash = hashlib.sha256(accepted_path.read_bytes()).hexdigest() if accepted_path else ""
        receipt = build_deployment_receipt(
            root=root,
            machine=args.machine,
            change_id=args.change_id,
            authorization_type=args.authorization_type,
            approval_ref=args.approval_ref,
            accepted_commit_receipt_path=accepted_path,
            accepted_commit_receipt_sha256=accepted_hash,
        )
        write_immutable_receipt(receipt_path, receipt)
    receipt, blockers = deployment_receipt_blockers(receipt_path, root=root)
    payload = {
        "schemaVersion": "fouler-deployment-receipt-check/v1",
        "ok": not blockers,
        "receiptPath": str(receipt_path),
        "receiptFileSha256": (
            hashlib.sha256(receipt_path.read_bytes()).hexdigest()
            if receipt_path.is_file() and not receipt_path.is_symlink()
            else None
        ),
        "deployment": receipt,
        "blockers": blockers,
        "noRuntimeActions": True,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if not blockers else 2


if __name__ == "__main__":
    raise SystemExit(main())
