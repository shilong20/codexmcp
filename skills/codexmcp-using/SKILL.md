---
name: codexmcp-using
description: "调用 CodexMCP 执行 AI 辅助编码任务。支持阻塞式审阅/并行实现和异步后台分派。Use when delegating code review, parallel module implementation, or long-running coding tasks to Codex via MCP."
---

# CodexMCP 使用指南

通过 MCP 调用 Codex CLI 执行编码任务。所有任务运行在 tmux session 中，断连后任务继续。

## 前置条件

| 依赖 | 用途 | 检查 |
|------|------|------|
| tmux | 持久化进程 | `which tmux` |
| git | worktree 隔离（full-access 需要） | `which git` |

不满足时 MCP 会返回错误信息和安装命令。详见 [troubleshoot.md](troubleshoot.md)。

## 工具速查

> 注意：以下示例中的 `server` 值取决于你的 MCP 配置。常见名称有 `codexmcp`、`codex-mcp` 等，请以实际配置为准。

### codex — 阻塞执行

等任务完成后返回结果。适用于审阅、短任务、并行模块实现。

```
CallMcpTool(server="codexmcp", toolName="codex", arguments={
  "prompt": "审阅以下代码...",
  "cwd": "/path/to/project",
  "topic": "review-auth",
  "sandbox": "read-only"
})
```

### codex_dispatch — 异步分派

立即返回 task_id，任务在后台运行。适用于耗时数十分钟到数小时的长任务。

```
CallMcpTool(server="codexmcp", toolName="codex_dispatch", arguments={
  "prompt": "实现完整的用户认证模块...",
  "cwd": "/path/to/project",
  "topic": "impl-auth-module",
  "sandbox": "full-access"
})
```

### codex_status — 查询状态

传 task_id 查单个任务（含进度事件/结果）。不传则列出所有任务。

```
CallMcpTool(server="codexmcp", toolName="codex_status", arguments={
  "task_id": "codex-impl-auth-module"
})
```

### codex_cancel — 取消任务

```
CallMcpTool(server="codexmcp", toolName="codex_cancel", arguments={
  "task_id": "codex-impl-auth-module"
})
```

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | str | 是 | 任务指令 |
| `cwd` | Path | 是 | 工作目录（绝对路径） |
| `topic` | str | 是 | 任务标识。用于 tmux session 名、worktree 分支、task_id |
| `sandbox` | `read-only` / `full-access` | 是 | 权限模式。read-only 不建 worktree；full-access 创建 worktree 隔离 |
| `session_id` | str | 否 | 恢复之前的 Codex 会话，实现多轮对话 |

## 返回结构

阻塞 `codex` 完成后返回（full-access 模式示例，含 worktree 字段）：

```json
{
  "success": true,
  "task_id": "codex-impl-feature",
  "session_id": "thread_abc123",
  "result": "Codex 的最终回复文本",
  "exit_code": 0,
  "elapsed_seconds": 45.2,
  "usage": {"input_tokens": 5000, "output_tokens": 1200},
  "worktree_dir": "/workspace/project-agent-impl-feature",
  "agent_branch": "agent/impl-feature",
  "base_branch": "main",
  "diff_stat": "3 files changed, 42 insertions(+), 10 deletions(-)",
  "commits_ahead": 2
}
```

- `session_id` 用于 resume（多轮对话）
- worktree 相关字段仅 full-access 模式出现

## 环境变量

通过 MCP 服务进程环境变量配置：

| 变量 | 说明 | 示例 |
|------|------|------|
| `CODEX_PROFILE` | codex 配置文件名（对应 `~/.codex/config.toml` 中的 profile） | `fast` |
| `CODEX_REASONING_EFFORT` | 推理强度 | `high`、`xhigh` |

## 日志查看

任务日志同时存在两个位置：
- 工作区：`<cwd>/.codex-tasks/<topic>/codex-exec.log`（symlink，可在 IDE 中直接打开）
- 主存储：`~/.codexmcp/tasks/<task_id>/codex-exec.log`

也可 `tmux attach -t codex-<topic>` 实时查看。

## 使用场景

| 场景 | 详情 |
|------|------|
| 代码审阅 | [scenarios/review.md](scenarios/review.md) |
| 并行模块实现 | [scenarios/parallel.md](scenarios/parallel.md) |
| 长任务后台分派 | [scenarios/dispatch.md](scenarios/dispatch.md) |
| MCP 不可用降级 | [scenarios/cli-fallback.md](scenarios/cli-fallback.md) |

## 故障排查

→ [troubleshoot.md](troubleshoot.md)
