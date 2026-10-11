from runboard.doctor import doctor


def test_doctor_passes_against_a_local_server(server, capsys):
    assert doctor(server=f"http://127.0.0.1:{server.port}", token="secret") == 0
    out = capsys.readouterr().out
    assert out.strip().endswith("PASS") and "secret" not in out and "from argument" in out
    assert len(server.storage.read_rows("runboard", "doctor")[0]) == 3


def test_doctor_reports_a_rejected_token(server, capsys):
    assert doctor(server=f"http://127.0.0.1:{server.port}", token="wrong-token") == 1
    out = capsys.readouterr().out
    assert "FAIL:" in out and "rejected the token" in out and "fix:" in out and "wrong-token" not in out


def test_doctor_reports_a_refused_connection(capsys):
    assert doctor(server="http://127.0.0.1:9", token="secret") == 1
    assert "refused" in capsys.readouterr().out


def test_doctor_checks_file_mode(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("RUNBOARD_DIR", str(tmp_path / "shared"))
    assert doctor() == 0
    out = capsys.readouterr().out
    assert "from RUNBOARD_DIR" in out and out.strip().endswith("PASS")


def test_doctor_notes_missing_configuration(capsys):
    assert doctor() == 0
    assert "no server is configured" in capsys.readouterr().out


def test_doctor_fails_on_half_configuration(monkeypatch, capsys):
    monkeypatch.setenv("RUNBOARD_SERVER", "http://example.invalid")
    assert doctor() == 1
    assert "not both" in capsys.readouterr().out
