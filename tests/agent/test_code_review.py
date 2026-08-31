"""Tests for the shared code-review subagent runner (agent/code_review.py)."""

from agent.code_review import (
    _pin_parent_context,
    build_review_prompt,
    run_code_review,
)


class _FakeAgent:
    def __init__(self, session_id="review_task"):
        self.session_id = session_id
        self.closed = False
        self._cached_system_prompt = None

    def run_conversation(self, user_message):
        assert "code review" in user_message.lower() or "git" in user_message
        return {"final_response": "REVIEW TEXT"}

    def close(self):
        self.closed = True


class TestBuildReviewPrompt:
    def test_base_prompt_mentions_code_review(self):
        assert "code review" in build_review_prompt()

    def test_topic_is_appended(self):
        assert "Focus area requested by the user: auth flow" in build_review_prompt("auth flow")


class TestRunCodeReview:
    def test_returns_final_response_and_closes_agent(self):
        agent = _FakeAgent()
        out = run_code_review(agent, topic="x")
        assert out == "REVIEW TEXT"
        assert agent.closed is True

    def test_returns_error_string_when_run_fails(self):
        class _ErrAgent(_FakeAgent):
            def run_conversation(self, user_message):
                return {"final_response": "", "error": "boom"}

        agent = _ErrAgent()
        out = run_code_review(agent)
        assert out == "Error: boom"
        assert agent.closed is True

    def test_closes_agent_even_when_run_conversation_raises(self):
        class _RaiseAgent(_FakeAgent):
            def run_conversation(self, user_message):
                raise RuntimeError("kaboom")

        agent = _RaiseAgent()
        try:
            run_code_review(agent)
        except RuntimeError:
            pass
        assert agent.closed is True


class TestPinParentContext:
    def test_pins_cached_system_prompt(self):
        agent = _FakeAgent()
        parent = _FakeAgent(session_id="parent_session")
        parent._cached_system_prompt = "SYSTEM PROMPT WITH SKILLS"

        _pin_parent_context(agent, parent)

        assert agent._cached_system_prompt == "SYSTEM PROMPT WITH SKILLS"

    def test_does_not_copy_parent_session_id(self):
        # The review subagent shares the parent's session_db but must keep its
        # own unique session id — otherwise its transcript is flushed into the
        # parent's session row.
        agent = _FakeAgent(session_id="review_task")
        parent = _FakeAgent(session_id="parent_session")

        _pin_parent_context(agent, parent)

        assert agent.session_id == "review_task"

    def test_missing_parent_attrs_do_not_crash(self):
        agent = _FakeAgent()
        _pin_parent_context(agent, object())  # no _cached_system_prompt
        assert agent._cached_system_prompt is None
