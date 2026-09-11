"""``POST /v1/runs`` 逐回合 model 覆盖契约测试。

覆盖 issue #126 的两半：
1. 请求体里的 ``model`` 必须真正作用于本次 run 的 agent（此前被静默忽略）；
2. run 状态与 202 响应必须回显**实际使用**的模型（此前回显 body 里的值，
   与 agent 实际使用的 config 默认值可能不一致）。
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter


class _RunRequest(dict):
    """提供 runs 处理器所需的最小 aiohttp Request 行为。"""

    def __init__(self, body, headers=None):
        super().__init__()
        self._body = body
        self.headers = headers or {}
        self.method = "POST"
        self.path_qs = "/v1/runs"
        self.remote = "127.0.0.1"
        self.transport = None

    async def json(self):
        return self._body


def _make_agent(captured):
    """创建不会访问模型服务的代理，并记录送入代理的消息。"""
    agent = MagicMock()
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0

    def _run_conversation(*, user_message, conversation_history, task_id):
        captured.update(user_message=user_message, task_id=task_id)
        return {"final_response": "ok"}

    agent.run_conversation.side_effect = _run_conversation
    return agent


async def _wait_for_run(adapter, response_payload):
    run_id = response_payload.get("run_id")
    task = adapter._active_run_tasks.get(run_id)
    if task is not None:
        await task


def test_runs_model_override_reaches_agent_and_is_echoed():
    """请求体带 model 时，agent 与回显都必须用这个模型。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        captured = {}
        agent = _make_agent(captured)
        create_agent = MagicMock(return_value=agent)

        # config 默认模型故意设成与请求体不同，确保断言的是覆盖值而非默认值。
        with patch.object(adapter, "_create_agent", create_agent), patch(
            "gateway.run._resolve_gateway_model", return_value="config-default-model"
        ):
            response = await adapter._handle_runs(
                _RunRequest(
                    {"input": "换个模型", "model": "deepseek-chat", "session_id": "s1"}
                )
            )
            payload = json.loads(response.text)
            await _wait_for_run(adapter, payload)

        assert response.status == 202
        # 1) agent 真正拿到了覆盖模型
        assert create_agent.call_args.kwargs["model"] == "deepseek-chat"
        # 2) 202 载荷回显实际模型
        assert payload["model"] == "deepseek-chat"
        # 3) 可轮询的 run 状态与 202 一致
        assert adapter._run_status_store.get(payload["run_id"])["model"] == "deepseek-chat"

    asyncio.run(_run())


def test_runs_without_model_uses_resolved_default_unchanged():
    """不传 model 时行为不变：用 config 解析出的默认模型，并如实回显。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        captured = {}
        agent = _make_agent(captured)
        create_agent = MagicMock(return_value=agent)

        with patch.object(adapter, "_create_agent", create_agent), patch(
            "gateway.run._resolve_gateway_model", return_value="config-default-model"
        ):
            response = await adapter._handle_runs(
                _RunRequest({"input": "普通对话", "session_id": "s2"})
            )
            payload = json.loads(response.text)
            await _wait_for_run(adapter, payload)

        assert response.status == 202
        assert create_agent.call_args.kwargs["model"] == "config-default-model"
        assert payload["model"] == "config-default-model"
        assert (
            adapter._run_status_store.get(payload["run_id"])["model"]
            == "config-default-model"
        )

    asyncio.run(_run())


def test_runs_model_is_stripped_before_use():
    """两侧空白必须去掉，避免把带空格的模型名送给上游。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        agent = _make_agent({})
        create_agent = MagicMock(return_value=agent)

        with patch.object(adapter, "_create_agent", create_agent), patch(
            "gateway.run._resolve_gateway_model", return_value="config-default-model"
        ):
            response = await adapter._handle_runs(
                _RunRequest({"input": "x", "model": "  gpt-4o  ", "session_id": "s3"})
            )
            payload = json.loads(response.text)
            await _wait_for_run(adapter, payload)

        assert response.status == 202
        assert create_agent.call_args.kwargs["model"] == "gpt-4o"
        assert payload["model"] == "gpt-4o"

    asyncio.run(_run())


def test_runs_rejects_empty_model_before_creating_run():
    """空串 model 是参数错误，且不得分配后台运行资源。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        response = await adapter._handle_runs(_RunRequest({"input": "x", "model": "   "}))
        payload = json.loads(response.text)

        assert response.status == 400
        assert payload["error"]["param"] == "model"
        assert payload["error"]["code"] == "invalid_model"
        assert adapter._run_streams == {}

    asyncio.run(_run())


def test_runs_rejects_non_string_model_before_creating_run():
    """非字符串 model 是参数错误，且不得分配后台运行资源。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        response = await adapter._handle_runs(_RunRequest({"input": "x", "model": 123}))
        payload = json.loads(response.text)

        assert response.status == 400
        assert payload["error"]["param"] == "model"
        assert payload["error"]["code"] == "invalid_model"
        assert adapter._run_streams == {}

    asyncio.run(_run())
