"""验证 ``POST /v1/runs`` 的显式 Skill 调用契约。"""

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
        # Skill 查询接口与 runs 接口共用 Bearer Token 认证。允许测试显式
        # 传入请求头，既能覆盖成功路径，也能确认新接口没有绕过现有认证。
        self.headers = headers or {}
        # 无效凭据会进入现有安全审计日志，因此测试替身需要提供审计函数
        # 读取的最小 aiohttp Request 属性，避免用 mock 绕开真实认证流程。
        self.method = "GET"
        self.path_qs = "/v1/skills/custom"
        self.remote = "127.0.0.1"
        self.transport = None

    async def json(self):
        """返回测试请求体，避免启动真实 HTTP 监听端口。"""
        return self._body


def _make_agent(captured):
    """创建不会访问模型服务的代理，并记录最终送入代理的消息。"""
    agent = MagicMock()
    agent.session_prompt_tokens = 0
    agent.session_completion_tokens = 0
    agent.session_total_tokens = 0

    def _run_conversation(*, user_message, conversation_history, task_id):
        captured.update(
            user_message=user_message,
            conversation_history=conversation_history,
            task_id=task_id,
        )
        return {"final_response": "ok"}

    agent.run_conversation.side_effect = _run_conversation
    return agent


async def _wait_for_run(adapter, response_payload):
    """等待后台 run 收尾，确保测试不会遗留事件循环任务。"""
    run_id = response_payload.get("run_id")
    task = adapter._active_run_tasks.get(run_id)
    if task is not None:
        await task


def test_runs_explicit_skill_loads_skill_and_reports_canonical_name():
    """合法 skill 必须被确定性加载，并在提交结果和运行状态中回传。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        captured = {}
        agent = _make_agent(captured)

        def _resolve_skill(_requested_name):
            # API Server 的 platform_disabled 配置依赖当前会话平台；这里直接
            # 记录解析时上下文，防止接口误用全局 Skill 列表。
            from gateway.session_context import get_session_env

            captured["resolver_platform"] = get_session_env(
                "intellect_SESSION_PLATFORM"
            )
            return "/pdf"

        # 只隔离模型调用和 Skill 文件读取，runs 的请求解析、状态保存及后台
        # 执行流程均使用真实实现，以覆盖外部平台实际依赖的接口边界。
        with patch.object(adapter, "_create_agent", return_value=agent), patch(
            "agent.skill_commands.resolve_skill_command_key",
            side_effect=_resolve_skill,
        ), patch(
            "agent.skill_commands.get_skill_commands",
            return_value={"/pdf": {"name": "pdf"}},
        ), patch(
            "agent.skill_commands.build_skill_invocation_message",
            return_value="已加载的 PDF Skill\n\n用户要求：提取附件目录",
        ):
            response = await adapter._handle_runs(
                _RunRequest(
                    {
                        "input": "提取附件目录",
                        "skill": "PDF",
                        "session_id": "platform-session-1",
                    }
                )
            )
            payload = json.loads(response.text)
            await _wait_for_run(adapter, payload)

        assert response.status == 202
        assert payload["skill"] == "pdf"
        assert captured["user_message"] == "已加载的 PDF Skill\n\n用户要求：提取附件目录"
        assert captured["task_id"] == "platform-session-1"
        assert captured["resolver_platform"] == "api_server"
        assert adapter._run_status_store.get(payload["run_id"])["skill"] == "pdf"

    asyncio.run(_run())


def test_runs_without_skill_preserves_original_message():
    """不传 skill 时保持旧版 runs 行为，不能隐式触发 Skill 加载。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        captured = {}
        agent = _make_agent(captured)

        with patch.object(adapter, "_create_agent", return_value=agent), patch(
            "agent.skill_commands.build_skill_invocation_message",
        ) as build_message:
            response = await adapter._handle_runs(
                _RunRequest({"input": "普通对话", "session_id": "plain-session"})
            )
            payload = json.loads(response.text)
            await _wait_for_run(adapter, payload)

        assert response.status == 202
        assert "skill" not in payload
        assert captured["user_message"] == "普通对话"
        build_message.assert_not_called()

    asyncio.run(_run())


def test_runs_rejects_non_string_skill_before_creating_run():
    """skill 不是字符串时返回参数错误，且不能分配后台运行资源。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        response = await adapter._handle_runs(
            _RunRequest({"input": "执行任务", "skill": ["pdf"]})
        )
        payload = json.loads(response.text)

        assert response.status == 400
        assert payload["error"]["param"] == "skill"
        assert payload["error"]["code"] == "invalid_skill"
        assert adapter._run_streams == {}

    asyncio.run(_run())


def test_runs_rejects_path_like_skill_name():
    """skill 字段只能是名称，路径形式必须在进入加载器之前被拒绝。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        response = await adapter._handle_runs(
            _RunRequest({"input": "执行任务", "skill": "../private/skill"})
        )
        payload = json.loads(response.text)

        assert response.status == 400
        assert payload["error"]["code"] == "invalid_skill"
        assert adapter._run_streams == {}

    asyncio.run(_run())


def test_runs_returns_not_found_for_unknown_skill():
    """格式合法但未安装、已禁用或平台不兼容的 Skill 统一视为不可用。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        with patch(
            "agent.skill_commands.resolve_skill_command_key",
            return_value=None,
        ):
            response = await adapter._handle_runs(
                _RunRequest({"input": "执行任务", "skill": "missing-skill"})
            )
        payload = json.loads(response.text)

        assert response.status == 404
        assert payload["error"]["param"] == "skill"
        assert payload["error"]["code"] == "skill_not_found"
        assert adapter._run_streams == {}

    asyncio.run(_run())


def test_runs_rejects_registered_skill_when_payload_cannot_be_loaded():
    """注册项存在但 Skill 文件加载失败时，不能启动一个无 Skill 的降级 run。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        with patch(
            "agent.skill_commands.resolve_skill_command_key",
            return_value="/pdf",
        ), patch(
            "agent.skill_commands.get_skill_commands",
            return_value={"/pdf": {"name": "pdf"}},
        ), patch(
            "agent.skill_commands.build_skill_invocation_message",
            return_value=None,
        ):
            response = await adapter._handle_runs(
                _RunRequest({"input": "执行任务", "skill": "pdf"})
            )
        payload = json.loads(response.text)

        assert response.status == 422
        assert payload["error"]["param"] == "skill"
        assert payload["error"]["code"] == "skill_unavailable"
        assert adapter._run_streams == {}

    asyncio.run(_run())


def test_skills_listing_uses_api_server_platform_filter():
    """Skill 选择列表必须应用 api_server 专属禁用配置。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        captured = {}

        def _get_skill_commands():
            from gateway.session_context import get_session_env

            captured["platform"] = get_session_env("intellect_SESSION_PLATFORM")
            return {"/pdf": {"name": "pdf", "description": "PDF tools"}}

        with patch(
            "agent.skill_commands.get_skill_commands",
            side_effect=_get_skill_commands,
        ), patch(
            "tools.skills_tool._find_all_skills",
            return_value=[
                {
                    "name": "pdf",
                    "description": "PDF tools",
                    "category": "Documents",
                },
                {
                    "name": "disabled-skill",
                    "description": "Must not be listed",
                    "category": "Other",
                },
            ],
        ), patch(
            "tools.skills_tool._sort_skills",
            side_effect=lambda skills: skills,
        ):
            response = await adapter._handle_skills(_RunRequest({}))
        payload = json.loads(response.text)

        assert response.status == 200
        assert captured == {"platform": "api_server"}
        assert payload["data"] == [
            {
                "name": "pdf",
                "description": "PDF tools",
                "category": "Documents",
            }
        ]

    asyncio.run(_run())


def test_skills_listing_keeps_builtin_hub_and_local_sources():
    """旧接口不得因新增来源过滤而丢失 builtin、Hub 或 local Skill。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        all_metadata = [
            {"name": "builtin-skill", "description": "内置", "category": "System"},
            {"name": "hub-skill", "description": "Hub", "category": "Hub"},
            {"name": "local-skill", "description": "本地", "category": "Custom"},
        ]
        commands = {
            f"/{skill['name']}": {"name": skill["name"]}
            for skill in all_metadata
        }

        with patch(
            "agent.skill_commands.get_skill_commands",
            return_value=commands,
        ), patch(
            "tools.skills_tool._find_all_skills",
            return_value=all_metadata,
        ), patch(
            "tools.skills_tool._sort_skills",
            side_effect=lambda skills: skills,
        ):
            response = await adapter._handle_skills(_RunRequest({}))

        assert response.status == 200
        assert json.loads(response.text) == {"object": "list", "data": all_metadata}

    asyncio.run(_run())


def test_custom_skills_listing_returns_only_local_available_skills():
    """新接口只返回可调用的 local Skill，并保留原有排序与元数据结构。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig(extra={"key": "test-secret"}))
        captured = {}

        def _get_skill_commands():
            from gateway.session_context import get_session_env

            captured["platform"] = get_session_env("intellect_SESSION_PLATFORM")
            return {
                "/builtin-skill": {"name": "builtin-skill"},
                "/hub-skill": {"name": "hub-skill"},
                "/manual-local": {"name": "manual-local"},
                "/agent-local": {"name": "agent-local"},
            }

        # disabled-local 与 incompatible-local 虽然存在元数据，但没有出现在
        # api_server 命令注册表中，因此必须在来源过滤前就被共享逻辑排除。
        all_metadata = [
            {"name": "builtin-skill", "description": "内置", "category": "System"},
            {"name": "hub-skill", "description": "Hub", "category": "Hub"},
            {"name": "manual-local", "description": "手工创建", "category": "Custom"},
            {"name": "agent-local", "description": "Agent 创建", "category": "Custom"},
            {"name": "disabled-local", "description": "已禁用", "category": "Custom"},
            {
                "name": "incompatible-local",
                "description": "平台不兼容",
                "category": "Custom",
            },
        ]

        with patch(
            "agent.skill_commands.get_skill_commands",
            side_effect=_get_skill_commands,
        ), patch(
            "tools.skills_tool._find_all_skills",
            return_value=all_metadata,
        ), patch(
            "tools.skills_tool._sort_skills",
            side_effect=lambda skills: sorted(
                skills,
                key=lambda skill: (skill.get("category") or "", skill["name"]),
            ),
        ), patch(
            "tools.skill_usage._read_bundled_manifest_names",
            return_value={"builtin-skill"},
        ), patch(
            "tools.skill_usage._read_hub_installed_names",
            return_value={"hub-skill"},
        ):
            response = await adapter._handle_custom_skills(
                _RunRequest(
                    {},
                    headers={"Authorization": "Bearer test-secret"},
                )
            )
        payload = json.loads(response.text)

        assert response.status == 200
        assert captured == {"platform": "api_server"}
        assert payload == {
            "object": "list",
            "data": [
                {"name": "agent-local", "description": "Agent 创建", "category": "Custom"},
                {"name": "manual-local", "description": "手工创建", "category": "Custom"},
            ],
        }

    asyncio.run(_run())


def test_custom_skills_listing_returns_empty_list_when_no_local_skill_exists():
    """仅有 builtin 或 Hub Skill 时，新接口应返回 200 和空数组。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        with patch(
            "agent.skill_commands.get_skill_commands",
            return_value={"/builtin-skill": {"name": "builtin-skill"}},
        ), patch(
            "tools.skills_tool._find_all_skills",
            return_value=[
                {"name": "builtin-skill", "description": "内置", "category": "System"}
            ],
        ), patch(
            "tools.skills_tool._sort_skills",
            side_effect=lambda skills: skills,
        ), patch(
            "tools.skill_usage._read_bundled_manifest_names",
            return_value={"builtin-skill"},
        ), patch(
            "tools.skill_usage._read_hub_installed_names",
            return_value=set(),
        ):
            response = await adapter._handle_custom_skills(_RunRequest({}))

        assert response.status == 200
        assert json.loads(response.text) == {"object": "list", "data": []}

    asyncio.run(_run())


def test_custom_skills_listing_requires_existing_bearer_auth():
    """配置 API key 后，新接口必须沿用现有 401 错误契约。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig(extra={"key": "test-secret"}))
        response = await adapter._handle_custom_skills(_RunRequest({}))
        payload = json.loads(response.text)

        assert response.status == 401
        assert payload["error"] == {
            "message": "Invalid API key",
            "type": "invalid_request_error",
            "code": "invalid_api_key",
        }

    asyncio.run(_run())


def test_custom_skills_listing_returns_server_error_on_enumeration_failure():
    """枚举失败时不泄露内部异常，并沿用 OpenAI 风格 server_error。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        with patch(
            "agent.skill_commands.get_skill_commands",
            side_effect=RuntimeError("sensitive filesystem detail"),
        ):
            response = await adapter._handle_custom_skills(_RunRequest({}))
        payload = json.loads(response.text)

        assert response.status == 500
        assert payload["error"]["message"] == "Failed to enumerate custom skills"
        assert payload["error"]["type"] == "server_error"
        assert "sensitive filesystem detail" not in response.text

    asyncio.run(_run())


def test_capabilities_advertises_run_skill_selection():
    """外部平台可通过 capabilities 判断当前服务是否支持显式 Skill。"""

    async def _run():
        adapter = APIServerAdapter(PlatformConfig())
        response = await adapter._handle_capabilities(_RunRequest({}))
        payload = json.loads(response.text)

        assert response.status == 200
        assert payload["features"]["run_skill_selection"] is True
        assert payload["features"]["custom_skills_api"] is True
        assert payload["endpoints"]["skills"] == {
            "method": "GET",
            "path": "/v1/skills",
        }
        assert payload["endpoints"]["custom_skills"] == {
            "method": "GET",
            "path": "/v1/skills/custom",
        }

    asyncio.run(_run())
