#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.modules.setdefault("_wmi", None)
import psutil


def _command(process: psutil.Process) -> str:
    try:
        return " ".join(process.cmdline()).lower().replace("/", "\\")
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return ""


def _cwd(process: psutil.Process) -> str:
    try:
        return os.path.normcase(os.path.abspath(process.cwd()))
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess, OSError):
        return ""


def _belongs_to_project(process: psutil.Process, project: str) -> bool:
    command = _command(process)
    return _cwd(process) == project or project.lower().replace("/", "\\") in command


def discover_roots(project_dir: Path) -> list[psutil.Process]:
    project = os.path.normcase(os.path.abspath(project_dir))
    leaves: list[psutil.Process] = []
    for process in psutil.process_iter(["pid", "name"]):
        name = str(process.info.get("name") or "").lower()
        if name not in {"python.exe", "pythonw.exe", "cmd.exe", "powershell.exe", "pwsh.exe"}:
            continue
        command = _command(process)
        if "serve_obs_page.py" in command and _belongs_to_project(process, project):
            leaves.append(process)

    roots: dict[int, psutil.Process] = {}
    for leaf in leaves:
        root = leaf
        while True:
            try:
                parent = root.parent()
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                break
            if parent is None:
                break
            name = (parent.name() or "").lower()
            command = _command(parent)
            if name in {"nssm.exe", "services.exe", "svchost.exe"}:
                break
            if name not in {"python.exe", "pythonw.exe", "cmd.exe", "powershell.exe", "pwsh.exe"}:
                break
            if not (_belongs_to_project(parent, project) or "start_obs_server_task.ps1" in command):
                break
            root = parent
        roots[root.pid] = root
    return [roots[pid] for pid in sorted(roots)]


def describe(process: psutil.Process) -> dict[str, object]:
    try:
        return {
            "pid": process.pid,
            "ppid": process.ppid(),
            "name": process.name(),
            "command": _command(process),
        }
    except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
        return {"pid": process.pid, "unavailable": True}


def stop_roots(roots: list[psutil.Process]) -> list[int]:
    targets: dict[int, psutil.Process] = {}
    for root in roots:
        try:
            for child in root.children(recursive=True):
                targets[child.pid] = child
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
        targets[root.pid] = root
    ordered = sorted(targets.values(), key=lambda process: process.pid, reverse=True)
    for process in ordered:
        try:
            process.terminate()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    _gone, alive = psutil.wait_procs(ordered, timeout=5)
    for process in alive:
        try:
            process.kill()
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass
    psutil.wait_procs(alive, timeout=5)
    return sorted(targets)


def main() -> int:
    parser = argparse.ArgumentParser(description="Stop only this repo's Fouler OBS process trees.")
    parser.add_argument("--project-dir", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()

    roots = discover_roots(args.project_dir.resolve())
    payload: dict[str, object] = {
        "projectDir": str(args.project_dir.resolve()),
        "execute": args.execute,
        "roots": [describe(process) for process in roots],
        "rootCount": len(roots),
        "stoppedPids": [],
    }
    if args.execute:
        payload["stoppedPids"] = stop_roots(roots)
    print(json.dumps(payload, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
