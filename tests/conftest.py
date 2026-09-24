import pytest

from runboard.server import Server


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNBOARD_HOME", str(tmp_path / "home"))
    for k in ("RUNBOARD_SERVER", "RUNBOARD_TOKEN", "RUNBOARD_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.chdir(tmp_path)


@pytest.fixture
def server(tmp_path):
    srv = Server(tmp_path / "runs", "secret", host="127.0.0.1", port=0).start()
    yield srv
    srv.stop()
