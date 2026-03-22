<div align="center">

# codex-mcp-server

MCP server for Codex CLI — tmux persistence, git worktree isolation, async dispatch.

[English](./docs/README_EN.md) | 简体中文

</div>

---

## 特性

- **tmux 持久化** — `full-access` 任务运行在 tmux session 中，网络断开、窗口关闭后任务继续执行；`read-only` 任务直接 subprocess 执行
- **git worktree 隔离** — `full-access` 模式自动创建独立 worktree，并行任务互不干扰
- **阻塞 + 异步双模式** — `codex` 同步等待结果，`codex_dispatch` 立即返回后台运行
- **多轮对话 (resume)** — 通过 `session_id` 延续上一次 Codex 会话
- **文件系统持久化** — 任务元数据存储在 `~/.codexmcp/tasks/`，服务重启后可恢复
- **工作区日志** — 自动在 `<cwd>/.codex-tasks/` 创建 symlink，IDE 中直接查看日志

## 工具

| 工具 | 作用 | 适合场景 |
| --- | --- | --- |
| `codex` | 阻塞执行，等待完成后返回结果 | 代码审阅、短任务、并行模块实现 |
| `codex_dispatch` | 后台分派，立即返回 task_id | 长任务（数十分钟到数小时） |
| `codex_status` | 查询任务状态和进度 | 追踪后台任务、获取结果 |
| `codex_cancel` | 取消运行中的任务 | 终止不需要的任务 |

---

## 前置要求

- Python `3.12+`
- `codex` CLI 已安装且在 PATH 中
- `tmux` 已安装（`full-access` 模式必须）
- `git` 已安装（`full-access` 模式必须）

```bash
codex --version
tmux -V
git --version
```

---

## 安装

从 [PyPI](https://pypi.org/project/codex-mcp-server/) 安装：

```bash
pip install codex-mcp-server
```

### Claude Code

```bash
claude mcp add codex -s user --transport stdio -- \
  uvx codex-mcp-server
```

验证：

```bash
claude mcp list
```

### Cursor / 通用 MCP 客户端

在 MCP 配置文件（如 `mcp.json`）中添加：

```json
{
  "mcpServers": {
    "codex": {
      "command": "uvx",
      "args": ["codex-mcp-server"]
    }
  }
}
```

### 更新到最新版

`uvx` 会缓存已安装的包。更新到新版本时需要加 `--refresh` 刷新缓存：

```bash
uvx --refresh codex-mcp-server --help
```

> **注意**：如果使用 PyPI 镜像源（如清华源），新版本可能需要 5-15 分钟才能同步。若 `--refresh` 仍拉取到旧版本，可临时指定官方源：
> ```bash
> uvx --refresh --index-url https://pypi.org/simple/ codex-mcp-server --help
> ```

### 从源码安装（开发用）

```bash
git clone https://github.com/shilong20/codexmcp.git
cd codexmcp
pip install -e .
```

---

## 快速上手

### 1. 代码审阅（阻塞，只读）

```json
{
  "tool": "codex",
  "arguments": {
    "prompt": "审阅 src/auth/ 目录的代码质量和安全性",
    "cwd": "/workspace/my-project",
    "topic": "review-auth_module-v1",
    "sandbox": "read-only"
  }
}
```

### 2. 并行模块实现（阻塞，写入）

并发调用多个 `codex`，每个使用不同 `topic`：

```json
{
  "tool": "codex",
  "arguments": {
    "prompt": "实现用户注册模块...",
    "cwd": "/workspace/my-project",
    "topic": "implement-user_register-v1",
    "sandbox": "full-access"
  }
}
```

每个 `full-access` 任务自动创建独立的 git worktree 和分支 `agent/<topic>`。

### 3. 长任务后台分派

```json
{
  "tool": "codex_dispatch",
  "arguments": {
    "prompt": "重构整个项目为异步架构...",
    "cwd": "/workspace/my-project",
    "topic": "longrun-async_refactor-v1",
    "sandbox": "full-access"
  }
}
```

立即返回 `task_id`。之后用 `codex_status` 查看进度。

### 4. Resume 多轮对话

首次执行返回 `session_id`。修改代码后继续：

```json
{
  "tool": "codex",
  "arguments": {
    "prompt": "我已修改了代码，请重新审阅",
    "cwd": "/workspace/my-project",
    "topic": "review-auth_module-v2",
    "sandbox": "read-only",
    "session_id": "019d0aa8-..."
  }
}
```

> resume 时 topic 版本号 +1（如 `v1`→`v2`），传入上次的 `session_id`。worktree 基于 `<type>-<description>` 生成（不含版本号），自动复用。

---

## 参数说明

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `prompt` | str | 是 | 任务指令 |
| `cwd` | Path | 是 | 工作目录（绝对路径） |
| `topic` | str | 是 | 任务标识，格式 `<type>-<desc>-v<ver>`。用于 task_id、worktree 分支名、tmux session 名（full-access） |
| `sandbox` | `read-only` / `full-access` | 是 | 权限模式。read-only 不建 worktree 不走 tmux；full-access 创建 worktree + tmux 隔离 |
| `session_id` | str | 否 | 恢复之前的 Codex 会话（多轮对话） |

## 返回结构

阻塞 `codex` 完成后返回：

```json
{
  "success": true,
  "task_id": "codex-implement-user_register-v1",
  "session_id": "019d0aa8-...",
  "result": "Codex 的最终回复文本",
  "exit_code": 0,
  "elapsed_seconds": 45.2,
  "usage": {"input_tokens": 5000, "output_tokens": 1200},
  "worktree_dir": "/workspace/project-agent-implement-user_register",
  "agent_branch": "agent/implement-user_register",
  "base_branch": "main",
  "diff_stat": "3 files changed, 42 insertions(+), 10 deletions(-)",
  "commits_ahead": 2
}
```

- `session_id` 用于 resume
- worktree 相关字段仅 `full-access` 模式返回
- 当 `CODEXMCP_READONLY_FALLBACK` 启用且 sandbox 为 `read-only` 时，返回额外的 `readonly_audit` 字段：

```json
{
  "readonly_audit": {
    "mode": "fallback",
    "violations_detected": 0,
    "violations": [],
    "verdict": "CLEAN"
  }
}
```

`verdict` 为 `VIOLATION` 时表示检测到违规操作，`violations` 列出具体违规项。

---

## 环境变量

通过 MCP 服务进程环境变量配置：

| 变量 | 说明 | 示例 |
|------|------|------|
| `CODEX_PROFILE` | codex 配置文件名 | `fast` |
| `CODEX_REASONING_EFFORT` | 推理强度 | `high`、`xhigh` |
| `CODEXMCP_READONLY_FALLBACK` | 容器环境下启用 read-only 降级模式（见下方说明） | `1` |

### Read-Only 降级模式

在 Docker 等容器环境中，`read-only` sandbox 可能因 `bwrap` namespace 限制无法正常工作。设置 `CODEXMCP_READONLY_FALLBACK=1` 后：

1. `read-only` 请求内部使用 `danger-full-access` 执行
2. 自动注入强约束提示词，禁止任何文件修改操作
3. 任务完成后自动审计日志，检测违规操作
4. 返回结果中附加 `readonly_audit` 字段

> 执行路径不变：不创建 tmux session 和 git worktree，仍走 subprocess 直接执行。

---

## 日志

| 位置 | 路径 |
|------|------|
| 工作区 symlink | `<cwd>/.codex-tasks/<topic>/codex-exec.log` |
| 主存储 | `~/.codexmcp/tasks/<task_id>/codex-exec.log` |
| 实时查看 | `tmux attach -t codex-<topic>`（仅 full-access） |

---

## 开发

```bash
git clone https://github.com/shilong20/codexmcp.git
cd codexmcp
pip install -e .
```

---

## 许可证

[MIT License](./LICENSE)
