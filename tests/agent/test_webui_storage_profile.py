"""WebUI storage bridge — profile isolation without os.environ races."""

from __future__ import annotations

import threading

import pytest


@pytest.fixture
def two_profiles(tmp_path, monkeypatch):
    monkeypatch.setattr("intellect_cli.profiles.Path.home", lambda: tmp_path)
    root = tmp_path / ".intellect"
    root.mkdir()
    monkeypatch.setenv("INTELLECT_HOME", str(root))
    for name in ("work", "personal"):
        profile_dir = root / "profiles" / name
        profile_dir.mkdir(parents=True)
        (profile_dir / "config.yaml").write_text(
            f"display:\n  skin: {name}\n",
            encoding="utf-8",
        )
    return root


def test_webui_profile_scope_sets_context_override(two_profiles):
    from agent.webui_storage import load_config_for_webui, webui_profile_scope
    from intellect_constants import get_intellect_home

    with webui_profile_scope("work"):
        cfg = load_config_for_webui()
        assert cfg["display"]["skin"] == "work"
        assert get_intellect_home() == two_profiles / "profiles" / "work"

    assert get_intellect_home() == two_profiles


def test_load_config_for_webui_requires_scope_when_profile_passed(two_profiles):
    from agent.webui_storage import load_config_for_webui

    with pytest.raises(RuntimeError, match="webui_profile_scope"):
        load_config_for_webui(profile="work")


def test_concurrent_profiles_do_not_cross_contaminate(two_profiles, monkeypatch):
    from agent.webui_storage import load_config_for_webui, webui_profile_scope
    from intellect_constants import get_intellect_home

    monkeypatch.setenv("INTELLECT_HOME", str(two_profiles))
    errors: list[str] = []
    barrier = threading.Barrier(2)

    def worker(profile: str, expected_skin: str) -> None:
        try:
            with webui_profile_scope(profile):
                barrier.wait(timeout=5)
                cfg = load_config_for_webui()
                home = get_intellect_home()
                if cfg["display"]["skin"] != expected_skin:
                    errors.append(f"{profile}: skin={cfg['display']['skin']!r}")
                expected_home = two_profiles / "profiles" / profile
                if home != expected_home:
                    errors.append(f"{profile}: home={home}")
        except Exception as exc:
            errors.append(f"{profile}: {exc}")

    threads = [
        threading.Thread(target=worker, args=("work", "work")),
        threading.Thread(target=worker, args=("personal", "personal")),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    assert not errors, errors
