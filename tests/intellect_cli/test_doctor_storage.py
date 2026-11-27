"""Tests for ``intellect doctor --storage``."""

from __future__ import annotations




def test_doctor_storage_sqlite_backend(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
    home = tmp_path / ".intellect"
    home.mkdir()
    (home / "config.yaml").write_text(
        "storage:\n  backend: sqlite\nmembers:\n  enabled: true\n",
        encoding="utf-8",
    )
    import intellect_cli.doctor as doctor_mod

    monkeypatch.setenv("INTELLECT_HOME", str(home))
    monkeypatch.setattr(doctor_mod, "INTELLECT_HOME", home)
    monkeypatch.setattr(doctor_mod, "_DHH", str(home))

    rc = doctor_mod.run_doctor_storage()
    out = capsys.readouterr().out
    assert rc == 0
    assert "storage backend: sqlite" in out


