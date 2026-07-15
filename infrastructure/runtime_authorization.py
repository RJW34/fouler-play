#!/usr/bin/env python3
"""Ed25519 authorization for v3 Fouler runtime leases."""

from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import math
import os
import re
import stat
import uuid
from pathlib import Path
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)


AUTHORIZATION_SCHEMA_VERSION = "fouler-controller-authorization/v1"
KEYRING_SCHEMA_VERSION = "fouler-controller-keyring/v1"
RUNTIME_LEASE_SCHEMA_VERSION = "fouler-play-runtime-lease/v3"
AUTHORIZATION_CHECK_SCHEMA_VERSION = "fouler-controller-authorization-check/v1"
KEYGEN_RESULT_SCHEMA_VERSION = "fouler-controller-keygen-result/v1"
ALGORITHM = "Ed25519"
AUTHORIZATION_FIELDS = frozenset(
    {"schemaVersion", "algorithm", "keyId", "issuedBy", "signature"}
)
AUTHORIZATION_FIELD = "controllerAuthorization"
TRUST_STORE_PATH_ENV = "FOULER_CONTROLLER_TRUST_STORE_PATH"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_B64URL_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_SIGNING_PREFIX = b"fouler-play/runtime-lease/v3\x00"
_MAX_KEY_FILE_BYTES = 64 * 1024
_MAX_TRUST_STORE_BYTES = 1024 * 1024
ALLOW_UNPROTECTED_TRUST_STORE_FOR_TESTS = False

if os.name == "nt":
    from ctypes import wintypes

    class _BY_HANDLE_FILE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("dwFileAttributes", wintypes.DWORD),
            ("ftCreationTime", wintypes.FILETIME),
            ("ftLastAccessTime", wintypes.FILETIME),
            ("ftLastWriteTime", wintypes.FILETIME),
            ("dwVolumeSerialNumber", wintypes.DWORD),
            ("nFileSizeHigh", wintypes.DWORD),
            ("nFileSizeLow", wintypes.DWORD),
            ("nNumberOfLinks", wintypes.DWORD),
            ("nFileIndexHigh", wintypes.DWORD),
            ("nFileIndexLow", wintypes.DWORD),
        ]


    class _ACL_SIZE_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("AceCount", wintypes.DWORD),
            ("AclBytesInUse", wintypes.DWORD),
            ("AclBytesFree", wintypes.DWORD),
        ]


    class _ACE_HEADER(ctypes.Structure):
        _fields_ = [
            ("AceType", ctypes.c_ubyte),
            ("AceFlags", ctypes.c_ubyte),
            ("AceSize", wintypes.WORD),
        ]


    class _ACCESS_ACE(ctypes.Structure):
        _fields_ = [
            ("Header", _ACE_HEADER),
            ("Mask", wintypes.DWORD),
            ("SidStart", wintypes.DWORD),
        ]

if os.name == "nt":
    DEFAULT_TRUST_STORE_PATH = (
        Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
        / "HERMES"
        / "authority"
        / "fouler"
        / "controller-keys.json"
    )
else:
    DEFAULT_TRUST_STORE_PATH = (
        Path.home()
        / ".config"
        / "deku-devstream"
        / "authority"
        / "fouler"
        / "controller-keys.json"
    )


class DuplicateJSONKeyError(ValueError):
    """Raised when JSON contains the same object member more than once."""


class _AuthorityInputError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_nonfinite_constant(_value: str) -> None:
    raise ValueError("non-finite JSON number")


def strict_json_loads(document: str | bytes | bytearray) -> Any:
    """Parse JSON while rejecting duplicate keys and non-finite numbers."""

    if isinstance(document, (bytes, bytearray)):
        document = bytes(document).decode("utf-8")
    if not isinstance(document, str):
        raise TypeError("JSON input must be text or bytes")
    return json.loads(
        document,
        object_pairs_hook=_reject_duplicate_keys,
        parse_constant=_reject_nonfinite_constant,
    )


def _absolute_path(path: str | os.PathLike[str]) -> Path:
    return Path(path).expanduser().absolute()


def _read_regular_file(
    path: Path,
    *,
    max_bytes: int,
    require_posix_0600: bool = False,
) -> bytes:
    metadata = os.lstat(path)
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise ValueError("path must be a regular, non-symlink file")

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode):
            raise ValueError("opened path is not a regular file")
        if require_posix_0600 and os.name == "posix" and stat.S_IMODE(opened.st_mode) != 0o600:
            raise PermissionError("private key file mode must be exactly 0600")
        with os.fdopen(descriptor, "rb", closefd=False) as handle:
            content = handle.read(max_bytes + 1)
    finally:
        os.close(descriptor)
    if len(content) > max_bytes:
        raise ValueError("file exceeds the permitted size")
    return content


def load_strict_json(path: str | os.PathLike[str]) -> Any:
    content = _read_regular_file(
        _absolute_path(path), max_bytes=_MAX_TRUST_STORE_BYTES
    )
    return strict_json_loads(content)


def resolve_trust_store_path(
    path: str | os.PathLike[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
) -> Path:
    environment = os.environ if env is None else env
    configured = str(path or environment.get(TRUST_STORE_PATH_ENV, "")).strip()
    return _absolute_path(configured) if configured else _absolute_path(DEFAULT_TRUST_STORE_PATH)


def base64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def base64url_decode(value: object, *, expected_bytes: int) -> bytes:
    if not isinstance(value, str) or not _B64URL_RE.fullmatch(value):
        raise ValueError("value is not unpadded base64url")
    padded = value + "=" * (-len(value) % 4)
    try:
        decoded = base64.b64decode(padded, altchars=b"-_", validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("value is not valid base64url") from exc
    if len(decoded) != expected_bytes or base64url_encode(decoded) != value:
        raise ValueError("base64url value has the wrong length or is non-canonical")
    return decoded


def _json_clone(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite JSON number")
        return value
    if isinstance(value, list):
        return [_json_clone(item) for item in value]
    if isinstance(value, Mapping):
        cloned: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("JSON object keys must be strings")
            cloned[key] = _json_clone(item)
        return cloned
    raise TypeError("payload contains a non-JSON value")


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(
        _json_clone(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
        )
        + "\n"
    ).encode("ascii")


def _validated_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise ValueError(f"{label} is required and malformed")
    return value


def _validated_issuer(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not (1 <= len(value) <= 256):
        raise ValueError("issued_by is required and malformed")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("issued_by contains control characters")
    return value


def _authorization_metadata(key_id: object, issued_by: object) -> dict[str, str]:
    return {
        "schemaVersion": AUTHORIZATION_SCHEMA_VERSION,
        "algorithm": ALGORITHM,
        "keyId": _validated_id(key_id, "key_id"),
        "issuedBy": _validated_issuer(issued_by),
    }


def _signing_bytes(payload: Mapping[str, Any], metadata: Mapping[str, str]) -> bytes:
    body = _json_clone(payload)
    body[AUTHORIZATION_FIELD] = dict(metadata)
    return _SIGNING_PREFIX + canonical_json_bytes(body)


def runtime_lease_signing_bytes(payload: Mapping[str, Any]) -> bytes:
    body = _json_clone(payload)
    if body.get("schemaVersion") != RUNTIME_LEASE_SCHEMA_VERSION:
        raise ValueError(f"runtime lease schemaVersion must be {RUNTIME_LEASE_SCHEMA_VERSION}")
    authorization = body.get(AUTHORIZATION_FIELD)
    if not isinstance(authorization, dict) or set(authorization) != AUTHORIZATION_FIELDS:
        raise ValueError("authorization must contain the exact v1 fields")
    metadata = _authorization_metadata(
        authorization.get("keyId"), authorization.get("issuedBy")
    )
    if authorization.get("schemaVersion") != AUTHORIZATION_SCHEMA_VERSION:
        raise ValueError("authorization schemaVersion is unsupported")
    if authorization.get("algorithm") != ALGORITHM:
        raise ValueError("authorization algorithm is unsupported")
    body.pop(AUTHORIZATION_FIELD)
    return _signing_bytes(body, metadata)


def load_ed25519_private_key(
    path: str | os.PathLike[str],
) -> Ed25519PrivateKey:
    content = _read_regular_file(
        _absolute_path(path),
        max_bytes=_MAX_KEY_FILE_BYTES,
        require_posix_0600=True,
    )
    if not content.startswith(b"-----BEGIN PRIVATE KEY-----\n"):
        raise ValueError("private key must be unencrypted PKCS8 PEM")
    try:
        key = serialization.load_pem_private_key(content, password=None)
    except (TypeError, ValueError) as exc:
        raise ValueError("private key is not valid unencrypted PKCS8 PEM") from exc
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError("private key is not Ed25519")
    return key


def _raw_public_key(key: Ed25519PublicKey | Ed25519PrivateKey) -> bytes:
    public_key = key.public_key() if isinstance(key, Ed25519PrivateKey) else key
    if not isinstance(public_key, Ed25519PublicKey):
        raise TypeError("key must be Ed25519")
    return public_key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw
    )


def public_key_fingerprint(key: Ed25519PublicKey | Ed25519PrivateKey | bytes) -> str:
    raw = bytes(key) if isinstance(key, bytes) else _raw_public_key(key)
    if len(raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return "sha256:" + base64url_encode(hashlib.sha256(raw).digest())


def build_keyring(
    public_key: Ed25519PublicKey | Ed25519PrivateKey,
    key_id: str,
    issued_by: str,
    *,
    status: str = "active",
) -> dict[str, Any]:
    metadata = _authorization_metadata(key_id, issued_by)
    if status not in {"active", "revoked"}:
        raise ValueError("key status must be active or revoked")
    return {
        "schemaVersion": KEYRING_SCHEMA_VERSION,
        "keys": [
            {
                "keyId": metadata["keyId"],
                "algorithm": ALGORITHM,
                "issuedBy": metadata["issuedBy"],
                "status": status,
                "publicKey": base64url_encode(_raw_public_key(public_key)),
            }
        ],
    }


def sign_runtime_lease(
    payload: Mapping[str, Any],
    private_key_path: str | os.PathLike[str] | Ed25519PrivateKey | None = None,
    key_id: str | None = None,
    issued_by: str | None = None,
    *,
    key: Ed25519PrivateKey | None = None,
) -> dict[str, Any]:
    """Return a signed copy; ``private_key_path`` may also be a key object."""

    if (private_key_path is None) == (key is None):
        raise ValueError("provide exactly one private key path or key object")
    selected = key if key is not None else private_key_path
    private_key = (
        selected
        if isinstance(selected, Ed25519PrivateKey)
        else load_ed25519_private_key(selected)  # type: ignore[arg-type]
    )
    if not isinstance(private_key, Ed25519PrivateKey):
        raise TypeError("private key must be Ed25519")

    body = _json_clone(payload)
    if not isinstance(body, dict):
        raise TypeError("runtime lease payload must be a JSON object")
    if body.get("schemaVersion") != RUNTIME_LEASE_SCHEMA_VERSION:
        raise ValueError(f"runtime lease schemaVersion must be {RUNTIME_LEASE_SCHEMA_VERSION}")
    body.pop(AUTHORIZATION_FIELD, None)
    metadata = _authorization_metadata(key_id, issued_by)
    signature = private_key.sign(_signing_bytes(body, metadata))
    body[AUTHORIZATION_FIELD] = {**metadata, "signature": base64url_encode(signature)}
    return body


def runtime_lease_authorization_sha256(payload: Mapping[str, Any]) -> str:
    """Return the signed capability digest used by replay accounting."""

    return hashlib.sha256(runtime_lease_signing_bytes(payload)).hexdigest()


def _block(result: dict[str, Any], code: str, message: str) -> None:
    result["blockers"].append({"code": code, "message": message})


class _WindowsTrustApi:
    GENERIC_READ = 0x80000000
    READ_CONTROL = 0x00020000
    FILE_READ_ATTRIBUTES = 0x00000080
    FILE_SHARE_READ = 0x00000001
    OPEN_EXISTING = 3
    FILE_ATTRIBUTE_DIRECTORY = 0x00000010
    FILE_ATTRIBUTE_REPARSE_POINT = 0x00000400
    FILE_FLAG_BACKUP_SEMANTICS = 0x02000000
    FILE_FLAG_OPEN_REPARSE_POINT = 0x00200000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    SE_FILE_OBJECT = 1
    OWNER_SECURITY_INFORMATION = 0x00000001
    DACL_SECURITY_INFORMATION = 0x00000004
    SE_DACL_PROTECTED = 0x1000
    ACL_SIZE_INFORMATION = 2
    ACCESS_ALLOWED_ACE_TYPE = 0
    ACCESS_DENIED_ACE_TYPE = 1

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows trust-store APIs are unavailable")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel32.CreateFileW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.HANDLE,
        ]
        self.kernel32.CreateFileW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.kernel32.GetFileInformationByHandle.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_BY_HANDLE_FILE_INFORMATION),
        ]
        self.kernel32.GetFileInformationByHandle.restype = wintypes.BOOL
        self.kernel32.GetFileSizeEx.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_longlong),
        ]
        self.kernel32.GetFileSizeEx.restype = wintypes.BOOL
        self.kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.ReadFile.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self.kernel32.LocalFree.restype = wintypes.HLOCAL
        self.advapi32.GetSecurityInfo.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
            ctypes.POINTER(wintypes.LPVOID),
        ]
        self.advapi32.GetSecurityInfo.restype = wintypes.DWORD
        self.advapi32.GetSecurityDescriptorControl.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.WORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.GetSecurityDescriptorControl.restype = wintypes.BOOL
        self.advapi32.GetAclInformation.argtypes = [
            wintypes.LPVOID,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self.advapi32.GetAclInformation.restype = wintypes.BOOL
        self.advapi32.GetAce.argtypes = [
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPVOID),
        ]
        self.advapi32.GetAce.restype = wintypes.BOOL
        self.advapi32.ConvertSidToStringSidW.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self.advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL

    @staticmethod
    def _winerror(operation: str, code: int | None = None) -> OSError:
        number = ctypes.get_last_error() if code is None else int(code)
        return OSError(number, f"{operation} failed: {ctypes.FormatError(number).strip()}")

    def close(self, handle: int | None) -> None:
        if handle and handle != self.INVALID_HANDLE_VALUE:
            self.kernel32.CloseHandle(handle)

    def open_component(self, path: Path, *, readable: bool) -> int:
        access = self.READ_CONTROL | self.FILE_READ_ATTRIBUTES
        if readable:
            access |= self.GENERIC_READ
        handle = self.kernel32.CreateFileW(
            str(path),
            access,
            self.FILE_SHARE_READ,
            None,
            self.OPEN_EXISTING,
            self.FILE_FLAG_OPEN_REPARSE_POINT | self.FILE_FLAG_BACKUP_SEMANTICS,
            None,
        )
        if handle == self.INVALID_HANDLE_VALUE:
            raise self._winerror(f"CreateFileW({path})")
        return handle

    def identity(self, handle: int) -> tuple[int, int, int]:
        information = _BY_HANDLE_FILE_INFORMATION()
        if not self.kernel32.GetFileInformationByHandle(
            handle, ctypes.byref(information)
        ):
            raise self._winerror("GetFileInformationByHandle")
        file_index = (int(information.nFileIndexHigh) << 32) | int(
            information.nFileIndexLow
        )
        return (
            int(information.dwVolumeSerialNumber),
            file_index,
            int(information.dwFileAttributes),
        )

    def sid_string(self, sid: int) -> str:
        output = wintypes.LPWSTR()
        if not self.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)):
            raise self._winerror("ConvertSidToStringSidW")
        try:
            return str(output.value)
        finally:
            self.kernel32.LocalFree(output)

    def acl_snapshot(self, handle: int) -> dict[str, Any]:
        owner = wintypes.LPVOID()
        dacl = wintypes.LPVOID()
        descriptor = wintypes.LPVOID()
        status = self.advapi32.GetSecurityInfo(
            handle,
            self.SE_FILE_OBJECT,
            self.OWNER_SECURITY_INFORMATION | self.DACL_SECURITY_INFORMATION,
            ctypes.byref(owner),
            None,
            ctypes.byref(dacl),
            None,
            ctypes.byref(descriptor),
        )
        if status:
            raise self._winerror("GetSecurityInfo", status)
        try:
            control = wintypes.WORD(0)
            revision = wintypes.DWORD(0)
            if not self.advapi32.GetSecurityDescriptorControl(
                descriptor, ctypes.byref(control), ctypes.byref(revision)
            ):
                raise self._winerror("GetSecurityDescriptorControl")
            entries: list[dict[str, Any]] = []
            entries_valid = bool(dacl)
            if dacl:
                information = _ACL_SIZE_INFORMATION()
                if not self.advapi32.GetAclInformation(
                    dacl,
                    ctypes.byref(information),
                    ctypes.sizeof(information),
                    self.ACL_SIZE_INFORMATION,
                ):
                    raise self._winerror("GetAclInformation")
                for index in range(int(information.AceCount)):
                    ace_pointer = wintypes.LPVOID()
                    if not self.advapi32.GetAce(
                        dacl, index, ctypes.byref(ace_pointer)
                    ):
                        raise self._winerror("GetAce")
                    header = ctypes.cast(
                        ace_pointer, ctypes.POINTER(_ACE_HEADER)
                    ).contents
                    if header.AceType not in {
                        self.ACCESS_ALLOWED_ACE_TYPE,
                        self.ACCESS_DENIED_ACE_TYPE,
                    }:
                        entries_valid = False
                        continue
                    ace = ctypes.cast(
                        ace_pointer, ctypes.POINTER(_ACCESS_ACE)
                    ).contents
                    sid_address = ctypes.addressof(ace) + _ACCESS_ACE.SidStart.offset
                    entries.append(
                        {
                            "type": (
                                "Allow"
                                if header.AceType == self.ACCESS_ALLOWED_ACE_TYPE
                                else "Deny"
                            ),
                            "sid": self.sid_string(sid_address),
                            "accessMask": int(ace.Mask),
                            "inherited": bool(header.AceFlags & 0x10),
                        }
                    )
            owner_sid = self.sid_string(owner.value)
            return {
                "owner": owner_sid,
                "ownerSid": owner_sid,
                "protected": bool(control.value & self.SE_DACL_PROTECTED),
                "access": entries,
                "entriesValid": entries_valid,
            }
        finally:
            self.kernel32.LocalFree(descriptor)

    def read_file(self, handle: int, *, max_bytes: int) -> bytes:
        size = ctypes.c_longlong(0)
        if not self.kernel32.GetFileSizeEx(handle, ctypes.byref(size)):
            raise self._winerror("GetFileSizeEx")
        if size.value < 0 or size.value > max_bytes:
            raise ValueError("file exceeds the permitted size")
        remaining = int(size.value)
        chunks: list[bytes] = []
        while remaining:
            amount = min(remaining, 64 * 1024)
            buffer = ctypes.create_string_buffer(amount)
            received = wintypes.DWORD(0)
            if not self.kernel32.ReadFile(
                handle, buffer, amount, ctypes.byref(received), None
            ):
                raise self._winerror("ReadFile(trust store)")
            if received.value == 0:
                raise OSError("trust store changed while it was being read")
            chunks.append(buffer.raw[: received.value])
            remaining -= int(received.value)
        return b"".join(chunks)


_WINDOWS_TRUST_API: _WindowsTrustApi | None = None


def _windows_trust_api() -> _WindowsTrustApi:
    global _WINDOWS_TRUST_API
    if _WINDOWS_TRUST_API is None:
        _WINDOWS_TRUST_API = _WindowsTrustApi()
    return _WINDOWS_TRUST_API


class _StableWindowsTrustStore:
    def __init__(self, path: Path) -> None:
        if os.name != "nt":
            raise OSError("Windows trust-store APIs are unavailable")
        self.path = _absolute_path(path)
        self.api = _windows_trust_api()
        self.components: list[tuple[Path, int]] = []
        current = Path(self.path.anchor)
        paths = [current]
        for component in self.path.parts[1:]:
            current /= component
            paths.append(current)
        try:
            for index, component_path in enumerate(paths):
                is_target = index == len(paths) - 1
                handle = self.api.open_component(component_path, readable=is_target)
                self.components.append((component_path, handle))
                _volume, _index, attributes = self.api.identity(handle)
                if attributes & self.api.FILE_ATTRIBUTE_REPARSE_POINT:
                    raise _AuthorityInputError(
                        "trust_store_unstable",
                        "trust store path ancestry contains a reparse point",
                    )
                is_directory = bool(attributes & self.api.FILE_ATTRIBUTE_DIRECTORY)
                if is_target == is_directory:
                    raise _AuthorityInputError(
                        "trust_store_unstable",
                        "trust store target or ancestry has the wrong object type",
                    )
        except Exception:
            self.close()
            raise

    def __enter__(self) -> "_StableWindowsTrustStore":
        return self

    def __exit__(self, *_error: object) -> None:
        self.close()

    def close(self) -> None:
        while self.components:
            _path, handle = self.components.pop()
            self.api.close(handle)

    def identities(self) -> tuple[tuple[int, int, int], ...]:
        return tuple(self.api.identity(handle) for _path, handle in self.components)

    def acl_snapshots(self) -> tuple[dict[str, Any], dict[str, Any]]:
        if len(self.components) < 2:
            raise _AuthorityInputError(
                "trust_store_unstable", "trust store parent directory is unavailable"
            )
        parent_handle = self.components[-2][1]
        file_handle = self.components[-1][1]
        return (
            self.api.acl_snapshot(file_handle),
            self.api.acl_snapshot(parent_handle),
        )

    def read(self, max_bytes: int) -> bytes:
        return self.api.read_file(self.components[-1][1], max_bytes=max_bytes)


def _open_stable_windows_trust_store(path: Path) -> _StableWindowsTrustStore:
    return _StableWindowsTrustStore(path)


def _windows_acl_snapshot(path: Path) -> dict[str, Any]:
    api = _windows_trust_api()
    handle = api.open_component(_absolute_path(path), readable=False)
    try:
        _volume, _index, attributes = api.identity(handle)
        if attributes & api.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError("ACL target is a reparse point")
        return api.acl_snapshot(handle)
    finally:
        api.close(handle)


def _acl_snapshot_blockers(snapshot: Mapping[str, Any], *, label: str) -> list[str]:
    blockers: list[str] = []
    owner = str(snapshot.get("owner") or "").strip().lower()
    owner_sid = str(snapshot.get("ownerSid") or "").strip()
    allowed_owners = {
        "builtin\\administrators",
        "nt authority\\system",
    }
    allowed_owner_sids = {"S-1-5-18", "S-1-5-32-544"}
    if owner not in allowed_owners and owner_sid not in allowed_owner_sids:
        blockers.append(f"{label} owner is not Administrators or SYSTEM")
    if snapshot.get("protected") is not True:
        blockers.append(f"{label} ACL inheritance is not disabled")
    if snapshot.get("entriesValid", True) is not True:
        blockers.append(f"{label} ACL contains unsupported access entries")
    entries = snapshot.get("access")
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        return [*blockers, f"{label} ACL entries are unavailable"]
    protected_writers = {
        "builtin\\administrators",
        "nt authority\\system",
    }
    protected_writer_sids = {"S-1-5-18", "S-1-5-32-544"}
    dangerous_rights = (
        "fullcontrol",
        "modify",
        "write",
        "delete",
        "changepermissions",
        "takeownership",
        "createdirectories",
        "createfiles",
        "appenddata",
    )
    dangerous_access_mask = (
        0x00000002
        | 0x00000004
        | 0x00000010
        | 0x00000040
        | 0x00000100
        | 0x00010000
        | 0x00040000
        | 0x00080000
        | 0x10000000
        | 0x40000000
    )
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("type") or "").lower() != "allow":
            continue
        identity = str(entry.get("identity") or "").strip().lower()
        identity_sid = str(entry.get("sid") or "").strip()
        rights = str(entry.get("rights") or "").replace(" ", "").lower()
        access_mask = entry.get("accessMask")
        mask_is_dangerous = (
            isinstance(access_mask, int)
            and not isinstance(access_mask, bool)
            and bool(access_mask & dangerous_access_mask)
        )
        if (
            identity not in protected_writers
            and identity_sid not in protected_writer_sids
            and (any(item in rights for item in dangerous_rights) or mask_is_dangerous)
        ):
            blockers.append(f"{label} grants write-capable access outside Administrators or SYSTEM")
            break
    return blockers


def _windows_acl_blockers(path: Path, *, label: str) -> list[str]:
    try:
        snapshot = _windows_acl_snapshot(path)
    except (OSError, UnicodeError, ValueError):
        return [f"{label} ACL could not be verified"]
    return _acl_snapshot_blockers(snapshot, label=label)


def protected_trust_store_blockers(path: Path) -> list[str]:
    if os.name != "nt":
        return []
    blockers = _windows_acl_blockers(path, label="trust store file")
    blockers.extend(_windows_acl_blockers(path.parent, label="trust store directory"))
    return blockers


def _read_protected_windows_trust_store(path: Path) -> bytes:
    with _open_stable_windows_trust_store(path) as stable:
        identities_before = stable.identities()
        file_acl_before, directory_acl_before = stable.acl_snapshots()
        blockers = _acl_snapshot_blockers(
            file_acl_before, label="trust store file"
        )
        blockers.extend(
            _acl_snapshot_blockers(
                directory_acl_before, label="trust store directory"
            )
        )
        if blockers:
            raise _AuthorityInputError(
                "trust_store_permissions_invalid",
                "trust store permissions are not protected: " + "; ".join(blockers),
            )
        content = stable.read(_MAX_TRUST_STORE_BYTES)
        identities_after = stable.identities()
        file_acl_after, directory_acl_after = stable.acl_snapshots()
        if identities_before != identities_after:
            raise _AuthorityInputError(
                "trust_store_unstable",
                "trust store object identity or ancestry changed during verification",
            )
        if (
            file_acl_before != file_acl_after
            or directory_acl_before != directory_acl_after
        ):
            raise _AuthorityInputError(
                "trust_store_unstable",
                "trust store ACL changed during verification",
            )
        return content


def _load_keyring(
    trust_store: str | os.PathLike[str] | Mapping[str, Any] | None,
    *,
    env: Mapping[str, str] | None,
    require_protected_trust_store: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(trust_store, Mapping):
        if require_protected_trust_store and not ALLOW_UNPROTECTED_TRUST_STORE_FOR_TESTS:
            raise _AuthorityInputError(
                "trust_store_mapping_forbidden",
                "protected runtime verification requires the fixed filesystem trust store",
            )
        return _json_clone(trust_store), {"source": "mapping", "path": None}
    path = resolve_trust_store_path(trust_store, env=env)
    source = {"source": "path", "path": str(path)}
    try:
        if (
            require_protected_trust_store
            and not ALLOW_UNPROTECTED_TRUST_STORE_FOR_TESTS
            and os.name == "nt"
        ):
            content = _read_protected_windows_trust_store(path)
        else:
            if require_protected_trust_store and not ALLOW_UNPROTECTED_TRUST_STORE_FOR_TESTS:
                permission_blockers = protected_trust_store_blockers(path)
                if permission_blockers:
                    raise _AuthorityInputError(
                        "trust_store_permissions_invalid",
                        "trust store permissions are not protected: "
                        + "; ".join(permission_blockers),
                    )
            content = _read_regular_file(path, max_bytes=_MAX_TRUST_STORE_BYTES)
        payload = strict_json_loads(content)
    except _AuthorityInputError:
        raise
    except FileNotFoundError as exc:
        raise _AuthorityInputError("trust_store_missing", "trust store is missing") from exc
    except DuplicateJSONKeyError as exc:
        raise _AuthorityInputError(
            "trust_store_duplicate_json_key", "trust store contains duplicate JSON keys"
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise _AuthorityInputError(
            "trust_store_malformed", "trust store is unreadable or malformed"
        ) from exc
    if not isinstance(payload, dict):
        raise _AuthorityInputError(
            "trust_store_malformed", "trust store must be a JSON object"
        )
    return payload, source


def verify_runtime_lease_authorization(
    payload: Mapping[str, Any],
    trust_store: str | os.PathLike[str] | Mapping[str, Any] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    require_protected_trust_store: bool = False,
) -> dict[str, Any]:
    """Verify a signed lease and return only JSON-safe, non-secret diagnostics."""

    result: dict[str, Any] = {
        "schemaVersion": AUTHORIZATION_CHECK_SCHEMA_VERSION,
        "ok": False,
        "authorized": False,
        "required": True,
        "authorization": {"keyId": None, "issuedBy": None},
        "publicKeyFingerprint": None,
        "trustStore": {"source": "unresolved", "path": None},
        "blockers": [],
    }
    try:
        body = _json_clone(payload)
    except (TypeError, ValueError):
        _block(result, "runtime_lease_malformed", "runtime lease is not canonical JSON data")
        result["blockerCodes"] = [item["code"] for item in result["blockers"]]
        return result
    if not isinstance(body, dict):
        _block(result, "runtime_lease_malformed", "runtime lease must be a JSON object")
        result["blockerCodes"] = [item["code"] for item in result["blockers"]]
        return result
    if body.get("schemaVersion") != RUNTIME_LEASE_SCHEMA_VERSION:
        _block(
            result,
            "runtime_lease_schema_invalid",
            f"runtime lease schemaVersion must be {RUNTIME_LEASE_SCHEMA_VERSION}",
        )

    authorization = body.get(AUTHORIZATION_FIELD)
    metadata: dict[str, str] | None = None
    signature: bytes | None = None
    if authorization is None:
        _block(result, "authorization_missing", "runtime lease authorization is required")
    elif not isinstance(authorization, dict) or set(authorization) != AUTHORIZATION_FIELDS:
        _block(
            result,
            "authorization_fields_invalid",
            f"{AUTHORIZATION_FIELD} must contain exactly schemaVersion, algorithm, keyId, issuedBy, and signature",
        )
    else:
        if authorization.get("schemaVersion") != AUTHORIZATION_SCHEMA_VERSION:
            _block(result, "authorization_schema_invalid", "authorization schema is unsupported")
        if authorization.get("algorithm") != ALGORITHM:
            _block(result, "authorization_algorithm_invalid", "authorization algorithm is unsupported")
        try:
            metadata = _authorization_metadata(
                authorization.get("keyId"), authorization.get("issuedBy")
            )
            result["authorization"] = {
                "keyId": metadata["keyId"],
                "issuedBy": metadata["issuedBy"],
            }
        except ValueError:
            _block(result, "authorization_identity_invalid", "authorization identity is malformed")
        try:
            signature = base64url_decode(
                authorization.get("signature"), expected_bytes=64
            )
        except ValueError:
            _block(result, "authorization_signature_malformed", "authorization signature is malformed")

    keyring: dict[str, Any] | None = None
    try:
        keyring, source = _load_keyring(
            trust_store,
            env=env,
            require_protected_trust_store=require_protected_trust_store,
        )
        result["trustStore"] = source
    except _AuthorityInputError as exc:
        if not isinstance(trust_store, Mapping):
            result["trustStore"] = {
                "source": "path",
                "path": str(resolve_trust_store_path(trust_store, env=env)),
            }
        _block(result, exc.code, str(exc))

    trusted_entry: dict[str, Any] | None = None
    if keyring is not None:
        if keyring.get("schemaVersion") != KEYRING_SCHEMA_VERSION:
            _block(result, "keyring_schema_invalid", "trust store keyring schema is unsupported")
        entries = keyring.get("keys")
        if not isinstance(entries, list):
            _block(result, "keyring_keys_invalid", "trust store keys must be a list")
        else:
            indexed: dict[str, dict[str, Any]] = {}
            duplicate = False
            for entry in entries:
                if not isinstance(entry, dict):
                    _block(result, "keyring_entry_invalid", "trust store contains a malformed key entry")
                    continue
                entry_id = entry.get("keyId")
                if not isinstance(entry_id, str) or not _ID_RE.fullmatch(entry_id):
                    _block(result, "keyring_entry_invalid", "trust store contains a malformed key identity")
                    continue
                if entry_id in indexed:
                    duplicate = True
                indexed[entry_id] = entry
            if duplicate:
                _block(result, "keyring_duplicate_key_id", "trust store contains duplicate key IDs")
            if metadata is not None and not duplicate:
                trusted_entry = indexed.get(metadata["keyId"])
                if trusted_entry is None:
                    _block(result, "key_not_trusted", "authorization key is not trusted")

    public_key: Ed25519PublicKey | None = None
    if trusted_entry is not None and metadata is not None:
        status = trusted_entry.get("status")
        if status == "revoked":
            _block(result, "key_revoked", "authorization key is revoked")
        elif status != "active":
            _block(result, "key_status_invalid", "authorization key is not active")
        if trusted_entry.get("algorithm") != ALGORITHM:
            _block(result, "key_algorithm_invalid", "trusted key algorithm is unsupported")
        if trusted_entry.get("issuedBy") != metadata["issuedBy"]:
            _block(result, "issuer_mismatch", "authorization issuer does not match the trusted key")
        try:
            raw_public = base64url_decode(
                trusted_entry.get("publicKey"), expected_bytes=32
            )
            public_key = Ed25519PublicKey.from_public_bytes(raw_public)
            result["publicKeyFingerprint"] = public_key_fingerprint(raw_public)
        except (TypeError, ValueError):
            _block(result, "public_key_malformed", "trusted public key is malformed")

    if (
        not result["blockers"]
        and metadata is not None
        and signature is not None
        and public_key is not None
    ):
        try:
            unsigned = dict(body)
            unsigned.pop(AUTHORIZATION_FIELD, None)
            public_key.verify(signature, _signing_bytes(unsigned, metadata))
        except InvalidSignature:
            _block(result, "signature_invalid", "runtime lease signature is invalid")
        except (TypeError, ValueError):
            _block(result, "runtime_lease_malformed", "runtime lease cannot be canonicalized")

    result["ok"] = not result["blockers"]
    result["authorized"] = result["ok"]
    result["blockerCodes"] = [item["code"] for item in result["blockers"]]
    return result


def _stage_file(path: Path, content: bytes, mode: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0),
        mode,
    )
    try:
        if os.name == "posix":
            os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return temporary


def atomic_write_exclusive(
    path: str | os.PathLike[str], content: bytes, *, mode: int = 0o600
) -> Path:
    """Atomically publish a complete file and fail if its name already exists."""

    destination = _absolute_path(path)
    temporary = _stage_file(destination, content, mode | stat.S_IWUSR)
    published = False
    try:
        os.link(temporary, destination)
        published = True
        temporary.unlink()
        os.chmod(destination, mode)
    except Exception:
        if published:
            try:
                os.chmod(destination, stat.S_IREAD | stat.S_IWRITE)
                destination.unlink()
            except OSError:
                pass
        raise
    finally:
        try:
            os.chmod(temporary, stat.S_IREAD | stat.S_IWRITE)
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return destination


def generate_keypair_and_keyring(
    private_key_path: str | os.PathLike[str],
    keyring_path: str | os.PathLike[str],
    *,
    key_id: str,
    issued_by: str,
) -> dict[str, Any]:
    """Generate and exclusively publish a PKCS8 key plus its public keyring."""

    private_path = _absolute_path(private_key_path)
    public_path = _absolute_path(keyring_path)
    if private_path == public_path:
        raise ValueError("private key and keyring paths must differ")
    _authorization_metadata(key_id, issued_by)
    for destination in (private_path, public_path):
        if os.path.lexists(destination):
            raise FileExistsError("refusing to overwrite an existing authority file")

    private_key = Ed25519PrivateKey.generate()
    private_pem = private_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    keyring = build_keyring(private_key, key_id, issued_by)
    keyring_json = (json.dumps(keyring, indent=2, sort_keys=True) + "\n").encode("utf-8")

    private_tmp = _stage_file(private_path, private_pem, 0o600)
    keyring_tmp = _stage_file(public_path, keyring_json, 0o644)
    published: list[tuple[Path, Path]] = []
    try:
        os.link(private_tmp, private_path)
        published.append((private_path, private_tmp))
        os.link(keyring_tmp, public_path)
        published.append((public_path, keyring_tmp))
    except Exception:
        for destination, temporary in reversed(published):
            try:
                if os.path.samefile(destination, temporary):
                    destination.unlink()
            except OSError:
                pass
        raise
    finally:
        private_tmp.unlink(missing_ok=True)
        keyring_tmp.unlink(missing_ok=True)

    return {
        "schemaVersion": KEYGEN_RESULT_SCHEMA_VERSION,
        "ok": True,
        "privateKeyPath": str(private_path),
        "keyringPath": str(public_path),
        "keyId": key_id,
        "publicKeyFingerprint": public_key_fingerprint(private_key),
    }
