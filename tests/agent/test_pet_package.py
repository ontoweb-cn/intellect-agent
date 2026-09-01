"""Tests for the pets package (PT V1) — store traversal guard, manifest
host-pin + local fallback, deterministic unicode rendering, config select."""


import json
import pytest

from agent.pet import manifest as pet_manifest
from agent.pet import render as pet_render
from agent.pet import state as pet_state
from agent.pet import store as pet_store


@pytest.fixture
def pet_home(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    return tmp_path


# ── store ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("bad", ["../../etc", "..", "", "a/b", ".hidden", "a" * 100])
def test_safe_slug_rejects_traversal(bad):
    assert pet_store.safe_slug(bad) is None


def test_install_and_load_roundtrip(pet_home):
    path = pet_store.install("nova", {"spritesheet.webp": b"\x89PNG"},
                             {"name": "Nova"})
    assert path is not None
    meta = pet_store.load_pet("nova")
    assert meta["name"] == "Nova"
    assert (path / "spritesheet.webp").read_bytes() == b"\x89PNG"
    assert pet_store.installed()[0]["slug"] == "nova"


def test_asset_path_escape_rejected(pet_home):
    path = pet_store.install("escape", {"../evil.txt": b"nope"}, {"name": "x"})
    assert path is not None
    assert not (pet_home / "evil.txt").exists()


# ── manifest ─────────────────────────────────────────────────────────────

def test_manifest_host_pin_rejects_foreign_assets(pet_home, monkeypatch):
    import urllib.request

    payload = {"pets": [{"slug": "evil", "spritesheet_url":
                         "https://evil.example/sheet.webp"}]}

    class _Resp:
        def read(self):
            return json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: _Resp())
    # network fail → falls back to local manifest (no evil entry)
    result = pet_manifest.fetch_manifest()
    assert result is None or all(
        p.get("slug") != "evil" for p in result.get("pets", [])
    )


def test_manifest_local_fallback_lists_installed(pet_home):
    pet_store.install("localpet", {}, {"name": "Local"})
    result = pet_manifest.fetch_manifest(timeout=0.01, allow_stale=True)
    assert result is not None
    assert result.get("source") == "local"
    assert any(p["slug"] == "localpet" for p in result["pets"])


# ── render / state ───────────────────────────────────────────────────────

def test_render_deterministic_per_inputs():
    a1 = pet_render.render_unicode("nova", "idle", 0)
    a2 = pet_render.render_unicode("nova", "idle", 0)
    assert a1 == a2
    assert pet_render.render_unicode("nova", "walk", 0) != a1


def test_state_machine_priority():
    pet_state.reset()
    assert pet_state.current_state() == "idle"
    pet_state.mark_activity()
    assert pet_state.current_state() in ("walk", "alert")
    pet_state.reset()
    assert pet_state.current_state() == "idle"
