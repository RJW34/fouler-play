#!/usr/bin/env python3
"""Immutable deployment receipts and checkout validation for Fouler Play."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import socket
import stat
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEPLOYMENT_SCHEMA_VERSION = "fouler-deployment-receipt/v1"
ACCEPTED_COMMIT_SCHEMA_VERSION = "fouler-accepted-commit/v1"
RUNTIME_MANIFEST_SCHEMA_VERSION = "fouler-runtime-files/v2"
RUNTIME_PREFIXES = (
    "constants_pkg/",
    "data/",
    "fp/",
    "infrastructure/",
    "replay_analysis/",
    "scripts/",
    "streaming/",
    "teams/",
)
RUNTIME_FILES = {
    "config.py",
    "constants.py",
    "pipeline.py",
    "process_lock.py",
    "run.py",
    "requirements.txt",
    "requirements-dev.txt",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40,64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{7,127}$")
HOST_ID_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
HostIdentityProvider = Callable[[], Mapping[str, str]]


def normalize_hostname(value: object) -> str:
    """Return one comparison form for an OS-reported hostname."""
    text = str(value or "").strip().rstrip(".").lower()
    try:
        text = text.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("hostname is malformed") from exc
    if (
        not text
        or len(text) > 253
        or any(char.isspace() or ord(char) < 33 or char in "/\\:@" for char in text)
        or ".." in text
    ):
        raise ValueError("hostname is malformed")
    return text


def _canonical_stable_host_id(value: object) -> str:
    text = str(value or "").strip().lower().strip("{}")
    if (
        len(text) < 8
        or len(text) > 256
        or text in {"uninitialized", "none", "unknown"}
        or not text.strip("0-")
        or any(char.isspace() or ord(char) < 33 for char in text)
    ):
        return ""
    return text


def _windows_machine_guid() -> str:
    try:
        import winreg

        access = winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
            0,
            access,
        ) as key:
            value, _kind = winreg.QueryValueEx(key, "MachineGuid")
    except Exception:
        raise RuntimeError("stable physical host identity is unavailable") from None
    canonical = _canonical_stable_host_id(value)
    if not canonical:
        raise RuntimeError("stable physical host identity is unavailable")
    return f"windows-machine-guid:{canonical}"


def _read_stable_id_file(path: Path) -> str:
    try:
        return _canonical_stable_host_id(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError):
        return ""


def _linux_machine_id() -> str:
    for path in (Path("/etc/machine-id"), Path("/var/lib/dbus/machine-id")):
        value = _read_stable_id_file(path)
        if value:
            return f"linux-machine-id:{value}"

    # Some minimal Linux images omit machine-id but expose a stable DMI UUID.
    for path in (
        Path("/sys/class/dmi/id/product_uuid"),
        Path("/sys/devices/virtual/dmi/id/product_uuid"),
    ):
        value = _read_stable_id_file(path)
        if value:
            return f"linux-dmi-product-uuid:{value}"
    raise RuntimeError("stable physical host identity is unavailable")


def _default_stable_host_id() -> str:
    if os.name == "nt":
        return _windows_machine_guid()
    if sys.platform.startswith("linux"):
        return _linux_machine_id()
    raise RuntimeError("stable physical host identity is unavailable")


def current_physical_host_identity(
    *,
    hostname_provider: Callable[[], str] | None = None,
    stable_id_provider: Callable[[], str] | None = None,
) -> dict[str, str]:
    """Return a normalized hostname and a non-reversible stable host ID hash."""
    try:
        hostname = normalize_hostname((hostname_provider or socket.gethostname)())
        stable_id = _canonical_stable_host_id(
            (stable_id_provider or _default_stable_host_id)()
        )
    except Exception:
        raise RuntimeError("stable physical host identity is unavailable") from None
    if not stable_id:
        raise RuntimeError("stable physical host identity is unavailable")
    digest = hashlib.sha256(
        b"fouler-play-physical-host/v1\0" + stable_id.encode("utf-8")
    ).hexdigest()
    return {"hostname": hostname, "hostIdSha256": digest}


def _resolve_physical_host_identity(
    *,
    current_host: Mapping[str, str] | None = None,
    host_identity_provider: HostIdentityProvider | None = None,
) -> dict[str, str]:
    try:
        identity = (
            current_host
            if current_host is not None
            else (host_identity_provider or current_physical_host_identity)()
        )
        hostname = normalize_hostname(identity.get("hostname"))
        host_id_sha256 = str(identity.get("hostIdSha256") or "").strip().lower()
    except Exception:
        raise RuntimeError("stable physical host identity is unavailable") from None
    if not HOST_ID_HASH_RE.fullmatch(host_id_sha256):
        raise RuntimeError("stable physical host identity is unavailable")
    return {"hostname": hostname, "hostIdSha256": host_id_sha256}


def physical_host_binding(
    *,
    host_identity_provider: HostIdentityProvider | None = None,
) -> dict[str, str]:
    identity = _resolve_physical_host_identity(
        host_identity_provider=host_identity_provider
    )
    return {
        "machine": identity["hostname"],
        "hostName": identity["hostname"],
        "hostIdSha256": identity["hostIdSha256"],
    }


def physical_host_binding_blockers(
    payload: Mapping[str, Any],
    *,
    label: str,
    current_host: Mapping[str, str] | None = None,
    host_identity_provider: HostIdentityProvider | None = None,
) -> list[str]:
    blockers: list[str] = []
    try:
        actual = _resolve_physical_host_identity(
            current_host=current_host,
            host_identity_provider=host_identity_provider,
        )
    except RuntimeError:
        return [f"{label} cannot validate the executing physical host identity"]

    raw_host_name = str(payload.get("hostName") or "").strip()
    try:
        host_name = normalize_hostname(raw_host_name)
    except ValueError:
        host_name = ""
        blockers.append(f"{label} hostName is missing or malformed")
    else:
        if raw_host_name != host_name:
            blockers.append(f"{label} hostName is not normalized")
    raw_machine = str(payload.get("machine") or "").strip()
    if not raw_machine:
        blockers.append(f"{label} machine compatibility label is missing")

    host_id_sha256 = str(payload.get("hostIdSha256") or "").strip().lower()
    if not HOST_ID_HASH_RE.fullmatch(host_id_sha256):
        blockers.append(f"{label} hostIdSha256 is missing or malformed")
    if host_name and host_name != actual["hostname"]:
        blockers.append(f"{label} hostname does not match the executing physical host")
    if HOST_ID_HASH_RE.fullmatch(host_id_sha256) and not hmac.compare_digest(
        host_id_sha256, actual["hostIdSha256"]
    ):
        blockers.append(f"{label} host ID does not match the executing physical host")
    return list(dict.fromkeys(blockers))


def canonical_json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def canonical_sha256(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def _git(root: Path, *args: str, text: bool = True) -> subprocess.CompletedProcess:
    command = ["git", "--no-optional-locks", "-C", str(root), *args]
    try:
        return subprocess.run(
            command,
            capture_output=True,
            text=text,
            encoding="utf-8" if text else None,
            errors="replace" if text else None,
            timeout=60,
            check=False,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        empty = "" if text else b""
        error = str(exc) if text else str(exc).encode("utf-8", errors="replace")
        return subprocess.CompletedProcess(command, 127, stdout=empty, stderr=error)


def _head_runtime_entries(root: Path) -> list[tuple[str, str]]:
    tracked = _git(root, "ls-tree", "-r", "-z", "--full-tree", "HEAD", text=False)
    if tracked.returncode:
        raise RuntimeError("tracked runtime files cannot be enumerated from HEAD")
    entries: list[tuple[str, str]] = []
    try:
        records = tracked.stdout.split(b"\0")
        for record in records:
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, object_id = metadata.decode("ascii").split()
            relative = raw_path.decode("utf-8")
            if relative not in RUNTIME_FILES and not relative.startswith(RUNTIME_PREFIXES):
                continue
            if object_type != "blob" or mode == "120000":
                raise RuntimeError(f"runtime file is linked or not a blob: {relative}")
            entries.append((relative, object_id))
    except (UnicodeError, ValueError) as exc:
        raise RuntimeError("tracked runtime file metadata is malformed") from exc
    return sorted(entries)


def _worktree_blob_ids(root: Path, paths: list[str]) -> dict[str, str]:
    if not paths:
        return {}
    if any("\n" in path or "\r" in path for path in paths):
        raise RuntimeError("runtime file path contains a line break")
    command = [
        "git",
        "--no-optional-locks",
        "-C",
        str(root),
        "hash-object",
        "--stdin-paths",
    ]
    try:
        result = subprocess.run(
            command,
            input="".join(f"{path}\n" for path in paths),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            env=_git_environment(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError("runtime files cannot be compared with HEAD") from exc
    object_ids = result.stdout.splitlines()
    if result.returncode or len(object_ids) != len(paths):
        raise RuntimeError("runtime files cannot be compared with HEAD")
    return dict(zip(paths, object_ids))


def _runtime_files_differing_from_head(
    root: Path,
    entries: list[tuple[str, str]],
) -> list[str]:
    paths = [relative for relative, _object_id in entries]
    worktree_ids = _worktree_blob_ids(root, paths)
    differing: list[str] = []
    for relative, head_object_id in entries:
        if worktree_ids.get(relative) == head_object_id:
            continue
        # hash-object sees the raw CRLF working-tree bytes on Windows while the
        # committed blob is normalized to LF. Read the blob only for mismatches
        # and accept line-ending-only differences without running Git filters.
        head = _git(root, "cat-file", "blob", head_object_id, text=False)
        try:
            working_bytes = (root / relative).read_bytes()
        except OSError:
            differing.append(relative)
            continue
        if head.returncode:
            differing.append(relative)
            continue
        if b"\0" not in working_bytes and b"\0" not in head.stdout:
            if working_bytes.replace(b"\r\n", b"\n") == head.stdout.replace(
                b"\r\n", b"\n"
            ):
                continue
        differing.append(relative)
    return differing


def _decode_git_paths(raw: bytes, *, label: str) -> list[str]:
    try:
        return [item.decode("utf-8") for item in raw.split(b"\0") if item]
    except UnicodeError as exc:
        raise RuntimeError(f"{label} paths are not UTF-8") from exc


def _checkout_dirty_entries(root: Path) -> list[str]:
    """Read checkout changes without invoking write-capable porcelain status."""
    probes = (
        ("tracked", ("diff-files", "--name-only", "-z", "--")),
        ("staged", ("diff-index", "--cached", "--name-only", "-z", "HEAD", "--")),
        ("untracked", ("ls-files", "--others", "--exclude-standard", "-z")),
    )
    entries: list[str] = []
    for label, args in probes:
        result = _git(root, *args, text=False)
        if result.returncode:
            raise RuntimeError(f"checkout {label} entries are unavailable")
        entries.extend(
            f"{label}:{path}"
            for path in _decode_git_paths(result.stdout, label=label)
        )
    return sorted(set(entries))


def runtime_manifest(root: Path) -> dict[str, Any]:
    entries = _head_runtime_entries(root)
    files: dict[str, str] = {}
    for relative, _object_id in entries:
        path = root / relative
        if not path.is_file() or path.is_symlink():
            raise RuntimeError(f"runtime file is missing or linked: {relative}")
        files[relative] = file_sha256(path)
    payload = {"schemaVersion": RUNTIME_MANIFEST_SCHEMA_VERSION, "files": files}
    return {**payload, "digest": canonical_sha256(payload)}


def checkout_identity(root: Path = PROJECT_ROOT) -> dict[str, Any]:
    head = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    blockers: list[str] = []
    source_commit = head.stdout.strip().lower() if head.returncode == 0 else ""
    source_tree = tree.stdout.strip().lower() if tree.returncode == 0 else ""
    try:
        dirty = _checkout_dirty_entries(root)
    except RuntimeError:
        dirty = []
        blockers.append("checkout change inventory is unavailable")
    if not COMMIT_RE.fullmatch(source_commit):
        blockers.append("checkout HEAD is unavailable or malformed")
    if not COMMIT_RE.fullmatch(source_tree):
        blockers.append("checkout tree is unavailable or malformed")
    if dirty:
        blockers.append("checkout contains tracked or untracked non-ignored changes")
    try:
        runtime_entries = _head_runtime_entries(root)
        manifest = runtime_manifest(root)
        differing_runtime_files = _runtime_files_differing_from_head(
            root,
            runtime_entries,
        )
        if differing_runtime_files:
            blockers.append(
                "runtime files differ from checkout HEAD: "
                + ", ".join(differing_runtime_files)
            )
    except Exception as exc:
        manifest = {}
        blockers.append(f"runtime manifest is unavailable: {exc}")
    return {
        "ready": not blockers,
        "blockers": blockers,
        "root": str(root.resolve()),
        "sourceCommit": source_commit or None,
        "sourceTree": source_tree or None,
        "runtimeManifest": manifest,
        "dirtyEntries": dirty,
    }


def _receipt_payload_without_hash(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in receipt.items() if key != "receiptSha256"}


def deployment_identity(payload: Mapping[str, Any]) -> str:
    identity = {
        key: value
        for key, value in payload.items()
        if key not in {"deploymentId", "receiptSha256", "createdAt"}
    }
    return "fouler-deploy-" + canonical_sha256(identity)[:32]


def build_deployment_receipt(
    *,
    root: Path,
    machine: str,
    change_id: str,
    authorization_type: str,
    approval_ref: str = "",
    accepted_commit_receipt_path: Path | None = None,
    accepted_commit_receipt_sha256: str = "",
    host_identity_provider: HostIdentityProvider | None = None,
) -> dict[str, Any]:
    checkout = checkout_identity(root)
    if not checkout["ready"]:
        raise ValueError("deployment checkout is not immutable: " + "; ".join(checkout["blockers"]))
    change_id = str(change_id or "").strip()
    approval_ref = str(approval_ref or "").strip()
    # Preserve the legacy label for downstream state readers, but never use it
    # as physical-host authority.
    machine_label = str(machine or "").strip()
    host_binding = physical_host_binding(
        host_identity_provider=host_identity_provider
    )
    host_binding["machine"] = machine_label or host_binding["hostName"]
    if not ID_RE.fullmatch(change_id):
        raise ValueError("change_id is required and malformed")
    authorization: dict[str, Any] = {"type": authorization_type}
    if authorization_type == "owner-approved-release":
        if not approval_ref:
            raise ValueError("owner-approved release requires approval_ref")
        authorization.update({"ownerApproved": True, "approvalRef": approval_ref})
    elif authorization_type == "accepted-change":
        if accepted_commit_receipt_path is None or not SHA256_RE.fullmatch(accepted_commit_receipt_sha256):
            raise ValueError("accepted-change deployment requires an accepted receipt path and SHA-256")
        authorization.update(
            {
                "acceptedCommitReceiptPath": str(accepted_commit_receipt_path.resolve()),
                "acceptedCommitReceiptSha256": accepted_commit_receipt_sha256,
            }
        )
    else:
        raise ValueError("authorization_type must be owner-approved-release or accepted-change")
    payload: dict[str, Any] = {
        "schemaVersion": DEPLOYMENT_SCHEMA_VERSION,
        "createdAt": datetime.now(timezone.utc).isoformat(),
        **host_binding,
        "releasePath": checkout["root"],
        "sourceCommit": checkout["sourceCommit"],
        "sourceTree": checkout["sourceTree"],
        "runtimeManifestDigest": checkout["runtimeManifest"]["digest"],
        "changeId": change_id,
        "authorization": authorization,
    }
    payload["deploymentId"] = deployment_identity(payload)
    payload["receiptSha256"] = canonical_sha256(payload)
    return payload


def write_immutable_receipt(path: Path, receipt: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(receipt, indent=2, sort_keys=True).encode("utf-8") + b"\n"
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
    return path


def _load_regular_json(path: Path, label: str, blockers: list[str]) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        blockers.append(f"{label} is missing or linked")
        return {}
    if path.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        blockers.append(f"{label} is writable instead of immutable")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        blockers.append(f"{label} is malformed: {exc}")
        return {}
    if not isinstance(payload, dict):
        blockers.append(f"{label} is not a JSON object")
        return {}
    return payload


def accepted_commit_receipt_blockers(
    path: Path,
    *,
    expected_file_sha256: str,
    source_commit: str,
    source_tree: str,
    change_id: str,
    root: Path,
) -> list[str]:
    blockers: list[str] = []
    receipt = _load_regular_json(path, "accepted commit receipt", blockers)
    if not receipt:
        return blockers
    if file_sha256(path) != expected_file_sha256:
        blockers.append("accepted commit receipt file SHA-256 does not match authorization")
    if receipt.get("schemaVersion") != ACCEPTED_COMMIT_SCHEMA_VERSION:
        blockers.append("accepted commit receipt schema is unsupported")
    reported_self_hash = str(receipt.get("receiptSha256") or "")
    if reported_self_hash != canonical_sha256(_receipt_payload_without_hash(receipt)):
        blockers.append("accepted commit receipt self-hash is invalid")
    candidate = receipt.get("candidate") if isinstance(receipt.get("candidate"), dict) else {}
    proof = receipt.get("proof") if isinstance(receipt.get("proof"), dict) else {}
    if candidate.get("postCommit") != source_commit or candidate.get("commitTree") != source_tree:
        blockers.append("accepted commit receipt does not name this commit and tree")
    if receipt.get("changeId") != change_id:
        blockers.append("accepted commit receipt changeId does not match deployment")
    for field in ("blobSha256", "patchSha256"):
        if not SHA256_RE.fullmatch(str(candidate.get(field) or "")):
            blockers.append(f"accepted commit receipt candidate {field} is malformed")
    if not SHA256_RE.fullmatch(str(proof.get("resultSha256") or "")):
        blockers.append("accepted commit receipt lacks the canonical H2H result hash")
    message = _git(root, "show", "-s", "--format=%B", source_commit)
    commit_message = message.stdout if message.returncode == 0 else ""
    required_trailers = {
        "Fouler-Change-Id": change_id,
        "Fouler-H2H-Result-SHA256": proof.get("resultSha256"),
        "Fouler-Candidate-Patch-SHA256": candidate.get("patchSha256"),
    }
    for trailer, value in required_trailers.items():
        if not value or f"{trailer}: {value}" not in commit_message:
            blockers.append(f"accepted commit is missing matching {trailer} trailer")
    return blockers


def deployment_receipt_blockers(
    path: Path,
    *,
    root: Path = PROJECT_ROOT,
    expected: Mapping[str, Any] | None = None,
    verify_checkout: bool = True,
    current_host: Mapping[str, str] | None = None,
    host_identity_provider: HostIdentityProvider | None = None,
) -> tuple[dict[str, Any], list[str]]:
    blockers: list[str] = []
    receipt = _load_regular_json(path, "deployment receipt", blockers)
    if not receipt:
        return {}, blockers
    if receipt.get("schemaVersion") != DEPLOYMENT_SCHEMA_VERSION:
        blockers.append("deployment receipt schema is unsupported")
    self_hash = str(receipt.get("receiptSha256") or "")
    if self_hash != canonical_sha256(_receipt_payload_without_hash(receipt)):
        blockers.append("deployment receipt self-hash is invalid")
    if receipt.get("deploymentId") != deployment_identity(receipt):
        blockers.append("deploymentId does not match deployment receipt content")
    blockers.extend(
        physical_host_binding_blockers(
            receipt,
            label="deployment receipt",
            current_host=current_host,
            host_identity_provider=host_identity_provider,
        )
    )
    if not COMMIT_RE.fullmatch(str(receipt.get("sourceCommit") or "")):
        blockers.append("deployment receipt sourceCommit is malformed")
    if not COMMIT_RE.fullmatch(str(receipt.get("sourceTree") or "")):
        blockers.append("deployment receipt sourceTree is malformed")
    if not SHA256_RE.fullmatch(str(receipt.get("runtimeManifestDigest") or "")):
        blockers.append("deployment receipt runtimeManifestDigest is malformed")
    if not ID_RE.fullmatch(str(receipt.get("changeId") or "")):
        blockers.append("deployment receipt changeId is malformed")
    if expected:
        field_map = {
            "sourceCommit": "sourceCommit",
            "sourceTree": "sourceTree",
            "changeId": "changeId",
            "deploymentId": "deploymentId",
            "runtimeManifestDigest": "runtimeManifestDigest",
            "machine": "machine",
            "hostName": "hostName",
            "hostIdSha256": "hostIdSha256",
        }
        for receipt_field, expected_field in field_map.items():
            value = expected.get(expected_field)
            if value and receipt.get(receipt_field) != value:
                blockers.append(f"deployment receipt {receipt_field} does not match runtime lease")
        expected_file_hash = str(expected.get("deploymentReceiptSha256") or "")
        if expected_file_hash and file_sha256(path) != expected_file_hash:
            blockers.append("deployment receipt file SHA-256 does not match runtime lease")
    authorization = receipt.get("authorization") if isinstance(receipt.get("authorization"), dict) else {}
    authorization_type = authorization.get("type")
    if authorization_type == "owner-approved-release":
        if authorization.get("ownerApproved") is not True or not str(authorization.get("approvalRef") or "").strip():
            blockers.append("owner-approved deployment receipt lacks explicit approval proof")
    elif authorization_type == "accepted-change":
        accepted_path_text = str(authorization.get("acceptedCommitReceiptPath") or "")
        accepted_hash = str(authorization.get("acceptedCommitReceiptSha256") or "")
        accepted_path = Path(accepted_path_text) if accepted_path_text else Path()
        if not accepted_path.is_absolute() or not SHA256_RE.fullmatch(accepted_hash):
            blockers.append("accepted-change authorization lacks an absolute receipt path and SHA-256")
        else:
            blockers.extend(
                accepted_commit_receipt_blockers(
                    accepted_path,
                    expected_file_sha256=accepted_hash,
                    source_commit=str(receipt.get("sourceCommit") or ""),
                    source_tree=str(receipt.get("sourceTree") or ""),
                    change_id=str(receipt.get("changeId") or ""),
                    root=root,
                )
            )
    else:
        blockers.append("deployment receipt authorization type is unsupported")
    if verify_checkout:
        checkout = checkout_identity(root)
        blockers.extend(checkout["blockers"])
        expected_checkout = {
            "releasePath": checkout.get("root"),
            "sourceCommit": checkout.get("sourceCommit"),
            "sourceTree": checkout.get("sourceTree"),
            "runtimeManifestDigest": (checkout.get("runtimeManifest") or {}).get("digest"),
        }
        for field, value in expected_checkout.items():
            if receipt.get(field) != value:
                blockers.append(f"deployment receipt {field} does not match the current checkout")
    return receipt, list(dict.fromkeys(blockers))
