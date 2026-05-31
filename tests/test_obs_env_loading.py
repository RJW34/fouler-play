import os

from streaming import serve_obs_page


def test_hermes_obs_env_overrides_repo_local_dotenv(monkeypatch, tmp_path):
    appdata = tmp_path / "roaming"
    secret_dir = appdata / "hermes-devstream"
    secret_dir.mkdir(parents=True)
    (secret_dir / "secrets.env").write_text(
        "OBS_WS_HOST=192.168.1.126\nOBS_WS_PORT=4455\nOBS_WS_PASSWORD=not-secret-in-test\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("OBS_WS_HOST", "127.0.0.1")
    monkeypatch.delenv("OBS_WS_PORT", raising=False)

    serve_obs_page._load_hermes_obs_env()

    assert os.environ["OBS_WS_HOST"] == "192.168.1.126"
    assert os.environ["OBS_WS_PORT"] == "4455"
    assert os.environ["OBS_WS_PASSWORD"] == "not-secret-in-test"
