"""Tests for /learn command helpers (HP-204)."""

import json
from unittest.mock import patch

from agent.learn_prompt import build_learn_messages, extract_skill_name_from_args
from intellect_cli.learn_cmd import LEARN_PRIVACY_NOTICE, run_learn_generate, run_learn_save


class TestLearnPrompt:
    def test_build_messages_includes_skill_name(self):
        msgs = build_learn_messages(
            skill_name="my-skill",
            conversation_excerpt=[{"role": "user", "content": "help me fix tests"}],
        )
        assert msgs[0]["role"] == "system"
        assert "my-skill" in msgs[1]["content"]

    def test_extract_skill_name(self):
        assert extract_skill_name_from_args("my-skill") == "my-skill"
        assert extract_skill_name_from_args("save") is None

    def test_build_messages_redacts_when_configured(self, monkeypatch):
        monkeypatch.setattr(
            "agent.learn_prompt._learn_redact_enabled",
            lambda: True,
        )
        msgs = build_learn_messages(
            skill_name="my-skill",
            conversation_excerpt=[
                {"role": "user", "content": "key sk-test123456789012345678901234"},
            ],
        )
        assert "sk-test123456789012345678901234" not in msgs[1]["content"]
        assert "user:" in msgs[1]["content"]


class TestLearnCommandFlow:
    def test_generate_includes_privacy_notice(self):
        draft = "---\nname: my-skill\ndescription: A short skill.\n---\n# Skill\n"
        with patch("agent.auxiliary_client.call_llm", return_value=draft):
            status, pending = run_learn_generate(
                args="my-skill",
                messages=[{"role": "user", "content": "hello"}],
            )
        assert pending == draft.strip()
        assert LEARN_PRIVACY_NOTICE in status

    def test_save_persists_via_skill_manage(self, tmp_path, monkeypatch):
        draft = (
            "---\n"
            "name: learned-skill\n"
            "description: A learned helper skill.\n"
            "author: User\n"
            "---\n"
            "# Learned Skill\n"
        )
        monkeypatch.setenv("INTELLECT_HOME", str(tmp_path / ".intellect"))
        from intellect_constants import get_intellect_home

        get_intellect_home().mkdir(parents=True, exist_ok=True)

        with patch("tools.skill_manager_tool.skill_manage") as mock_manage:
            mock_manage.return_value = json.dumps({
                "success": True,
                "name": "learned-skill",
            })
            result = run_learn_save(draft)
        assert "Skill saved" in result
        mock_manage.assert_called_once()
        assert mock_manage.call_args.kwargs["name"] == "learned-skill"
