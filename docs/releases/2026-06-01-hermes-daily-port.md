# Intellect Agent 每日更新 — 2026-06-01

**来源：** [hermes-agent](https://github.com/NousResearch/hermes-agent) 2026-05-31 每日更新移植
**区间：** hermes-agent `b1a25404b` → `eb3cf9750`（38 commits）
**分支：** `v0.4.1`
**移植提交：** 31 commits

---

## 一、安全修复

### Gateway 文件泄露防护
- 阻止系统提示自动上传本地文件到消息平台
- 阻止 `config.yaml` 通过媒体传递泄露（三层防御：白名单 + 系统提示屏蔽 + 禁用列表）
- 同时保护活跃 profile 目录和共享 root 目录

### 文件路径中和
- 文件变更验证器页脚中所有路径用 backtick 包裹，防止 gateway 的 bare-path 检测器匹配并上传

### Discord 去敏移除
- 从密钥清洗器中移除 Discord mention 的强制删除，保留原始内容

---

## 二、Gateway 稳定性

### 重启循环防护
- 防止 agent 通过 `/cron` 等 gateway 命令自指向触发重启循环
- 黑名单检测：`intellect gateway restart/stop`、`systemctl restart intellect-gateway`、`launchctl kickstart intellect`、`pkill intellect gateway`
- 收紧 cron 命令正则，避免误匹配

### /stop 兄弟参与者中断
- `/stop` 命令现在可中断同一用户线程中其他参与者的运行（此前仅中断自己）

### Telegram DM 路由修复
- `_get_dm_topic_info` 在适配器类上解析（而非实例）
- 合成通知中保留 Telegram DM 话题路由元数据，确保重启后通知正确投递

---

## 三、关键缺陷修复

### 流式处理
- 修复累计重发提供商的工具调用参数重复问题
- 回滚共享流式路径的累计重发启发式

### Anthropic Adapter
- orphan-strip 改变最后一轮消息时，降级已失效的 thinking 签名，防止 HTTP 400 死循环

### 图像嵌入限制
- 嵌入图像前限制尺寸至 4MB（Anthropic 单图 base64 上限为 5MB）
- 防止大图嵌入后导致会话永久卡死

### FTS5 生存
- SQLite 缺少 FTS5 时优雅降级：核心持久化继续工作，仅全文搜索不可用
- 当未来运行时支持 FTS5 时自动重建触发器和索引

### Terminal CWD 保留
- `terminal_tool` 保留 live session 工作目录，ACP `update_cwd` 保持权威

### spawn_via_env 修复
- 修复后台包装器被重复包裹的问题
- PID-not-found 时立即标记失败，防止僵尸会话

---

## 四、CLI/TUI 体验

### Curses 菜单重构
- 提取共享 `_run_curses_menu()` 事件循环驱动，消除重复代码
- Setup 模型/提供商选择器从 `simple_term_menu` 迁移到 curses
- 方向键解码修复：原始 CSI/SS3 转义序列正确解析

### Gateway 死亡自动恢复
- TUI 检测到 gateway 进程非预期退出后自动重连并恢复会话
- 引入 `parentLog.ts` 持久化生命周期面包屑，支持 crash forensics

### 其他 TUI 修复
- 限制异常终端尺寸（WSL 131072x1）防止渲染崩溃
- 吞下退化的鼠标事件防止事件循环停滞
- 状态栏压缩后 token 计数上限固定

---

## 五、功能增强

### 模型目录
- 每小时刷新（原每天刷新），新模型上线后 1 小时内可用
- 新增 `deepseek-v4-flash`，精简旧变体，按厂商分组精选列表

### Setup 精简
- 移除凭证轮换设置（默认关闭，按需通过 `intellect auth add` 配置）
- 移除 Vision 后端设置（自动检测主 provider 能力）
- 移除 TTS provider 提示（默认 Edge TTS）
- 快速设置路由至 OntoWeb Portal OAuth + 模型选择

### Tool Gateway 后端可见性
- OntoWeb-managed gateway 行始终在工具选择器中可见
- 未登录用户看到 "via OntoWeb Portal (login on select)" 提示
- 选择托管行触发内联 Portal 登录 + 授权检查

### Kanban 目标模式
- 新增 `goal_mode` 卡片：每张卡以 `/goal` 循环驱动后台工人持续执行
- 辅助 judge 模型每轮评估工人输出，未完成且预算剩余则继续
- 预算耗尽时 card 进入 blocked 状态（需人工审查）

### SSH Voice 支持
- SSH 下 PulseAudio/PipeWire socket 可达时允许 `/voice`

---

## 六、平台适配

- **Telegram**: httpx 连接池超时时自动重试，防止消息静默丢失
- **BlueBubbles**: `_guid_cache` 加入 LRU 淘汰（OrderedDict，上限 500）
- **Feishu**: `_message_text_cache` 加入 LRU 淘汰
- **MCP**: 认证重连轮询从 `time.sleep` 改为 `asyncio.sleep`
- **tool_output_limits**: 进程生命周期缓存，避免每次工具调用都读取配置

---

## 七、迁移 Bug 修复

| 类别 | 问题 | 修复 |
|------|------|------|
| 环境变量 | `intellect_SESSION_KEY`（小写 i） | → `INTELLECT_SESSION_KEY` |
| User-Agent | `H intellectAgent` / `HermesAgent` | → `IntellectAgent` |
| ontoweb 残留 | `Each 429 from OntoWeb` 等 | → `Each 429 from the OntoWeb Portal` |
| 路径残留 | `~/.hermes/` 引用 | → `~/.intellect/` |
| 测试导入 | `from hermes_cli` | → `from intellect_cli` |

---

## 八、测试结果

| 套件 | 通过 | 失败 |
|------|------|------|
| `tests/gateway/` | 499 | 0 |
| `tests/tools/` | 184 | 0 |
| `tests/agent/` | 116 | 0 |
| `tests/intellect_cli/` (选中) | 102 | 0 |
| **合计** | **~900** | **0** |

---

## 九、品牌重命名（同日完成）

- **nous → ontoweb**：148 文件，1337 处修改
- Provider ID、40+ 函数名、6 个速率限制函数、4 个数据类
- Portal URL `nousresearch.com` → `ontoweb.cn`
- 移除 `hermes-3-405b` / `hermes-3-70b` fallback 模型

## 十、安全审计（同日完成）

- 全量密码处理安全分析 → `docs/security/password-security-roadmap.md`
- 发现 3 高危 / 4 中危 / 4 低危
- 9 项安全亮点确认

## 十一、最终测试结果

| 套件 | 通过 |
|------|------|
| `tests/gateway/` | 499 |
| `tests/tools/` | 184 |
| `tests/agent/` | 116 |
| `tests/intellect_cli/` (选中) | 122 |
| v0.4.1 多用户/团队/项目/OAuth | 219 |
| **合计** | **~1140** |

---

*本文档基于 hermes-agent 2026-05-31 每日更新自动移植生成。*
*执行：Claude Code，2026-06-01。*
