"""Tests for per-turn micro-compaction in ``ContextCompressor``.

Micro-compaction amortizes the cost of context compression: instead of one
long pause when the window fills, each completed turn folds the single oldest
un-absorbed exchange into a rolling summary.

The invariants that matter:

* one pass absorbs exactly one exchange (assistant + its tool results), so
  the per-turn cost stays bounded;
* the absorbed span is replaced by a summary marker carrying the usual
  ``_compressed_summary`` metadata, so resume/compress treat it like a batch
  summary;
* the cursor advances, so successive passes walk forward rather than
  re-summarising the same exchange;
* protected head and tail messages are never touched;
* user messages are never absorbed — their text stays verbatim for the whole
  session;
* an exchange the summarizer cannot handle is retried a bounded number of
  times and then skipped, so a poison exchange can't stall every turn.
"""

import json
import logging

import pytest

from agent.context_compressor import (
    COMPRESSED_SUMMARY_HAS_USER_TURN_KEY,
    COMPRESSED_SUMMARY_METADATA_KEY,
    ContextCompressor,
    _MICRO_COMPACT_MAX_CONSECUTIVE_FAILURES,
)


def _compressor(summary="ROLLING SUMMARY") -> ContextCompressor:
    cc = ContextCompressor(
        model="test-model",
        threshold_percent=0.75,
        protect_first_n=1,
        protect_last_n=2,
        quiet_mode=True,
        config_context_length=40960,
        provider="test",
    )
    cc._micro_compact_enabled = True
    # Stand in for the auxiliary summarizer LLM.
    cc._micro_summarize_one = lambda _text: summary
    return cc


def _conversation(exchanges: int = 6) -> list:
    msgs = [{"role": "system", "content": "system prompt"}]
    for i in range(exchanges):
        msgs.append({"role": "user", "content": f"question {i}"})
        msgs.append({"role": "assistant", "content": f"answer {i} " + "z" * 400})
    return msgs


def _summary_markers(messages: list) -> list:
    return [m for m in messages if m.get(COMPRESSED_SUMMARY_METADATA_KEY)]


class TestMicroCompaction:
    def test_absorbs_one_exchange_and_leaves_a_summary_marker(self):
        cc = _compressor()
        messages = _conversation()

        result = cc._micro_compact(list(messages))

        assert not any("answer 0" in str(m.get("content")) for m in result)
        markers = _summary_markers(result)
        assert len(markers) == 1
        assert "ROLLING SUMMARY" in markers[0]["content"]
        # assistant-role marker keeps user → marker → user alternation valid.
        assert markers[0]["role"] == "assistant"

    def test_disabled_is_a_no_op(self):
        cc = _compressor()
        cc._micro_compact_enabled = False
        messages = _conversation()

        assert cc._micro_compact(list(messages)) == messages

    def test_is_off_unless_explicitly_enabled(self):
        cc = ContextCompressor(
            model="test-model",
            threshold_percent=0.75,
            protect_first_n=1,
            protect_last_n=2,
            quiet_mode=True,
            config_context_length=40960,
            provider="test",
        )
        cc._micro_summarize_one = lambda _text: "ROLLING SUMMARY"
        messages = _conversation()

        assert cc._micro_compact_enabled is False
        assert cc._micro_compact(list(messages)) == messages

    def test_cadence_of_one_runs_every_turn(self):
        cc = _compressor()
        cc._micro_compact_every_n_turns = 1
        messages = _conversation(exchanges=8)

        first = cc._micro_compact(list(messages))
        second = cc._micro_compact(list(first))

        assert cc._micro_compact_cursor > 0
        assert len(_summary_markers(first)) == 1
        assert len(second) < len(first)

    def test_cadence_skips_turns_until_a_pass_is_due(self):
        cc = _compressor()
        cc._micro_compact_every_n_turns = 3
        messages = _conversation(exchanges=8)

        first = cc._micro_compact(list(messages))
        second = cc._micro_compact(list(first))

        assert _summary_markers(first) == []
        assert _summary_markers(second) == []
        assert cc._micro_compact_cursor == 0

        third = cc._micro_compact(list(second))

        assert len(_summary_markers(third)) == 1
        assert cc._micro_compact_turns_since_pass == 0

    def test_cadence_is_clamped_to_at_least_one(self):
        for bogus in (0, -5):
            cc = _compressor()
            cc._micro_compact_every_n_turns = bogus
            messages = _conversation(exchanges=8)

            result = cc._micro_compact(list(messages))

            assert len(_summary_markers(result)) == 1

    def test_cursor_advances_across_successive_turns(self):
        cc = _compressor()
        messages = _conversation(exchanges=8)

        first = cc._micro_compact(list(messages))
        cursor_after_first = cc._micro_compact_cursor
        second = cc._micro_compact(list(first))

        assert cursor_after_first > 0
        assert cc._micro_compact_cursor >= cursor_after_first
        assert len(_summary_markers(second)) == 1

    def test_protected_head_and_tail_survive(self):
        cc = _compressor()
        messages = _conversation()

        result = cc._micro_compact(list(messages))

        assert result[0] == messages[0], "system prompt must be preserved"
        assert result[-1] == messages[-1], "most recent turn must be preserved"

    def test_user_messages_are_never_absorbed(self):
        cc = _compressor()
        messages = _conversation(exchanges=10)
        originals = [m["content"] for m in messages if m["role"] == "user"]

        for _ in range(5):
            messages = cc._micro_compact(messages)

        surviving_text = "\n\n".join(
            m["content"] for m in messages
            if m.get("role") == "user" and not m.get(COMPRESSED_SUMMARY_METADATA_KEY)
        )
        for original in originals:
            assert original in surviving_text, (
                f"user text {original!r} must survive verbatim"
            )

    def test_cursor_is_derived_from_the_spliced_list(self):
        cc = _compressor()
        msgs = [{"role": "system", "content": "sys"}]
        for i in range(8):
            msgs.append({"role": "user", "content": f"q{i}"})
            msgs.append({
                "role": "assistant",
                "content": f"a{i}",
                "tool_calls": [
                    {"id": f"c{i}-{j}", "type": "function",
                     "function": {"name": "f", "arguments": "{}"}}
                    for j in range(3)
                ],
            })
            for j in range(3):
                msgs.append({"role": "tool", "tool_call_id": f"c{i}-{j}",
                             "content": "T" * 500})

        for _ in range(4):
            msgs = cc._micro_compact(msgs)
            marker_idx = next(
                i for i, m in enumerate(msgs)
                if m.get(COMPRESSED_SUMMARY_METADATA_KEY)
            )
            assert cc._micro_compact_cursor == marker_idx + 1

    def test_resume_does_not_destroy_the_accumulated_summary(self):
        msgs = _conversation(exchanges=10)
        first = _compressor(summary="IMPORTANT HISTORY: decisions and paths")
        for _ in range(3):
            msgs = first._micro_compact(msgs)
        assert any("IMPORTANT HISTORY" in m["content"] for m in _summary_markers(msgs))

        resumed = _compressor(summary="MERGED: history plus newest exchange")
        assert resumed._micro_compact_rolling_summary == ""
        result = resumed._micro_compact(msgs)

        markers = _summary_markers(result)
        assert len(markers) == 1
        assert "MERGED" in markers[0]["content"]

    def test_short_conversation_is_untouched(self):
        cc = _compressor()
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]

        assert cc._micro_compact(list(messages)) == messages

    def test_summarizer_failure_leaves_conversation_intact(self):
        cc = _compressor()
        cc._micro_summarize_one = lambda _text: None
        messages = _conversation()

        result = cc._micro_compact(list(messages))

        assert result == messages
        assert cc._micro_compact_consecutive_failures == 1

    def test_poison_exchange_is_skipped_after_repeated_failures(self):
        cc = _compressor()
        cc._micro_summarize_one = lambda _text: None
        messages = _conversation()

        for _ in range(_MICRO_COMPACT_MAX_CONSECUTIVE_FAILURES):
            cc._micro_compact(list(messages))

        assert cc._micro_compact_cursor > 0
        assert cc._micro_compact_consecutive_failures == 0

    def test_repeated_compaction_shrinks_context_and_keeps_one_marker(self):
        from agent.model_metadata import estimate_messages_tokens_rough

        cc = _compressor()
        state = {"n": 0}

        def growing(_text):
            state["n"] += 1
            return "SUMMARY " + " ".join(f"ex{i}" for i in range(state["n"]))

        cc._micro_summarize_one = growing

        messages = _conversation(exchanges=12)
        before = estimate_messages_tokens_rough(messages)
        for _ in range(6):
            messages = cc._micro_compact(messages)
        after = estimate_messages_tokens_rough(messages)

        assert len(_summary_markers(messages)) == 1
        assert after < before, f"context grew: {before} -> {after}"

    def test_emits_content_free_token_telemetry(self, caplog):
        cc = _compressor()
        messages = _conversation(exchanges=8)

        with caplog.at_level(logging.INFO, logger="agent.context_compressor"):
            result = cc._micro_compact(messages)

        lines = [
            r.getMessage() for r in caplog.records
            if "micro compaction telemetry:" in r.getMessage()
        ]
        assert len(lines) == 1
        payload = json.loads(lines[0].split("micro compaction telemetry: ", 1)[1])

        assert payload["event"] == "micro_compaction"
        assert payload["outcome"] == "absorbed"
        assert payload["tokens_saved_total"] == -payload["tokens_delta"]
        assert payload["passes_total"] == 1
        assert payload["messages_after"] == len(result)
        blob = json.dumps(payload)
        assert "answer 0" not in blob and "question 0" not in blob

    def test_telemetry_reports_occupancy_without_forcing_resolution(self, caplog):
        cc = _compressor()
        cc.threshold_tokens = 10_000
        messages = _conversation(exchanges=8)

        with caplog.at_level(logging.INFO, logger="agent.context_compressor"):
            cc._micro_compact(messages)

        line = next(r.getMessage() for r in caplog.records
                    if "micro compaction telemetry:" in r.getMessage())
        payload = json.loads(line.split("micro compaction telemetry: ", 1)[1])

        assert payload["threshold_tokens"] == 10_000
        assert payload["occupancy_pct"] == pytest.approx(
            payload["tokens_after"] / 10_000 * 100, abs=0.1
        )

    def test_marker_reports_no_user_provenance(self):
        cc = _compressor()
        result = cc._micro_compact(_conversation(exchanges=6))
        marker = _summary_markers(result)[0]

        assert marker[COMPRESSED_SUMMARY_HAS_USER_TURN_KEY] is False

    def test_supersede_never_drops_a_batch_compaction_marker(self):
        cc = _compressor(summary="MICRO SUMMARY (exchanges 1..k only)")
        msgs = _conversation(exchanges=8)
        msgs = cc._micro_compact(msgs)
        assert cc._micro_compact_rolling_summary

        # Simulate a batch-compaction marker replacing the middle (batch
        # markers carry the shared metadata key but NOT the micro tag).
        batch_marker = {
            "role": "user",
            "content": "[batch summary] CRITICAL HISTORY: exchanges 1..m",
            COMPRESSED_SUMMARY_METADATA_KEY: True,
        }
        micro_idx = next(
            i for i, m in enumerate(msgs)
            if m.get(COMPRESSED_SUMMARY_METADATA_KEY)
        )
        msgs = msgs[:micro_idx] + [batch_marker] + msgs[micro_idx + 3:]

        out = cc._micro_compact(msgs)

        assert any(
            "CRITICAL HISTORY" in str(m.get("content")) for m in out
        ), "batch-compaction summary destroyed by micro supersede"

    def test_defrag_triggers_once_the_rolling_summary_grows(self):
        cc = _compressor(summary="FRESH DEFRAGGED SUMMARY")
        messages = _conversation(exchanges=8)
        messages = cc._micro_compact(list(messages))
        cc._micro_compact_rolling_summary = "x" * 40_000
        cursor_before = cc._micro_compact_cursor
        shape_before = [m.get("role") for m in messages]

        assert cc._needs_defrag() is True
        result = cc._micro_compact(list(messages))

        assert cc._micro_compact_rolling_summary == "FRESH DEFRAGGED SUMMARY"
        markers = _summary_markers(result)
        assert len(markers) == 1
        assert "FRESH DEFRAGGED SUMMARY" in markers[0]["content"]
        assert [m.get("role") for m in result] == shape_before
        assert cc._micro_compact_cursor == cursor_before

    def test_defrag_never_absorbs_user_messages(self):
        cc = _compressor(summary="DEFRAGGED")
        messages = [{"role": "system", "content": "sys"}]
        for i in range(10):
            messages.append({"role": "user", "content": f"UNIQUE-USER-PROMPT-{i}"})
            messages.append({"role": "assistant", "content": f"answer {i} " + "z" * 400})

        cc._micro_compact_rolling_summary = "x" * 40_000
        result = cc._micro_compact(list(messages))

        surviving = [
            m["content"] for m in result
            if m.get("role") == "user" and not m.get(COMPRESSED_SUMMARY_METADATA_KEY)
        ]
        for i in range(10):
            assert any(f"UNIQUE-USER-PROMPT-{i}" in s for s in surviving), (
                f"user prompt {i} was absorbed by defrag"
            )

    def test_defrag_summarizes_only_the_summary_text(self):
        cc = _compressor()
        captured = {}

        def capture(text):
            captured["text"] = text
            return "DEFRAGGED"

        cc._micro_summarize_one = capture
        cc._micro_compact_rolling_summary = "OLD-SUMMARY " + "x" * 40_000
        messages = _conversation(exchanges=8)
        cc._micro_compact(list(messages))

        assert "OLD-SUMMARY" in captured["text"]
        assert "[USER]" not in captured["text"], (
            "defrag must never serialize transcript user turns"
        )

    def test_spliced_transcript_survives_repair_message_sequence(self):
        from agent.agent_runtime_helpers import repair_message_sequence

        class _DummyAgent:
            session_id = "probe"
            _last_flushed_db_idx = 0

        cc = _compressor()
        messages = _conversation(exchanges=8)

        for _ in range(3):
            messages = cc._micro_compact(messages)
            repairs = repair_message_sequence(_DummyAgent(), messages)
            assert repairs == 0, (
                "micro-compacted transcript must already be alternation-valid"
            )
            markers = _summary_markers(messages)
            assert len(markers) == 1, "marker destroyed by repair pass"
            polluted = [
                m for m in messages
                if m.get("role") == "user"
                and not m.get(COMPRESSED_SUMMARY_METADATA_KEY)
                and "ROLLING SUMMARY" in str(m.get("content"))
            ]
            assert not polluted, "summary text leaked into a real user message"

    def test_batch_compress_resets_micro_state(self):
        cc = _compressor()
        msgs = _conversation(exchanges=8)
        msgs = cc._micro_compact(msgs)
        assert cc._micro_compact_rolling_summary
        assert cc._micro_compact_cursor > 0

        cc.compress(msgs, force=True)

        assert cc._micro_compact_rolling_summary == ""
        assert cc._micro_compact_cursor == 0

    def test_on_session_reset_clears_micro_state(self):
        """/new must not carry the prior session's rolling summary/cursor over."""
        cc = _compressor()
        msgs = _conversation(exchanges=8)
        msgs = cc._micro_compact(msgs)
        cc._micro_compact_consecutive_failures = 2
        cc._micro_compact_last_failure_cursor = 5
        cc._micro_compact_passes = 3
        cc._micro_compact_tokens_saved_total = 999
        assert cc._micro_compact_rolling_summary
        assert cc._micro_compact_cursor > 0

        cc.on_session_reset()

        assert cc._micro_compact_rolling_summary == ""
        assert cc._micro_compact_cursor == 0
        assert cc._micro_compact_consecutive_failures == 0
        assert cc._micro_compact_last_failure_cursor == -1
        assert cc._micro_compact_passes == 0
        assert cc._micro_compact_tokens_saved_total == 0
