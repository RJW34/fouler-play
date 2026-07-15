#!/usr/bin/env python3
"""Key-generation-only CLI for Fouler runtime lease authority."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from infrastructure.runtime_authorization import (  # noqa: E402
    KEYGEN_RESULT_SCHEMA_VERSION,
    generate_keypair_and_keyring,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate Fouler Ed25519 controller authority files. This CLI cannot issue leases."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    keygen = commands.add_parser("keygen", help="Generate a private key and public keyring.")
    keygen.add_argument("--private-key", "--private-key-path", dest="private_key", required=True)
    keygen.add_argument("--keyring", "--trust-store", dest="keyring", required=True)
    keygen.add_argument("--key-id", required=True)
    keygen.add_argument("--issued-by", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = generate_keypair_and_keyring(
            args.private_key,
            args.keyring,
            key_id=args.key_id,
            issued_by=args.issued_by,
        )
    except FileExistsError:
        result = {
            "schemaVersion": KEYGEN_RESULT_SCHEMA_VERSION,
            "ok": False,
            "blockers": [
                {"code": "output_exists", "message": "authority output already exists; nothing was overwritten"}
            ],
        }
    except (OSError, TypeError, ValueError):
        result = {
            "schemaVersion": KEYGEN_RESULT_SCHEMA_VERSION,
            "ok": False,
            "blockers": [
                {"code": "keygen_failed", "message": "authority key generation failed safely"}
            ],
        }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
