from __future__ import annotations

import atexit
import json
import os
import signal
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil
except Exception:  # pragma: no cover - psutil is present in the runtime env.
    psutil = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]
PID_DIR = PROJECT_ROOT / ".pids"
DEFAULT_LEASE_NAME = "fouler-runtime-lane"
LEASE_TOKEN_ENV = "FOULER_RUNTIME_LEASE_TOKEN"
LEASE_NAME_ENV = "FOULER_RUNTIME_LEASE_NAME"


class RuntimeLeaseBusy(RuntimeError):
    def __init__(self, path: Path, holder: dict[str, Any] | None):
        self.path = path
        self.holder = holder or {}
        holder_text = json.dumps(self.holder, sort_keys=True, default=str)[:500]
        super().__init__(f"runtime lease busy at {path}: {holder_text}")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if psutil is not None and hasattr(psutil, "pid_exists") and hasattr(psutil, "Process"):
        try:
            if not psutil.pid_exists(pid):
                return False
            proc = psutil.Process(pid)
            return proc.is_running() and proc.status() != getattr(psutil, "STATUS_ZOMBIE", "zombie")
        except psutil.NoSuchProcess:
            return False
        except psutil.ZombieProcess:
            return False
        except (psutil.AccessDenied, OSError):
            return True
    if os.name == "nt":
        try:
            import ctypes

            handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def _process_snapshot(pid: int) -> dict[str, Any]:
    snap: dict[str, Any] = {"pid": pid, "alive": _pid_alive(pid)}
    if psutil is None or not snap["alive"]:
        return snap
    try:
        proc = psutil.Process(pid)
        snap["createTime"] = proc.create_time()
        snap["cwd"] = proc.cwd()
        snap["cmdline"] = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
        pass
    return snap


def read_lease_metadata(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8-sig").strip()
        return json.loads(raw) if raw else None
    except Exception:
        return None


class RuntimeLease:
    def __init__(
        self,
        *,
        name: str = DEFAULT_LEASE_NAME,
        holder: str,
        lease_dir: Path | None = None,
        inherited_token: str | None = None,
    ):
        self.name = name
        self.holder = holder
        self.lease_dir = Path(lease_dir) if lease_dir is not None else PID_DIR
        self.path = self.lease_dir / f"{name}.lease.json"
        self.token = uuid.uuid4().hex
        self.inherited_token = inherited_token if inherited_token is not None else os.getenv(LEASE_TOKEN_ENV)
        self.acquired = False
        self.reentrant = False
        self._previous_token: str | None = None
        self._previous_name: str | None = None

    def _metadata(self) -> dict[str, Any]:
        return {
            "schemaVersion": "fouler-play-runtime-lease/v1",
            "name": self.name,
            "holder": self.holder,
            "pid": os.getpid(),
            "token": self.token,
            "cwd": str(PROJECT_ROOT),
            "argv": sys.argv,
            "createdAt": _utcnow(),
        }

    def _write_new(self) -> bool:
        self.lease_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self._metadata(), indent=2, sort_keys=True) + "\n"
        try:
            fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        self.acquired = True
        self._arm_release()
        return True

    def acquire(self) -> RuntimeLease:
        for _attempt in range(3):
            if self._write_new():
                self._export_env()
                return self
            existing = read_lease_metadata(self.path)
            if not existing:
                try:
                    self.path.unlink()
                except OSError:
                    pass
                continue
            existing_pid = int(existing.get("pid") or 0)
            if (
                self.inherited_token
                and existing.get("token") == self.inherited_token
                and _pid_alive(existing_pid)
            ):
                self.token = self.inherited_token
                self.reentrant = True
                self._export_env()
                return self
            if not _pid_alive(existing_pid):
                try:
                    self.path.unlink()
                except OSError:
                    pass
                continue
            existing["process"] = _process_snapshot(existing_pid)
            raise RuntimeLeaseBusy(self.path, existing)
        existing = read_lease_metadata(self.path)
        raise RuntimeLeaseBusy(self.path, existing)

    def _export_env(self) -> None:
        if self._previous_token is None:
            self._previous_token = os.getenv(LEASE_TOKEN_ENV)
        if self._previous_name is None:
            self._previous_name = os.getenv(LEASE_NAME_ENV)
        os.environ[LEASE_TOKEN_ENV] = self.token
        os.environ[LEASE_NAME_ENV] = self.name

    def _restore_env(self) -> None:
        if self._previous_token is None:
            os.environ.pop(LEASE_TOKEN_ENV, None)
        else:
            os.environ[LEASE_TOKEN_ENV] = self._previous_token
        if self._previous_name is None:
            os.environ.pop(LEASE_NAME_ENV, None)
        else:
            os.environ[LEASE_NAME_ENV] = self._previous_name

    def _arm_release(self) -> None:
        atexit.register(self.release)
        if os.name != "nt":
            def _signal_release(signum, _frame):
                self.release()
                raise SystemExit(128 + int(signum))

            for signum in (signal.SIGTERM, signal.SIGINT):
                try:
                    signal.signal(signum, _signal_release)
                except Exception:
                    pass

    def release(self) -> None:
        if not self.acquired:
            self._restore_env()
            return
        existing = read_lease_metadata(self.path)
        if existing and existing.get("token") == self.token and int(existing.get("pid") or 0) == os.getpid():
            try:
                self.path.unlink()
            except OSError:
                pass
        self.acquired = False
        self._restore_env()

    def __enter__(self) -> RuntimeLease:
        return self.acquire()

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        self.release()


def acquire_runtime_lease(
    *,
    holder: str,
    name: str = DEFAULT_LEASE_NAME,
    lease_dir: Path | None = None,
) -> RuntimeLease:
    lease = RuntimeLease(name=name, holder=holder, lease_dir=lease_dir)
    return lease.acquire()


def runtime_lease(
    *,
    holder: str,
    name: str = DEFAULT_LEASE_NAME,
    lease_dir: Path | None = None,
) -> RuntimeLease:
    return RuntimeLease(name=name, holder=holder, lease_dir=lease_dir)


def lease_status(name: str = DEFAULT_LEASE_NAME, lease_dir: Path | None = None) -> dict[str, Any]:
    directory = Path(lease_dir) if lease_dir is not None else PID_DIR
    path = directory / f"{name}.lease.json"
    metadata = read_lease_metadata(path)
    if not metadata:
        return {"path": str(path), "present": path.exists(), "alive": False}
    pid = int(metadata.get("pid") or 0)
    return {
        "path": str(path),
        "present": True,
        "alive": _pid_alive(pid),
        "metadata": metadata,
    }
