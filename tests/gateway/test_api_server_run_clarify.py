"""``/v1/runs`` 的 subagent_progress 与 clarify 交付契约测试（issue #125）。

两项都是「对外通道补齐」：
- ``subagent_progress``：委派子代理运行中的进度摘要必须转发为 run 事件，
  否则外部客户端只见一次长时间静默的 delegate_task 调用。
- ``clarify``：api_server 过去没有交付路径，agent 调用 clarify 时用户收不到
  提问。现在以 ``clarify.request`` 事件下发，``POST /v1/runs/{run_id}/clarify``
  回填后解除 agent 线程阻塞。
"""

import asyncio
import json
from unittest.mock import MagicMock, patch

from gateway.config import PlatformConfig
from gateway.platforms.api_server import APIServerAdapter
from toolsets import resolve_toolset


class _RunRequest(dict):
    """提供 runs 处理器所需的最小 aiohttp Request 行为。"""

    def __init__(self, body, headers=None, match_info=None):
        super().__init__()
        self._body = body
        self.headers = headers or {}
        self.method = "POST"
        self.path_qs = "/v1/runs"
        self.remote = "127.0.0.1"
        self.transport = None
        self.match_info = match_info or {}

    async def json(self):
        return self._body


# ---------------------------------------------------------------------------
# subagent_progress 转发
# ---------------------------------------------------------------------------


async def _drain_queue(adapter, run_id):
    """取出该 run 队列里已入队的事件。

    ``_push`` 走 ``loop.call_soon_threadsafe``，回调要等事件循环让出一次才会
    执行，所以先 yield 一个 tick 再取。
    """
    await asyncio.sleep(0)
    q = adapter._run_streams.get(run_id)
    if q is None:
        return []
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    return events


def test_run_event_callback_forwards_subagent_progress():
    """delegate_tool 把摘要放在 name 位置参，转发必须取它而非 preview。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        run_id = "run_sub"
        adapter._run_streams[run_id] = asyncio.Queue()
        adapter._set_run_status(run_id, "running")

        cb = adapter._make_run_event_callback(run_id, asyncio.get_running_loop())
        # 生产端实际形状：parent_cb("subagent_progress", summary)
        cb("subagent_progress", "├─ 🔀 child searched docs")

        events = await _drain_queue(adapter, run_id)
        assert len(events) == 1
        ev = events[0]
        assert ev["event"] == "subagent_progress"
        assert ev["run_id"] == run_id
        assert ev["text"] == "├─ 🔀 child searched docs"

    asyncio.run(_run())


def test_run_event_callback_subagent_progress_falls_back_to_preview():
    """name 为空时退回 preview，避免丢掉摘要。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        run_id = "run_sub2"
        adapter._run_streams[run_id] = asyncio.Queue()
        adapter._set_run_status(run_id, "running")

        cb = adapter._make_run_event_callback(run_id, asyncio.get_running_loop())
        cb("subagent_progress", None, "summary in preview")

        events = await _drain_queue(adapter, run_id)
        assert events[0]["text"] == "summary in preview"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# clarify 工具集可用性
# ---------------------------------------------------------------------------


def test_api_server_toolset_includes_clarify():
    """api_server 默认工具集必须暴露 clarify，否则接口无从被调用。"""
    assert "clarify" in resolve_toolset("intellect-api-server")


# ---------------------------------------------------------------------------
# clarify 回调 + resolve 端点
# ---------------------------------------------------------------------------


def test_runs_supplies_clarify_callback_to_agent():
    """_handle_runs 必须把 clarify 回调注入 agent，否则工具直接报"不可用"。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        agent = MagicMock()
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0
        agent.run_conversation.return_value = {"final_response": "ok"}
        create_agent = MagicMock(return_value=agent)

        with patch.object(adapter, "_create_agent", create_agent):
            response = await adapter._handle_runs(
                _RunRequest({"input": "x", "session_id": "s1"})
            )
            payload = json.loads(response.text)
            task = adapter._active_run_tasks.get(payload["run_id"])
            if task is not None:
                await task

        assert response.status == 202
        assert callable(create_agent.call_args.kwargs["clarify_callback"])

    asyncio.run(_run())


def test_clarify_callback_emits_event_and_unblocks_on_resolve():
    """回调发 clarify.request 事件；resolve 端点回填后回调返回该答案。

    直接驱动 ``_handle_runs`` 注入的那个回调（它跑在 agent 工作线程上），
    绕开 executor，把「事件形状 + 阻塞/解除」这条契约测清楚。
    """

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        run_id = "run_clar"
        adapter._run_streams[run_id] = asyncio.Queue()
        adapter._set_run_status(run_id, "running")

        captured = {}
        agent = MagicMock()
        agent.session_prompt_tokens = 0
        agent.session_completion_tokens = 0
        agent.session_total_tokens = 0

        def _run_conversation(*, user_message, conversation_history, task_id):
            # agent 线程：调用注入的 clarify 回调（模拟 clarify 工具内部行为）。
            captured["answer"] = captured["clarify_callback"](
                "Which approach?", ["A", "B"]
            )
            return {"final_response": "done"}

        agent.run_conversation.side_effect = _run_conversation

        def _capture_create_agent(**kwargs):
            captured["clarify_callback"] = kwargs["clarify_callback"]
            return agent

        with patch.object(adapter, "_create_agent", side_effect=_capture_create_agent):
            response = await adapter._handle_runs(
                _RunRequest({"input": "x", "session_id": "s-c"})
            )
            payload = json.loads(response.text)
            assert response.status == 202
            real_run_id = payload["run_id"]

            # 回调在 executor 线程上运行；等它注册 pending 并阻塞。
            for _ in range(200):
                if adapter._run_clarify_pending.get(real_run_id):
                    break
                await asyncio.sleep(0.02)
            clarify_id = adapter._run_clarify_pending.get(real_run_id)
            assert clarify_id, "clarify 回调未注册 pending 请求"

            # call_soon_threadsafe 排队的 clarify.request 需要让出一tick。
            await asyncio.sleep(0)
            q = adapter._run_streams[real_run_id]
            events = []
            while not q.empty():
                events.append(q.get_nowait())
            req = next(e for e in events if e["event"] == "clarify.request")
            assert req["question"] == "Which approach?"
            assert req["choices"] == ["A", "B"]
            assert req["clarify_id"] == clarify_id
            assert (
                adapter._run_status_store.get(real_run_id)["status"]
                == "waiting_for_clarify"
            )

            # 客户端回填答案 → 解除阻塞。
            resolve_resp = await adapter._handle_run_clarify(
                _RunRequest({"response": "B"}, match_info={"run_id": real_run_id})
            )
            assert resolve_resp.status == 200
            resolved_payload = json.loads(resolve_resp.text)
            assert resolved_payload["clarify_id"] == clarify_id

            # 等 agent 线程结束。
            task = adapter._active_run_tasks.get(real_run_id)
            if task is not None:
                await asyncio.wait_for(task, timeout=10)

        assert captured["answer"] == "B"

    # clarify 默认超时 600s；测试里压短，避免等待窗口过长。
    with patch("tools.clarify_gateway.get_clarify_timeout", return_value=5):
        asyncio.run(_run())


def test_run_clarify_unknown_run_returns_404():
    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        resp = await adapter._handle_run_clarify(
            _RunRequest({"response": "x"}, match_info={"run_id": "nope"})
        )
        assert resp.status == 404
        assert json.loads(resp.text)["error"]["code"] == "run_not_found"

    asyncio.run(_run())


def test_run_clarify_missing_response_returns_400():
    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        run_id = "run_missing"
        adapter._set_run_status(run_id, "running")

        resp = await adapter._handle_run_clarify(
            _RunRequest({}, match_info={"run_id": run_id})
        )
        assert resp.status == 400
        assert json.loads(resp.text)["error"]["code"] == "invalid_clarify_response"

    asyncio.run(_run())


def test_run_clarify_no_pending_returns_409():
    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        run_id = "run_none_pending"
        adapter._set_run_status(run_id, "running")

        resp = await adapter._handle_run_clarify(
            _RunRequest({"response": "x"}, match_info={"run_id": run_id})
        )
        assert resp.status == 409
        assert json.loads(resp.text)["error"]["code"] == "clarify_not_pending"

    asyncio.run(_run())


def test_run_clarify_id_mismatch_returns_409():
    """客户端指的 clarify_id 与当前 pending 不一致时拒绝，避免答错问题。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        run_id = "run_mismatch"
        adapter._set_run_status(run_id, "running")
        adapter._run_clarify_pending[run_id] = "current-id"

        resp = await adapter._handle_run_clarify(
            _RunRequest(
                {"response": "x", "clarify_id": "stale-id"},
                match_info={"run_id": run_id},
            )
        )
        assert resp.status == 409
        assert json.loads(resp.text)["error"]["code"] == "clarify_id_mismatch"

    asyncio.run(_run())


def test_capabilities_advertises_run_clarify():
    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        resp = await adapter._handle_capabilities(
            _RunRequest({}, headers={"Authorization": "Bearer x"})
        )
        payload = json.loads(resp.text)
        # capabilities 结构可能包一层，逐层找 runs 端点表。
        text = json.dumps(payload)
        assert "/v1/runs/{run_id}/clarify" in text

    with patch.object(APIServerAdapter, "_check_auth", return_value=None):
        asyncio.run(_run())
