#!/usr/bin/env python3
"""Strict named-pipe client for the Windows Fouler lease broker.

The wire helpers in this module are intentionally platform-independent so the
protocol can be tested on non-Windows hosts. The transport and process-identity
checks use only the Python standard library and documented Win32 APIs via
``ctypes``.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import struct
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping


PIPE_NAME = r"\\.\pipe\HERMES.FoulerLeaseBroker.v1"
PROTOCOL_VERSION = "fouler-lease-broker-request/v1"
RESPONSE_VERSION = "fouler-lease-broker-response/v1"
DEFAULT_SERVICE_NAME = "HERMES-FoulerLeaseBroker"
MAX_FRAME_BYTES = 64 * 1024
RUNTIME_RESERVATION_PURPOSE = "run-py-battle-runner"
IMPROVE_RESERVATION_PURPOSE = "deku-control-plane-improvement"
RESERVATION_BINDING_FIELDS = (
    "reservationId",
    "kind",
    "purpose",
    "battleCount",
    "cycleCount",
    "maxConcurrentBattles",
    "supervisorProcessId",
    "supervisorProcessCreationFiletime",
    "supervisorInstanceId",
    "launchNonce",
)
_FRAME_PREFIX = struct.Struct(">I")
_IMMUTABLE_PYTHON_RE = re.compile(
    r"^D:\\Releases\\fouler-play\\[0-9a-f]{40,64}\\\.venv\\Scripts\\python\.exe$",
    re.IGNORECASE,
)
_IMMUTABLE_BROKER_SCRIPT_RE = re.compile(
    r"^D:\\Releases\\fouler-play\\[0-9a-f]{40,64}\\infrastructure\\windows\\"
    r"fouler_lease_broker\.py$",
    re.IGNORECASE,
)


class DuplicateJSONKeyError(ValueError):
    """Raised when a JSON object repeats a member name."""


class ProtocolError(ValueError):
    """A bounded, caller-safe protocol failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ServerIdentityError(PermissionError):
    """The connected pipe server is not the installed broker service."""


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DuplicateJSONKeyError(f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_nonfinite(value: str) -> None:
    raise ValueError(f"non-finite JSON number is forbidden: {value}")


def strict_json_loads(document: str | bytes | bytearray) -> Any:
    """Decode UTF-8 JSON while rejecting duplicate keys and non-finite numbers."""

    if isinstance(document, (bytes, bytearray)):
        try:
            document = bytes(document).decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            raise ProtocolError("invalid_utf8", "frame payload is not valid UTF-8") from exc
    if not isinstance(document, str):
        raise TypeError("JSON input must be text or bytes")
    try:
        return json.loads(
            document,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_nonfinite,
        )
    except DuplicateJSONKeyError:
        raise
    except (json.JSONDecodeError, ValueError) as exc:
        raise ProtocolError("invalid_json", "frame payload is not strict JSON") from exc


def canonical_json_bytes(value: Any) -> bytes:
    """Return the canonical, bounded wire representation for a JSON value."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=True,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
    except (TypeError, ValueError) as exc:
        raise ProtocolError("non_json_value", "payload is not canonical JSON data") from exc
    if not encoded:
        raise ProtocolError("empty_frame", "frame payload must not be empty")
    if len(encoded) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large", "frame payload exceeds the size limit")
    return encoded


def encode_frame(payload: Any) -> bytes:
    body = canonical_json_bytes(payload)
    return _FRAME_PREFIX.pack(len(body)) + body


def decode_frame_bytes(frame: bytes | bytearray) -> Any:
    """Decode exactly one complete length-prefixed frame."""

    raw = bytes(frame)
    if len(raw) < _FRAME_PREFIX.size:
        raise ProtocolError("truncated_prefix", "frame length prefix is incomplete")
    (length,) = _FRAME_PREFIX.unpack(raw[: _FRAME_PREFIX.size])
    if length == 0:
        raise ProtocolError("empty_frame", "frame payload must not be empty")
    if length > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large", "frame payload exceeds the size limit")
    if len(raw) != _FRAME_PREFIX.size + length:
        raise ProtocolError("frame_length_mismatch", "frame length does not match its prefix")
    return strict_json_loads(raw[_FRAME_PREFIX.size :])


def read_framed(
    read_exact: Callable[[int], bytes], *, max_bytes: int = MAX_FRAME_BYTES
) -> Any:
    prefix = read_exact(_FRAME_PREFIX.size)
    if len(prefix) != _FRAME_PREFIX.size:
        raise ProtocolError("truncated_prefix", "frame length prefix is incomplete")
    (length,) = _FRAME_PREFIX.unpack(prefix)
    if length == 0:
        raise ProtocolError("empty_frame", "frame payload must not be empty")
    if length > max_bytes:
        raise ProtocolError("frame_too_large", "frame payload exceeds the size limit")
    payload = read_exact(length)
    if len(payload) != length:
        raise ProtocolError("truncated_frame", "frame payload is incomplete")
    return strict_json_loads(payload)


def request_digest(payload: Mapping[str, Any]) -> str:
    """Return the canonical request digest used by the idempotency journal."""

    return hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()


@dataclass(frozen=True)
class VerifiedServerIdentity:
    process_id: int
    process_creation_filetime: int
    executable: str
    token_user_sid: str
    service_sid: str
    service_process_id: int
    parent_process_id: int
    command_line: str
    broker_script: str


if os.name == "nt":
    from ctypes import wintypes

    class _FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", wintypes.DWORD),
            ("dwHighDateTime", wintypes.DWORD),
        ]


    class _OVERLAPPED(ctypes.Structure):
        _fields_ = [
            ("Internal", ctypes.c_size_t),
            ("InternalHigh", ctypes.c_size_t),
            ("Offset", wintypes.DWORD),
            ("OffsetHigh", wintypes.DWORD),
            ("hEvent", wintypes.HANDLE),
        ]


    class _UNICODE_STRING(ctypes.Structure):
        _fields_ = [
            ("Length", wintypes.USHORT),
            ("MaximumLength", wintypes.USHORT),
            ("Buffer", wintypes.LPWSTR),
        ]


    class _SID_AND_ATTRIBUTES(ctypes.Structure):
        _fields_ = [("Sid", wintypes.LPVOID), ("Attributes", wintypes.DWORD)]


    class _TOKEN_USER(ctypes.Structure):
        _fields_ = [("User", _SID_AND_ATTRIBUTES)]


    class _TOKEN_GROUPS_ONE(ctypes.Structure):
        _fields_ = [
            ("GroupCount", wintypes.DWORD),
            ("Groups", _SID_AND_ATTRIBUTES * 1),
        ]


    class _PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]


    class _SERVICE_STATUS_PROCESS(ctypes.Structure):
        _fields_ = [
            ("dwServiceType", wintypes.DWORD),
            ("dwCurrentState", wintypes.DWORD),
            ("dwControlsAccepted", wintypes.DWORD),
            ("dwWin32ExitCode", wintypes.DWORD),
            ("dwServiceSpecificExitCode", wintypes.DWORD),
            ("dwCheckPoint", wintypes.DWORD),
            ("dwWaitHint", wintypes.DWORD),
            ("dwProcessId", wintypes.DWORD),
            ("dwServiceFlags", wintypes.DWORD),
        ]


class _WindowsApi:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    TOKEN_QUERY = 0x0008
    TOKEN_USER = 1
    TOKEN_GROUPS = 2
    SE_GROUP_ENABLED = 0x00000004
    SE_GROUP_USE_FOR_DENY_ONLY = 0x00000010
    TH32CS_SNAPPROCESS = 0x00000002
    SC_MANAGER_CONNECT = 0x0001
    SERVICE_QUERY_STATUS = 0x0004
    SC_STATUS_PROCESS_INFO = 0
    OPEN_EXISTING = 3
    FILE_READ_DATA = 0x0001
    FILE_WRITE_DATA = 0x0002
    FILE_FLAG_OVERLAPPED = 0x40000000
    SECURITY_SQOS_PRESENT = 0x00100000
    SECURITY_IDENTIFICATION = 0x00010000
    ERROR_INSUFFICIENT_BUFFER = 122
    ERROR_IO_PENDING = 997
    ERROR_OPERATION_ABORTED = 995
    ERROR_NOT_FOUND = 1168
    PROCESS_COMMAND_LINE_INFORMATION = 60
    WAIT_OBJECT_0 = 0x00000000
    WAIT_TIMEOUT = 0x00000102
    WAIT_FAILED = 0xFFFFFFFF
    INFINITE = 0xFFFFFFFF
    CANCELLATION_GRACE_MS = 1_000
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows named pipes are unavailable on this platform")
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)
        self.ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        self.shell32 = ctypes.WinDLL("shell32", use_last_error=True)

        self.kernel32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
        self.kernel32.WaitNamedPipeW.restype = wintypes.BOOL
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
        self.kernel32.ReadFile.argtypes = [
            wintypes.HANDLE,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPVOID,
        ]
        self.kernel32.ReadFile.restype = wintypes.BOOL
        self.kernel32.WriteFile.argtypes = self.kernel32.ReadFile.argtypes
        self.kernel32.WriteFile.restype = wintypes.BOOL
        self.kernel32.CreateEventW.argtypes = [
            wintypes.LPVOID,
            wintypes.BOOL,
            wintypes.BOOL,
            wintypes.LPCWSTR,
        ]
        self.kernel32.CreateEventW.restype = wintypes.HANDLE
        self.kernel32.WaitForSingleObject.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
        ]
        self.kernel32.WaitForSingleObject.restype = wintypes.DWORD
        self.kernel32.GetOverlappedResult.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_OVERLAPPED),
            ctypes.POINTER(wintypes.DWORD),
            wintypes.BOOL,
        ]
        self.kernel32.GetOverlappedResult.restype = wintypes.BOOL
        self.kernel32.CancelIoEx.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_OVERLAPPED),
        ]
        self.kernel32.CancelIoEx.restype = wintypes.BOOL
        self.kernel32.GetNamedPipeServerProcessId.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.GetNamedPipeServerProcessId.restype = wintypes.BOOL
        self.kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        self.kernel32.OpenProcess.restype = wintypes.HANDLE
        self.kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
            ctypes.POINTER(_FILETIME),
        ]
        self.kernel32.GetProcessTimes.restype = wintypes.BOOL
        self.kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        self.kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
        self.kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        self.kernel32.Process32FirstW.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(_PROCESSENTRY32W),
        ]
        self.kernel32.Process32FirstW.restype = wintypes.BOOL
        self.kernel32.Process32NextW.argtypes = self.kernel32.Process32FirstW.argtypes
        self.kernel32.Process32NextW.restype = wintypes.BOOL
        self.kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
        self.kernel32.LocalFree.restype = wintypes.HLOCAL

        self.advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        ]
        self.advapi32.OpenProcessToken.restype = wintypes.BOOL
        self.advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.GetTokenInformation.restype = wintypes.BOOL
        self.advapi32.ConvertSidToStringSidW.argtypes = [
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.LPWSTR),
        ]
        self.advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.advapi32.LookupAccountNameW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.LPVOID,
            ctypes.POINTER(wintypes.DWORD),
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.LookupAccountNameW.restype = wintypes.BOOL
        self.advapi32.OpenSCManagerW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        self.advapi32.OpenSCManagerW.restype = wintypes.HANDLE
        self.advapi32.OpenServiceW.argtypes = [
            wintypes.HANDLE,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        self.advapi32.OpenServiceW.restype = wintypes.HANDLE
        self.advapi32.QueryServiceStatusEx.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_ubyte),
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self.advapi32.QueryServiceStatusEx.restype = wintypes.BOOL
        self.ntdll.NtQueryInformationProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.ULONG,
            ctypes.POINTER(wintypes.ULONG),
        ]
        self.ntdll.NtQueryInformationProcess.restype = wintypes.LONG
        self.shell32.CommandLineToArgvW.argtypes = [
            wintypes.LPCWSTR,
            ctypes.POINTER(ctypes.c_int),
        ]
        self.shell32.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)

    @staticmethod
    def _filetime_value(value: "_FILETIME") -> int:
        return (int(value.dwHighDateTime) << 32) | int(value.dwLowDateTime)

    def close(self, handle: int | None) -> None:
        if handle and handle != self.INVALID_HANDLE_VALUE:
            self.kernel32.CloseHandle(handle)

    def last_error(self, operation: str) -> OSError:
        code = ctypes.get_last_error()
        return OSError(code, f"{operation} failed: {ctypes.FormatError(code).strip()}")

    @staticmethod
    def error_from_code(operation: str, code: int) -> OSError:
        return OSError(code, f"{operation} failed: {ctypes.FormatError(code).strip()}")

    def process_creation_filetime(self, process: int) -> int:
        creation = _FILETIME()
        exit_time = _FILETIME()
        kernel = _FILETIME()
        user = _FILETIME()
        if not self.kernel32.GetProcessTimes(
            process,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            raise self.last_error("GetProcessTimes")
        result = self._filetime_value(creation)
        if result <= 0:
            raise ServerIdentityError("server process creation FILETIME is invalid")
        return result

    def process_image(self, process: int) -> str:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not self.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            raise self.last_error("QueryFullProcessImageNameW")
        return buffer.value

    def process_command_line(self, process: int) -> str:
        required = wintypes.ULONG(0)
        self.ntdll.NtQueryInformationProcess(
            process,
            self.PROCESS_COMMAND_LINE_INFORMATION,
            None,
            0,
            ctypes.byref(required),
        )
        if required.value <= ctypes.sizeof(_UNICODE_STRING) or required.value > 1024 * 1024:
            raise ServerIdentityError("server process command line is unavailable")
        buffer = ctypes.create_string_buffer(required.value)
        status = self.ntdll.NtQueryInformationProcess(
            process,
            self.PROCESS_COMMAND_LINE_INFORMATION,
            buffer,
            required,
            ctypes.byref(required),
        )
        if status != 0:
            raise ServerIdentityError(
                f"server process command line query failed with NTSTATUS 0x{status & 0xFFFFFFFF:08x}"
            )
        value = ctypes.cast(buffer, ctypes.POINTER(_UNICODE_STRING)).contents
        if not value.Buffer or value.Length <= 0 or value.Length % 2:
            raise ServerIdentityError("server process command line is malformed")
        return ctypes.wstring_at(value.Buffer, value.Length // 2)

    def command_line_arguments(self, command_line: str) -> list[str]:
        count = ctypes.c_int(0)
        arguments = self.shell32.CommandLineToArgvW(command_line, ctypes.byref(count))
        if not arguments or count.value <= 0:
            raise self.last_error("CommandLineToArgvW")
        try:
            return [arguments[index] for index in range(count.value)]
        finally:
            self.kernel32.LocalFree(arguments)

    def sid_string(self, sid: int) -> str:
        output = wintypes.LPWSTR()
        if not self.advapi32.ConvertSidToStringSidW(sid, ctypes.byref(output)):
            raise self.last_error("ConvertSidToStringSidW")
        try:
            return output.value
        finally:
            self.kernel32.LocalFree(output)

    def token_user_and_groups(self, process: int) -> tuple[str, dict[str, int]]:
        token = wintypes.HANDLE()
        if not self.advapi32.OpenProcessToken(process, self.TOKEN_QUERY, ctypes.byref(token)):
            raise self.last_error("OpenProcessToken")
        try:
            user_buffer = self._token_information(token, self.TOKEN_USER)
            token_user = ctypes.cast(user_buffer, ctypes.POINTER(_TOKEN_USER)).contents
            user_sid = self.sid_string(token_user.User.Sid)

            group_buffer = self._token_information(token, self.TOKEN_GROUPS)
            group_count = ctypes.cast(
                group_buffer, ctypes.POINTER(wintypes.DWORD)
            ).contents.value
            base = ctypes.addressof(group_buffer) + _TOKEN_GROUPS_ONE.Groups.offset
            groups: dict[str, int] = {}
            for index in range(group_count):
                entry = _SID_AND_ATTRIBUTES.from_address(
                    base + index * ctypes.sizeof(_SID_AND_ATTRIBUTES)
                )
                groups[self.sid_string(entry.Sid)] = int(entry.Attributes)
            return user_sid, groups
        finally:
            self.close(token.value)

    def _token_information(self, token: int, information_class: int) -> Any:
        needed = wintypes.DWORD(0)
        self.advapi32.GetTokenInformation(
            token, information_class, None, 0, ctypes.byref(needed)
        )
        if ctypes.get_last_error() != self.ERROR_INSUFFICIENT_BUFFER or needed.value == 0:
            raise self.last_error("GetTokenInformation(size)")
        buffer = ctypes.create_string_buffer(needed.value)
        if not self.advapi32.GetTokenInformation(
            token,
            information_class,
            buffer,
            needed,
            ctypes.byref(needed),
        ):
            raise self.last_error("GetTokenInformation")
        return buffer

    def resolve_account_sid(self, account: str) -> str:
        sid_size = wintypes.DWORD(0)
        domain_size = wintypes.DWORD(0)
        sid_type = wintypes.DWORD(0)
        self.advapi32.LookupAccountNameW(
            None,
            account,
            None,
            ctypes.byref(sid_size),
            None,
            ctypes.byref(domain_size),
            ctypes.byref(sid_type),
        )
        if ctypes.get_last_error() != self.ERROR_INSUFFICIENT_BUFFER:
            raise self.last_error("LookupAccountNameW(size)")
        sid_buffer = ctypes.create_string_buffer(sid_size.value)
        domain = ctypes.create_unicode_buffer(max(domain_size.value, 1))
        if not self.advapi32.LookupAccountNameW(
            None,
            account,
            sid_buffer,
            ctypes.byref(sid_size),
            domain,
            ctypes.byref(domain_size),
            ctypes.byref(sid_type),
        ):
            raise self.last_error("LookupAccountNameW")
        return self.sid_string(ctypes.addressof(sid_buffer))

    def parent_process_id(self, process_id: int) -> int:
        snapshot = self.kernel32.CreateToolhelp32Snapshot(self.TH32CS_SNAPPROCESS, 0)
        if snapshot == self.INVALID_HANDLE_VALUE:
            raise self.last_error("CreateToolhelp32Snapshot")
        try:
            entry = _PROCESSENTRY32W()
            entry.dwSize = ctypes.sizeof(entry)
            ok = self.kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                if int(entry.th32ProcessID) == process_id:
                    return int(entry.th32ParentProcessID)
                ok = self.kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            self.close(snapshot)
        raise ServerIdentityError("server process is absent from the process snapshot")

    def service_process_id(self, service_name: str) -> int:
        manager = self.advapi32.OpenSCManagerW(None, None, self.SC_MANAGER_CONNECT)
        if not manager:
            raise self.last_error("OpenSCManagerW")
        service = None
        try:
            service = self.advapi32.OpenServiceW(
                manager, service_name, self.SERVICE_QUERY_STATUS
            )
            if not service:
                raise self.last_error("OpenServiceW")
            status = _SERVICE_STATUS_PROCESS()
            needed = wintypes.DWORD(0)
            raw = ctypes.cast(ctypes.byref(status), ctypes.POINTER(ctypes.c_ubyte))
            if not self.advapi32.QueryServiceStatusEx(
                service,
                self.SC_STATUS_PROCESS_INFO,
                raw,
                ctypes.sizeof(status),
                ctypes.byref(needed),
            ):
                raise self.last_error("QueryServiceStatusEx")
            if status.dwProcessId <= 0:
                raise ServerIdentityError("broker service is not running")
            return int(status.dwProcessId)
        finally:
            self.close(service)
            self.close(manager)


_WINDOWS_API: _WindowsApi | None = None


def _windows_api() -> _WindowsApi:
    global _WINDOWS_API
    if _WINDOWS_API is None:
        _WINDOWS_API = _WindowsApi()
    return _WINDOWS_API


def _same_windows_path(left: str, right: str) -> bool:
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


def _is_expected_broker_command(
    arguments: list[str], expected_script: str
) -> bool:
    if len(arguments) < 3 or "serve" not in arguments[1:]:
        return False
    return (
        sum(
            1
            for argument in arguments[1:]
            if _same_windows_path(argument, expected_script)
        )
        == 1
    )


def verify_broker_server_identity(
    pipe_handle: int,
    *,
    expected_executable: str,
    expected_broker_script: str,
    service_name: str = DEFAULT_SERVICE_NAME,
    expected_service_sid: str | None = None,
) -> VerifiedServerIdentity:
    """Verify the pipe endpoint against process, token, and SCM ground truth."""

    api = _windows_api()
    expected_path = str(Path(expected_executable).resolve(strict=True))
    if not _IMMUTABLE_PYTHON_RE.fullmatch(expected_path):
        raise ServerIdentityError(
            "expected broker executable is not in D:\\Releases\\fouler-play\\<commit>"
        )
    expected_script = str(Path(expected_broker_script).resolve(strict=True))
    if not _IMMUTABLE_BROKER_SCRIPT_RE.fullmatch(expected_script):
        raise ServerIdentityError(
            "expected broker script is not in D:\\Releases\\fouler-play\\<commit>"
        )
    if not _same_windows_path(
        str(Path(expected_path).parents[2]), str(Path(expected_script).parents[2])
    ):
        raise ServerIdentityError(
            "expected broker executable and script are from different immutable releases"
        )

    server_pid = wintypes.DWORD(0)
    if not api.kernel32.GetNamedPipeServerProcessId(pipe_handle, ctypes.byref(server_pid)):
        raise api.last_error("GetNamedPipeServerProcessId")
    if server_pid.value <= 0:
        raise ServerIdentityError("named pipe server PID is invalid")

    process = api.kernel32.OpenProcess(
        api.PROCESS_QUERY_LIMITED_INFORMATION, False, server_pid.value
    )
    if not process:
        raise api.last_error("OpenProcess(server)")
    try:
        creation = api.process_creation_filetime(process)
        executable = api.process_image(process)
        command_line = api.process_command_line(process)
        token_user, groups = api.token_user_and_groups(process)
    finally:
        api.close(process)

    if not _same_windows_path(executable, expected_path):
        raise ServerIdentityError("named pipe server executable does not match the immutable release")
    command_arguments = api.command_line_arguments(command_line)
    if not _is_expected_broker_command(command_arguments, expected_script):
        raise ServerIdentityError(
            "named pipe server command line is not the immutable broker serve command"
        )
    if token_user != "S-1-5-19":
        raise ServerIdentityError("named pipe server is not running as LocalService")

    resolved_service_sid = api.resolve_account_sid(f"NT SERVICE\\{service_name}")
    if expected_service_sid and resolved_service_sid != expected_service_sid:
        raise ServerIdentityError("installed broker service SID does not match the expected SID")
    attributes = groups.get(resolved_service_sid)
    if attributes is None:
        raise ServerIdentityError("broker process token does not contain its service SID")
    if not (attributes & api.SE_GROUP_ENABLED) or attributes & api.SE_GROUP_USE_FOR_DENY_ONLY:
        raise ServerIdentityError("broker service SID is not an enabled unrestricted token group")

    service_pid = api.service_process_id(service_name)
    parent_pid = api.parent_process_id(int(server_pid.value))
    if parent_pid != service_pid:
        raise ServerIdentityError("named pipe server is not the child of the installed NSSM service")

    return VerifiedServerIdentity(
        process_id=int(server_pid.value),
        process_creation_filetime=creation,
        executable=executable,
        token_user_sid=token_user,
        service_sid=resolved_service_sid,
        service_process_id=service_pid,
        parent_process_id=parent_pid,
        command_line=command_line,
        broker_script=expected_script,
    )


def _remaining_wait_ms(deadline: float) -> int:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return 0
    return min(0xFFFFFFFE, max(1, int(remaining * 1000 + 0.999)))


def _defer_overlapped_cleanup(
    api: _WindowsApi,
    event: int,
    overlapped: "_OVERLAPPED",
    keepalive: object,
) -> None:
    """Retain async-I/O storage until a late cancellation completes."""

    def reap() -> None:
        api.kernel32.WaitForSingleObject(event, api.INFINITE)
        api.close(event)
        # The closure deliberately retains overlapped and keepalive.
        _ = (overlapped, keepalive)

    threading.Thread(
        target=reap,
        name="fouler-pipe-io-cancel-reaper",
        daemon=True,
    ).start()


def _overlapped_transfer(
    handle: int,
    buffer: Any,
    length: int,
    *,
    write: bool,
    deadline: float,
) -> int:
    api = _windows_api()
    operation_name = "WriteFile" if write else "ReadFile"
    wait_ms = _remaining_wait_ms(deadline)
    if wait_ms == 0:
        raise ProtocolError("pipe_timeout", "named-pipe session deadline expired")

    event = api.kernel32.CreateEventW(None, True, False, None)
    if not event:
        raise api.last_error("CreateEventW")
    overlapped = _OVERLAPPED()
    overlapped.hEvent = event
    transferred = wintypes.DWORD(0)
    operation = api.kernel32.WriteFile if write else api.kernel32.ReadFile
    pending = False
    try:
        if operation(
            handle,
            buffer,
            length,
            ctypes.byref(transferred),
            ctypes.byref(overlapped),
        ):
            return int(transferred.value)

        code = ctypes.get_last_error()
        if code != api.ERROR_IO_PENDING:
            raise api.error_from_code(operation_name, code)
        pending = True
        wait_result = api.kernel32.WaitForSingleObject(
            event, _remaining_wait_ms(deadline)
        )
        if wait_result == api.WAIT_TIMEOUT:
            if not api.kernel32.CancelIoEx(handle, ctypes.byref(overlapped)):
                cancel_code = ctypes.get_last_error()
                if cancel_code != api.ERROR_NOT_FOUND:
                    raise api.error_from_code("CancelIoEx", cancel_code)
            if (
                api.kernel32.WaitForSingleObject(event, api.CANCELLATION_GRACE_MS)
                != api.WAIT_OBJECT_0
            ):
                _defer_overlapped_cleanup(api, event, overlapped, buffer)
                event = None
            pending = False
            raise ProtocolError("pipe_timeout", "named-pipe session deadline expired")
        if wait_result != api.WAIT_OBJECT_0:
            raise api.last_error("WaitForSingleObject")
        pending = False
        if not api.kernel32.GetOverlappedResult(
            handle,
            ctypes.byref(overlapped),
            ctypes.byref(transferred),
            False,
        ):
            code = ctypes.get_last_error()
            if code == api.ERROR_OPERATION_ABORTED:
                raise ProtocolError("pipe_timeout", "named-pipe I/O was cancelled")
            raise api.error_from_code("GetOverlappedResult", code)
        return int(transferred.value)
    finally:
        if pending:
            api.kernel32.CancelIoEx(handle, ctypes.byref(overlapped))
            if (
                api.kernel32.WaitForSingleObject(event, api.CANCELLATION_GRACE_MS)
                != api.WAIT_OBJECT_0
            ):
                _defer_overlapped_cleanup(api, event, overlapped, buffer)
                event = None
        if event:
            api.close(event)


def _read_exact_handle(handle: int, length: int, *, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = length
    while remaining:
        amount = min(remaining, 16 * 1024)
        buffer = ctypes.create_string_buffer(amount)
        received = _overlapped_transfer(
            handle, buffer, amount, write=False, deadline=deadline
        )
        if received == 0:
            raise ProtocolError("truncated_frame", "pipe closed before the frame completed")
        chunks.append(buffer.raw[:received])
        remaining -= received
    return b"".join(chunks)


def _write_all_handle(handle: int, content: bytes, *, deadline: float) -> None:
    offset = 0
    while offset < len(content):
        chunk = content[offset : offset + 16 * 1024]
        buffer = ctypes.create_string_buffer(chunk)
        written = _overlapped_transfer(
            handle, buffer, len(chunk), write=True, deadline=deadline
        )
        if written == 0:
            raise OSError("WriteFile completed without progress")
        offset += written


class LeaseBrokerClient:
    """One-request-per-connection client with mandatory server verification."""

    def __init__(
        self,
        *,
        pipe_name: str = PIPE_NAME,
        expected_server_executable: str | os.PathLike[str] | None = None,
        expected_broker_script: str | os.PathLike[str] | None = None,
        service_name: str = DEFAULT_SERVICE_NAME,
        expected_service_sid: str | None = None,
        timeout_ms: int = 10_000,
    ) -> None:
        self.pipe_name = pipe_name
        self.expected_server_executable = str(expected_server_executable or sys.executable)
        self.expected_broker_script = str(
            expected_broker_script
            or Path(__file__).resolve().parent / "windows" / "fouler_lease_broker.py"
        )
        self.service_name = service_name
        self.expected_service_sid = expected_service_sid
        self.timeout_ms = int(timeout_ms)
        self.last_server_identity: VerifiedServerIdentity | None = None
        if self.timeout_ms <= 0 or self.timeout_ms > 120_000:
            raise ValueError("timeout_ms must be between 1 and 120000")

    def request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        api = _windows_api()
        frame = encode_frame(dict(payload))
        deadline = time.monotonic() + self.timeout_ms / 1000
        if not api.kernel32.WaitNamedPipeW(
            self.pipe_name, _remaining_wait_ms(deadline)
        ):
            raise api.last_error("WaitNamedPipeW")
        handle = api.kernel32.CreateFileW(
            self.pipe_name,
            api.FILE_READ_DATA | api.FILE_WRITE_DATA,
            0,
            None,
            api.OPEN_EXISTING,
            api.FILE_FLAG_OVERLAPPED
            | api.SECURITY_SQOS_PRESENT
            | api.SECURITY_IDENTIFICATION,
            None,
        )
        if handle == api.INVALID_HANDLE_VALUE:
            raise api.last_error("CreateFileW(pipe)")
        try:
            self.last_server_identity = verify_broker_server_identity(
                handle,
                expected_executable=self.expected_server_executable,
                expected_broker_script=self.expected_broker_script,
                service_name=self.service_name,
                expected_service_sid=self.expected_service_sid,
            )
            _write_all_handle(handle, frame, deadline=deadline)
            response = read_framed(
                lambda count: _read_exact_handle(handle, count, deadline=deadline)
            )
        finally:
            api.close(handle)
        if not isinstance(response, dict):
            raise ProtocolError("invalid_response", "broker response must be a JSON object")
        if response.get("schemaVersion") != RESPONSE_VERSION:
            raise ProtocolError("invalid_response", "broker response schema is unsupported")
        return response


def new_request_id(prefix: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_.:-]+", "-", str(prefix or "request")).strip("-.")
    normalized = normalized[:120] or "request"
    return f"{normalized}-{uuid.uuid4().hex}"


def broker_request_payload(
    action: str,
    *,
    authorization_digest: str,
    lease_id: str,
    request_id: str | None = None,
    **fields: Any,
) -> dict[str, Any]:
    payload = {
        "schemaVersion": PROTOCOL_VERSION,
        "action": str(action),
        "requestId": request_id or new_request_id(action),
        "authorizationDigest": str(authorization_digest),
        "leaseId": str(lease_id),
        **fields,
    }
    canonical_json_bytes(payload)
    return payload


def require_exact_reservation_binding(
    result: Mapping[str, Any], expected: Mapping[str, Any]
) -> dict[str, Any]:
    """Fail closed unless a broker result echoes the exact launch authority."""

    if not isinstance(result, Mapping):
        raise ProtocolError("invalid_response", "broker result must be an object")
    missing = [name for name in RESERVATION_BINDING_FIELDS if name not in expected]
    if missing:
        raise ValueError("expected reservation binding is incomplete: " + ", ".join(missing))
    mismatches = [
        name
        for name in RESERVATION_BINDING_FIELDS
        if result.get(name) != expected.get(name)
    ]
    if mismatches:
        raise ProtocolError(
            "reservation_binding_mismatch",
            "broker response does not match reservation binding: "
            + ", ".join(mismatches),
        )
    return {name: result[name] for name in RESERVATION_BINDING_FIELDS}


def _validate_response_for_request(
    response: Mapping[str, Any], payload: Mapping[str, Any]
) -> dict[str, Any]:
    if response.get("requestId") != payload.get("requestId"):
        raise ProtocolError("invalid_response", "broker response requestId does not match")
    if response.get("action") != payload.get("action"):
        raise ProtocolError("invalid_response", "broker response action does not match")
    return dict(response)


def _recover_committed_response(
    payload: Mapping[str, Any], broker: LeaseBrokerClient, *, attempts: int
) -> dict[str, Any] | None:
    if payload.get("action") == "status":
        return None
    target_request_id = str(payload.get("requestId") or "")
    if not target_request_id:
        return None
    status_payload = broker_request_payload(
        "status",
        authorization_digest=str(payload.get("authorizationDigest") or ""),
        lease_id=str(payload.get("leaseId") or ""),
        request_id=new_request_id("status-request"),
        lookupType="request",
        lookupId=target_request_id,
    )
    for _attempt in range(attempts):
        try:
            status_response = _validate_response_for_request(
                broker.request(status_payload), status_payload
            )
        except (OSError, ProtocolError):
            continue
        if not status_response.get("ok"):
            continue
        status_result = (
            status_response.get("result")
            if isinstance(status_response.get("result"), Mapping)
            else {}
        )
        if not status_result.get("found"):
            return None
        original = status_result.get("response")
        if not isinstance(original, Mapping):
            raise ProtocolError(
                "invalid_response", "status lookup returned a malformed original response"
            )
        if original.get("schemaVersion") != RESPONSE_VERSION:
            raise ProtocolError(
                "invalid_response", "recovered broker response schema is unsupported"
            )
        return _validate_response_for_request(original, payload)
    return None


def request_with_retry(
    payload: Mapping[str, Any],
    *,
    client: LeaseBrokerClient | None = None,
    attempts: int = 2,
) -> dict[str, Any]:
    """Retry an identical request so a lost post-commit response stays idempotent."""

    if attempts < 1 or attempts > 3:
        raise ValueError("attempts must be between 1 and 3")
    broker = client or LeaseBrokerClient()
    last_error: Exception | None = None
    for _attempt in range(attempts):
        try:
            response = broker.request(payload)
        except (OSError, ProtocolError) as exc:
            last_error = exc
            continue
        return _validate_response_for_request(response, payload)
    recovered = _recover_committed_response(payload, broker, attempts=attempts)
    if recovered is not None:
        return recovered
    assert last_error is not None
    raise last_error


def response_error_text(response: Mapping[str, Any]) -> str:
    error = response.get("error") if isinstance(response.get("error"), Mapping) else {}
    code = str(error.get("code") or "broker_rejected")
    message = str(error.get("message") or "lease broker rejected the request")
    return f"{code}: {message}"


def _read_request_file(path: str) -> dict[str, Any]:
    source = sys.stdin.buffer.read(MAX_FRAME_BYTES + 1) if path == "-" else Path(path).read_bytes()
    if len(source) > MAX_FRAME_BYTES:
        raise ProtocolError("frame_too_large", "request JSON exceeds the size limit")
    payload = strict_json_loads(source)
    if not isinstance(payload, dict):
        raise ProtocolError("invalid_request", "request JSON must be an object")
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Call the local Fouler lease broker named pipe.")
    parser.add_argument("--request-json", required=True, help="Strict JSON file, or - for stdin")
    parser.add_argument("--pipe-name", default=PIPE_NAME)
    parser.add_argument("--service-name", default=DEFAULT_SERVICE_NAME)
    parser.add_argument("--expected-server-executable", default=sys.executable)
    parser.add_argument("--expected-service-sid")
    parser.add_argument("--timeout-ms", type=int, default=10_000)
    args = parser.parse_args(argv)
    try:
        payload = _read_request_file(args.request_json)
        client = LeaseBrokerClient(
            pipe_name=args.pipe_name,
            expected_server_executable=args.expected_server_executable,
            service_name=args.service_name,
            expected_service_sid=args.expected_service_sid,
            timeout_ms=args.timeout_ms,
        )
        response = client.request(payload)
    except (OSError, PermissionError, ProtocolError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(response, indent=2, sort_keys=True))
    return 0 if response.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
