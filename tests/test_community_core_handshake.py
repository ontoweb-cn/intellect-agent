"""Tests for the intellect_community_core binding handshake (P0-1)."""



def test_extension_reports_version():
    """A freshly built extension exposes rust_core_version + RUST_CORE_VERSION."""
    import intellect_community_core as c

    assert callable(c.rust_core_version)
    v = c.rust_core_version()
    assert isinstance(v, str) and v.count(".") == 2, v
    assert c.RUST_CORE_VERSION == v


def test_version_matches_cargo_toml():
    """Wrapper expectation tracks rust-core/Cargo.toml (P0-1 handshake)."""
    import pathlib

    import intellect_community_core as c

    cargo = pathlib.Path(__file__).resolve().parents[2] / "rust-core" / "Cargo.toml"
    if not cargo.exists():  # installed-wheel context: skip source check
        return
    text = cargo.read_text(encoding="utf-8")
    expected = text.split('version = "', 1)[1].split('"', 1)[0]
    assert c.RUST_CORE_VERSION == expected


def test_no_mismatch_warning_on_current_tree():
    """Importing the current-tree build must not warn about drift."""
    import subprocess
    import sys

    code = (
        "import warnings; warnings.simplefilter('error'); "
        "import intellect_community_core"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=120
    )
    assert proc.returncode == 0, proc.stderr
