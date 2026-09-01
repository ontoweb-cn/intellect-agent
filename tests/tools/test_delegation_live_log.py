"""Tests for the delegation live transcript (G-12 / A2-3②)."""


import pytest

from tools import delegation_live_log as ll


@pytest.fixture()
def live_home(tmp_path, monkeypatch):
    monkeypatch.setenv("INTELLECT_HOME", str(tmp_path))
    # live_transcript_root reads get_intellect_home() lazily — verify.
    assert ll.live_transcript_root() == tmp_path / "cache" / "delegation" / "live"
    return tmp_path


def test_writer_creates_dir_and_header(live_home):
    w = ll.create_live_transcript("sa-0-abc", "run the tests")
    assert w is not None
    text = w.path.read_text(encoding="utf-8")
    assert "sa-0-abc" in text
    assert "tail -f" in text
    assert "run the tests" in text


def test_observe_tool_events_written_and_redacted(live_home):
    w = ll.create_live_transcript("sa-1", "goal")
    w.observe("tool.start", "terminal", "pytest -q")
    w.observe(
        "tool.complete", "terminal", "3 passed in 0.1s",
        duration=0.1, is_error=False,
    )
    w.finalize("completed")
    text = w.path.read_text(encoding="utf-8")
    assert "→ terminal pytest -q" in text
    assert "← terminal ok (0.1s): 3 passed" in text
    assert "final    | completed" in text


def test_redaction_withholds_line_on_redactor_failure(live_home, monkeypatch):
    import agent.redact as redact_mod

    def _boom(*a, **k):
        raise RuntimeError("redactor down")

    monkeypatch.setattr(redact_mod, "redact_sensitive_text", _boom)
    w = ll.create_live_transcript("sa-2", "goal")
    w.observe("tool.start", "terminal", "SECRET-CONTENT")
    text = w.path.read_text(encoding="utf-8")
    assert "SECRET-CONTENT" not in text
    assert "line withheld" in text


def test_stream_buffer_flushes_on_finalize(live_home):
    w = ll.create_live_transcript("sa-3", "goal")
    w.observe("assistant.delta", text="partial ")
    w.observe("assistant.delta", text="streaming text")
    assert "partial" not in w.path.read_text(encoding="utf-8")  # buffered
    w.finalize("completed")
    text = w.path.read_text(encoding="utf-8")
    assert "partial streaming text" in text


def test_small_results_never_stub_style_budget(live_home):
    w = ll.create_live_transcript("sa-4", "goal")
    long_line = "z" * 5000
    w.observe("tool.start", "web_search", long_line)
    text = w.path.read_text(encoding="utf-8")
    # The tool budget (220) must truncate with the overflow marker.
    assert "(+" in text and "chars)" in text
    assert len([ln for ln in text.splitlines() if "web_search" in ln]) == 1


def test_wrap_progress_callback_tee_preserves_inner(live_home):
    w = ll.create_live_transcript("sa-5", "goal")
    seen = []

    def inner(event_type, tool_name=None, preview=None, args=None, **kw):
        seen.append(event_type)

    wrapped = ll.wrap_progress_callback(inner, w)
    wrapped("tool.start", "terminal", "x")
    assert seen == ["tool.start"]  # inner still called
    assert "terminal" in w.path.read_text(encoding="utf-8")


def test_wrap_progress_callback_none_writer_returns_inner():
    def inner(event_type, **kw):
        return "inner"

    assert ll.wrap_progress_callback(inner, None) is inner


def test_prune_stale_dirs(live_home, monkeypatch):
    w_old = ll.create_live_transcript("sa-old", "goal")
    import os
    import time as _t

    old_ts = _t.time() - 8 * 86400
    os.utime(w_old.dir, (old_ts, old_ts))
    ll.create_live_transcript("sa-new", "goal")
    removed = ll.prune_stale_live_dirs(days=7)
    assert removed >= 1
    assert not w_old.dir.exists()
    assert ll.live_transcript_root().joinpath("sa-new").exists()


def test_manifest_entry_shape(live_home):
    w = ll.create_live_transcript("sa-6", "goal")
    entry = w.manifest_entry()
    assert entry["subagent_id"] == "sa-6"
    assert entry["log"] == str(w.path)
    assert entry["disabled"] is False


def test_writer_failure_disables_silently(live_home, monkeypatch):
    w = ll.create_live_transcript("sa-7", "goal")
    # Simulate the log directory being removed mid-run (mode=ro parent, or
    # manual cleanup): append fails -> the writer disables itself.
    import shutil

    shutil.rmtree(w.dir)
    w.observe("tool.start", "terminal", "x")  # must not raise
    assert w._disabled is True
