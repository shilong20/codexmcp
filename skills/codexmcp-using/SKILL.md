---
name: codexmcp-using
description: "调用 CodexMCP 执行 AI 辅助编码任务。支持阻塞式审阅/并行实现和异步后台分派。Use when delegating code review, parallel module implementation, or long-running coding tasks to Codex via MCP."
---

# CodexMCP 使用指南

通过 MCP 调用 Codex CLI 执行编码任务。

## 工具一览

| 工具 | 类型 | 用途 |
|------|------|------|
| `codex` | 阻塞 | 等待完成后返回结果（**主推**） |
| `codex_dispatch` | 异步 | 立即返回，后台运行 |
| `codex_status` | 查询 | 查询任务状态/进度/结果 |
| `codex_cancel` | 控制 | 取消运行中的任务 |

## 快速参考

```
CallMcpTool(server="codex", toolName="codex", arguments={
  "prompt": "任务描述...",
  "cwd": "/absolute/path",
  "topic": "review-auth_module-v1",
  "sandbox": "read-only"
})
```

| 参数 | 必填 | 说明 |
|------|------|------|
| `prompt` | 是 | 任务指令 |
| `cwd` | 是 | 工作目录（绝对路径） |
| `topic` | 是 | 任务标识，格式见下方 |
| `sandbox` | 是 | `read-only` / `full-access` |
| `session_id` | 否 | 恢复之前的会话（多轮对话） |

> `server` 取决于 MCP 配置（常见：`codex`、`codexmcp`）。

## Topic 命名

格式：`<type>-<description>-v<version>`

| type | 场景 | sandbox |
|------|------|---------|
| `review` | 审阅、分析 | read-only |
| `implement` | 功能实现、修复、重构 | full-access |
| `longrun` | 长任务后台分派 | full-access |
| `test` | 测试补充、测试重构 | full-access |

- description 用 `_` 分割单词：`auth_module`、`db_migration`
- version 方便标记轮次：`v1` → `v2`

**Resume 规则**：topic 版本号 +1（如 `v1` → `v2`），传入上次的 `session_id`。
版本号变化不会创建新 worktree——worktree 和分支名基于 `<type>-<description>` 生成（不含版本），自动复用。

示例：`review-auth_handler-v1`、`implement-user_register-v1`、`longrun-async_refactor-v1`

## 模式选择

**默认使用阻塞模式（`codex`）。** 仅在满足以下条件时使用 `codex_dispatch`：
1. 用户明确要求分派/后台执行/长任务，**或**
2. 你有清晰的计划，Codex 后台执行期间你自己有实质性工作要做

```
需要 Codex 的任务
  │
  ├─ 审阅/分析 → codex (read-only)
  ├─ 写代码/修复 → codex (full-access)
  ├─ 多模块并行 → 多个 codex (full-access) 并发
  └─ 长任务 + 用户要求分派 → codex_dispatch (full-access)
```

| 场景 | 详情 |
|------|------|
| 代码审阅 | [scenarios/review.md](scenarios/review.md) |
| 并行实现 | [scenarios/parallel.md](scenarios/parallel.md) |
| 后台分派 | [scenarios/dispatch.md](scenarios/dispatch.md) |
| MCP 不可用 | [scenarios/cli-fallback.md](scenarios/cli-fallback.md) |

Prompt 编写指南和完整示例已融入各场景文档中。

## MCP 不可用处理

<HARD-GATE>
若 MCP 调用返回错误或超时：

1. **立即停止** — 不继续依赖 Codex 的步骤
2. **告知用户** — 提供选项："等待恢复" 或 "CLI 降级"（见 [cli-fallback.md](scenarios/cli-fallback.md)）
3. **严禁**：自己执行 Codex 任务、编造结果、静默跳过
</HARD-GATE>

## 返回结构

```json
{
  "success": true,
  "task_id": "codex-implement-user_register-v1",
  "session_id": "019d0aa8-...",
  "result": "Codex 回复文本",
  "exit_code": 0,
  "elapsed_seconds": 45.2,
  "usage": {"input_tokens": 5000, "output_tokens": 1200},
  "worktree_dir": "/workspace/project-agent-implement-user_register",
  "agent_branch": "agent/implement-user_register",
  "base_branch": "main",
  "diff_stat": "3 files changed, 42 insertions(+)",
  "commits_ahead": 2
}
```

- worktree 字段仅 full-access 出现
- `session_id` 用于 resume（topic 版本号 +1）

## 补充信息

| 项目 | 说明 |
|------|------|
| 环境变量 | `CODEX_PROFILE`（配置文件）、`CODEX_REASONING_EFFORT`（推理强度） |
| 日志 | 工作区 `<cwd>/.codex-tasks/<topic>/`（symlink）；主存储 `~/.codexmcp/tasks/<task_id>/` |
| tmux | `tmux attach -t codex-<topic>`（仅 full-access） |
| 故障排查 | [troubleshoot.md](troubleshoot.md) |
