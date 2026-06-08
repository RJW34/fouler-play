import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import devstream_session


def test_start_process_rotates_oversized_child_log_before_append(tmp_path, monkeypatch):
    pid_dir = tmp_path / ".pids"
    pid_file = pid_dir / "devstream_battle_session.pid"
    log_path = tmp_path / "logs" / "devstream_battle_session.log"
    rotated_path = tmp_path / "logs" / "devstream_battle_session.log.old"
    oversized_log = "x" * 17
    child_line = "new child output\n"
    env = {devstream_session.CHILD_LOG_MAX_BYTES_ENV: "16"}

    log_path.parent.mkdir(parents=True)
    log_path.write_text(oversized_log, encoding="utf-8")
    rotated_path.write_text("previous rotation", encoding="utf-8")

    monkeypatch.setattr(devstream_session, "ROOT", tmp_path)
    monkeypatch.setattr(devstream_session, "PID_DIR", pid_dir)
    monkeypatch.setattr(devstream_session, "BATTLE_PID_FILE", pid_file)
    monkeypatch.setattr(devstream_session, "existing_battle_runner_start_result", lambda command: None)
    monkeypatch.setattr(devstream_session, "pid_alive", lambda path: (False, None))
    monkeypatch.setattr(devstream_session, "_find_existing_process", lambda command: None)

    class FakeProc:
        pid = 50001

    def fake_spawn(command, **kwargs):
        stdout = kwargs["stdout"]
        stdout.write(child_line)
        stdout.flush()
        return FakeProc()

    monkeypatch.setattr(devstream_session.subprocess, "Popen", fake_spawn)

    payload = devstream_session.start_process(["python", "run.py"], pid_file, env)

    assert payload["pid"] == 50001
    assert payload["logRotation"] == {
        "path": str(log_path),
        "rotatedTo": str(rotated_path),
        "previousBytes": len(oversized_log),
        "maxBytes": 16,
    }
    assert log_path.read_text(encoding="utf-8") == child_line
    assert rotated_path.read_text(encoding="utf-8") == oversized_log

    pid_payload = json.loads(pid_file.read_text(encoding="utf-8"))
    assert pid_payload["pid"] == 50001
