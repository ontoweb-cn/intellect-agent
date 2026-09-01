"""Tests for proactive prune (G-06 / A2-1): trigger, reclaim gate, rearm."""

import json


from agent.context_compressor import ContextCompressor


def make_compressor(**overrides) -> ContextCompressor:
    kwargs = dict(
        model="test-model",
        threshold_percent=0.90,  # keep should_compress away from the prune test
        protect_last_n=4,
        proactive_prune_tokens=1000,
        proactive_prune_min_reclaim_tokens=100,
        proactive_prune_min_result_chars=200,
    )
    kwargs.update(overrides)
    return ContextCompressor(**kwargs)


def _messages(n_tool_results=30, result_chars=2000):
    msgs = [{"role": "system", "content": "sys prompt " * 20}]
    for i in range(n_tool_results):
        msgs.append({"role": "user", "content": f"question {i}"})
        msgs.append({
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": f"call_{i}", "type": "function",
                "function": {"name": "terminal",
                             "arguments": json.dumps({"cmd": f"run {i}"})},
            }],
        })
        msgs.append({"role": "tool", "tool_call_id": f"call_{i}",
                     "content": f"result {i}: " + "r" * result_chars})
    msgs.append({"role": "user", "content": "final question"})
    return msgs


def test_default_trigger_zero_is_off():
    c = ContextCompressor(model="m")
    msgs = _messages()
    out, reclaimed = c.prune_tool_results_only(msgs, 999_999)
    assert out is msgs and reclaimed == 0  # off = strict no-op


def test_below_trigger_is_noop():
    c = make_compressor()
    msgs = _messages()
    out, reclaimed = c.prune_tool_results_only(msgs, 500)  # trigger=1000
    assert out is msgs and reclaimed == 0


def test_reclaim_gate_commit_and_shrink():
    c = make_compressor()
    msgs = _messages()
    out, reclaimed = c.prune_tool_results_only(msgs, 50_000)
    assert out is not msgs  # committed
    assert reclaimed >= c._proactive_prune_min_reclaim_tokens
    # Prune replaces content in place (summaries keep the 1:1 message
    # skeleton; dedup may drop repeats) — the reclaim token count is the
    # contract, not the message count.


def test_rearm_blocks_immediate_second_prune():
    c = make_compressor()
    msgs = _messages()
    out1, r1 = c.prune_tool_results_only(msgs, 50_000)
    assert r1 > 0
    # Second call at the same token level: below the rearm watermark → no-op.
    out2, r2 = c.prune_tool_results_only(out1, 50_000)
    assert out2 is out1 and r2 == 0


def test_rearm_clears_after_growth():
    c = make_compressor()
    msgs = _messages()
    out1, r1 = c.prune_tool_results_only(msgs, 50_000)
    assert r1 > 0
    # Simulate enough growth to pass the rearm watermark.
    c._proactive_prune_rearm_tokens = 10
    grown = out1 + [
        {"role": "user", "content": "more " * 500},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "go on"},
    ]
    out2, r2 = c.prune_tool_results_only(grown, 999_999)
    # May or may not commit again (reclaim gate), but the rearm no longer
    # blocks the attempt: with fresh prune-able content it must commit.
    assert r2 > 0


def test_tiny_history_noop():
    c = make_compressor()
    msgs = [{"role": "user", "content": "hi"}]
    out, reclaimed = c.prune_tool_results_only(msgs, 50_000)
    assert out is msgs and reclaimed == 0


def test_rearm_persistence_roundtrip(tmp_path):
    """Persisted watermark survives into a fresh compressor via the session
    model_config round trip (update + load)."""
    from intellect_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("sess-1", source="cli")

    c1 = make_compressor()
    c1._persist_prune_rearm(db, "sess-1", 12_345)

    c2 = make_compressor()
    assert c2._proactive_prune_rearm_tokens == 0
    c2.load_proactive_prune_rearm(db, "sess-1")
    assert c2._proactive_prune_rearm_tokens == 12_345


def test_persisted_rearm_blocks_prune_after_reload(tmp_path):
    """End-to-end: prune commits -> watermark persisted -> fresh compressor
    (simulating a restart) loads it -> prune at the same level is a no-op."""
    from intellect_state import SessionDB

    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("sess-r", source="cli")

    c1 = make_compressor()
    msgs = _messages()
    out1, r1 = c1.prune_tool_results_only(
        msgs, 50_000, session_id="sess-r", session_db=db
    )
    assert r1 > 0

    c2 = make_compressor()
    c2.load_proactive_prune_rearm(db, "sess-r")
    out2, r2 = c2.prune_tool_results_only(out1, 50_000)
    assert out2 is out1 and r2 == 0  # watermark survived the "restart"
