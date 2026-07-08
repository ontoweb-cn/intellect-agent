# PR：修正误导性 Intellect Desktop 文档

> 依据：`2026-07-08-intellect-desktop-feasibility-review.md` §7.3
> 状态：补丁已起草（待 PR）

## Summary

本仓库**未发布**官方 Intellect Desktop 聊天应用。此前文档将「Intellect Desktop `.exe`」与 **NSIS CLI 安装器**、**PowerShell `install.ps1`** 混为一谈，并引用不存在的 Electron 产品。本 PR 统一表述：Windows 官方路径为 `install.ps1` / 可选 `Intellect-Agent-*-Setup.exe`（CLI bundle）；图形界面请用 **`intellect webui start`**。

## 改动文件

| 文件 | 变更 |
|------|------|
| `website/docs/getting-started/installation.md` | 删除「下载 Intellect Desktop」；改为 NSIS + WebUI；增加 *not shipped* admonition |
| `website/docs/user-guide/windows-native.md` | 章节重命名 `#optional-nsis-installer`；区分 CLI Setup vs Desktop GUI |
| `website/docs/getting-started/updating.md` | 并发 `intellect.exe` 说明去掉 Intellect Desktop |
| `website/i18n/zh-Hans/.../installation.md` | 中文同步 |
| `website/i18n/zh-Hans/.../windows-native.md` | 中文同步 |
| `website/i18n/zh-Hans/.../updating.md` | 中文同步 |
| `docs/packaging/gitee-releases.md` | `Desktop .exe` → `NSIS Setup.exe`（CLI bundle，非 GUI） |
| `intellect_cli/main.py` | update 提示/注释泛化为 gateway、WebUI、GUI wrapper |
| `webui/static/style.css` | CSS 注释标明 future design target |
| `tests/intellect_cli/test_update_concurrent_quarantine.py` | 断言与新用户文案对齐 |

## 刻意未改（可选 follow-up）

| 文件 | 原因 |
|------|------|
| `website/src/data/userStories.json` | 社区展示引用，保留第三方项目原话 |
| `scripts/install.ps1` 注释 | 「desktop GUI's onboarding wizard」为 Stage 协议**前瞻性**设计，可在后续 PR 改为「future GUI driver」 |
| `docs/packaging/windows.md` | 无 Intellect Desktop 误导句（仅 VS C++ workload 等） |

## Test plan

- [ ] `scripts/run_tests.sh tests/intellect_cli/test_update_concurrent_quarantine.py -q`
- [ ] 文档链接：`installation.md` → `windows-native#optional-nsis-installer` 锚点有效
- [ ] 中英文 installation / windows-native / updating 三处表述一致
- [ ] `grep -R "Intellect Desktop" website/docs docs/packaging intellect_cli/main.py` 无用户面向误导句（userStories 除外）

## 建议 PR 标题

```
docs: clarify Intellect Desktop is not shipped; fix Windows installer wording
```

## 建议 PR 正文

```markdown
## Summary

- Correct docs that implied an official **Intellect Desktop** chat `.exe` exists on Windows.
- Clarify that Gitee/NSIS `Intellect-Agent-*-Setup.exe` installs the **CLI/agent bundle**, not a GUI app.
- Point users to `intellect webui start` for a graphical interface today.
- Generalize `intellect update` Windows lock messages (gateway / WebUI / REPL, not "Intellect Desktop").

Background: `docs/plans/2026-07-08-intellect-desktop-feasibility-review.md`.

## Test plan

- [x] `scripts/run_tests.sh tests/intellect_cli/test_update_concurrent_quarantine.py -q`
- [ ] Spot-check installation + windows-native EN/zh pages
```
