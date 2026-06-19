"""Tests for Windows webui daemon launch configuration."""

from pathlib import Path
from unittest.mock import patch

import pytest

import intellect_cli.gateway_windows as gateway_windows
import intellect_cli.webui as webui


@pytest.mark.parametrize("platform", ["win32"])
def test_webui_start_puts_project_root_on_pythonpath(monkeypatch, tmp_path, platform):
    """Detached webui must expose the agent project root for ``-m webui.server``."""

    project = tmp_path / "project"
    scripts = project / "venv" / "Scripts"
    site_packages = project / "venv" / "Lib" / "site-packages"
    base = tmp_path / "uv" / "python" / "cpython-3.12-windows-x86_64-none"
    scripts.mkdir(parents=True)
    site_packages.mkdir(parents=True)
    base.mkdir(parents=True)

    venv_python = scripts / "python.exe"
    base_pythonw = base / "pythonw.exe"
    for exe in (venv_python, base_pythonw):
        exe.write_text("", encoding="utf-8")
    (project / "venv" / "pyvenv.cfg").write_text(
        f"home = {base}\nimplementation = CPython\nuv = 0.11.14\nversion_info = 3.12.10\n",
        encoding="utf-8",
    )

    intellect_home = tmp_path / "intellect-home"
    intellect_home.mkdir()
    captured = {}

    class _FakeProcess:
        pid = 4242

        def poll(self):
            return None

    def _fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _FakeProcess()

    monkeypatch.setattr(webui.sys, "platform", platform)
    monkeypatch.setattr(webui.sys, "executable", str(venv_python))
    monkeypatch.setattr(webui, "_INTELLECT_HOME", intellect_home)
    monkeypatch.setattr(webui, "_LOG_FILE", intellect_home / "webui.log")
    monkeypatch.setattr(webui, "_PID_FILE", intellect_home / "webui.pid")
    monkeypatch.setattr(webui, "_STATE_FILE", intellect_home / "webui.ctl.env")
    monkeypatch.setattr(webui, "_get_running_pid", lambda: None)
    monkeypatch.setattr(webui, "_is_pid_alive", lambda _pid: True)
    monkeypatch.setattr(webui.subprocess, "Popen", _fake_popen)
    monkeypatch.setattr(
        "intellect_cli.config.get_project_root",
        lambda: project,
    )
    monkeypatch.setattr(
        "intellect_cli.config.get_intellect_home",
        lambda: str(intellect_home),
    )

    webui.webui_start(type("Args", (), {"host": "127.0.0.1", "port": 9119})())

    assert captured["argv"][:4] == [str(base_pythonw), "-P", "-m", "webui.server"]
    assert captured["kwargs"]["cwd"] == str(project)
    env = captured["kwargs"]["env"]
    assert env["VIRTUAL_ENV"] == str(project / "venv")
    pythonpath = env["PYTHONPATH"].split(gateway_windows.os.pathsep)
    assert str(project) in pythonpath
    assert str(site_packages) in pythonpath
    assert pythonpath.index(str(site_packages)) < pythonpath.index(str(project))
