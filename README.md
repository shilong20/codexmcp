![CodexMCP](./images/title.png)

<div align="center">

一个围绕 `codex exec --json` 构建的薄包装 MCP 服务器，提供同步兼容接口、异步任务池、会话续跑与事件轮询。

[English](./docs/README_EN.md) | 简体中文

</div>

---

## 项目现状

`codexmcp` 当前的真实定位，不再是“给 Codex 额外发明一层复杂语义”，而是：

- 用 `FastMCP` 暴露一组稳定的 MCP 工具。
- 用 `codex exec --json` 跑底层任务。
- 把 Codex 的 JSONL 事件流整理成适合轮询的结构化返回。
- 提供一个同步兼容包装器，以及一套更适合长任务的异步接口。

如果你只关心现在这版仓库到底提供什么能力，可以直接记住 5 个工具：

| 工具 | 作用 | 适合场景 |
| --- | --- | --- |
| `codex` | 同步执行，阻塞直到完成 | 老客户端兼容、短任务 |
| `codex_start` | 启动异步任务并立即返回 `task_id` | 长任务、并行任务 |
| `codex_check` | 轮询任务状态与增量事件 | 进度追踪、读取最终结果 |
| `codex_cancel` | 取消运行中的任务 | 超时、用户中止 |
| `codex_list` | 查看运行中和最近完成的任务 | 调试、运维 |

---

## 当前实现特性

- 基于 `codex exec --json`，不自己重造代理协议。
- 支持通过 `SESSION_ID` / `session_id` 续跑既有 Codex 会话。
- 支持图片输入透传给 Codex CLI 的 `--image`。
- 支持把 `model_reasoning_effort` 透传到底层 `codex exec --config`。
- 支持异步任务池，默认最多并发 `5` 个任务。
- 支持事件轮询，事件类型包含 `thinking`、`text`、`tool_call`、`tool_result`、`command`、`error`。
- 支持最近完成任务的短期保留，便于客户端晚一点再取结果。

---

## 前置要求

- Python `3.12+`
- 已安装并能在 `PATH` 中找到 `codex`
- 已安装 `uv` / `uvx`
- 若通过 Claude Code 使用，还需要本机已安装 Claude Code CLI

建议先自检：

```bash
codex --version
uvx --version
```

---

## 安装

### Claude Code

如果之前装过旧版，先移除：

```bash
claude mcp remove codex
```

再安装当前仓库版本：

```bash
claude mcp add codex -s user --transport stdio -- \
  uvx --from git+https://github.com/shilong20/codexmcp.git codexmcp
```

验证：

```bash
claude mcp list
```

看到 `codex` 已连接即可。

### 通用 MCP 配置

如果你的 MCP 客户端支持手写 stdio 配置，核心命令就是：

```json
{
  "command": "uvx",
  "args": [
    "--from",
    "git+https://github.com/shilong20/codexmcp.git",
    "codexmcp"
  ]
}
```

### 更新已安装版本

`uvx --from git+...` 不保证“重启客户端”就一定重新拉取最新源码。若怀疑拿到的是旧缓存，可以在终端先强制刷新一次：

```bash
uvx --refresh --from git+https://github.com/shilong20/codexmcp.git codexmcp --help
```

---

## 推荐使用方式

### 短任务或兼容旧客户端

直接调用同步工具 `codex`。

### 长任务、并行任务、需要进度回显

优先使用异步三件套：

1. `codex_start` 启动任务，拿到 `task_id`
2. `codex_check` 轮询状态和增量事件
3. 如需中止，调用 `codex_cancel`

这是当前仓库最推荐的使用路径，因为它不会长时间阻塞 MCP 连接。

---

## 一个最小异步流程

### 1. 启动任务

```json
{
  "prompt": "检查当前仓库中的失败测试并给出修复建议",
  "cwd": "/absolute/path/to/repo",
  "sandbox": "read-only",
  "model_reasoning_effort": "xhigh"
}
```

返回：

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "running",
  "started_at": "2026-03-17T09:30:00.123456"
}
```

### 2. 轮询进度

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "include_events": true,
  "since_event_index": 0
}
```

任务运行中时，典型返回如下：

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "running",
  "elapsed_ms": 4212,
  "event_base_index": 0,
  "recent_events": [
    { "type": "thinking", "text": "Inspecting the repository layout..." },
    { "type": "command", "text": "rg --files (exit=0)" }
  ],
  "total_events": 2
}
```

完成后会额外返回：

- `exit_code`
- `result`
- `session_id`
- `usage`

### 3. 增量轮询

下一轮把上一次返回的 `total_events` 作为新的 `since_event_index` 即可。

注意：

- 单个任务最多保留最近 `100` 条事件。
- 更早的事件如果被裁剪，`event_base_index` 会递增。
- 客户端应把“下一次请求起点”理解成全局事件索引，而不是当前 `recent_events` 数组下标。

---

## 工具文档

这里有一个当前实现必须明说的兼容性细节：

- 同步工具 `codex` 使用 `PROMPT` / `cd` / `SESSION_ID`
- 异步工具 `codex_start` 使用 `prompt` / `cwd` / `session_id`

这不是文档笔误，而是当前代码里的真实接口差异。

### `codex`

同步兼容包装器。它内部仍然走异步任务池，但会一直等待到底层任务结束后再返回。

### 必填参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `PROMPT` | `string` | 发给 Codex 的任务指令 |
| `cd` | `Path` | Codex 的工作目录 |

### 可选参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `sandbox` | `read-only \| workspace-write \| danger-full-access` | `read-only` | 底层命令沙箱 |
| `SESSION_ID` | `string` | `""` | 续跑已有会话；空字符串表示新会话 |
| `skip_git_repo_check` | `bool` | `true` | 允许在非 Git 目录运行 |
| `return_all_messages` | `bool` | `false` | 是否返回完整事件流 |
| `image` | `Path[]` | `[]` | 附加到初始提示词的图片 |
| `dangerously_bypass_approvals_and_sandbox` | `bool` | `false` | 极危险；跳过审批并绕过沙箱 |
| `profile` | `string` | `""` | 从 `~/.codex/config.toml` 载入命名 profile；除非用户明确指定，否则不要传 |
| `model_reasoning_effort` | `string` | `""` | 透传到底层 `codex exec --config` |

### 返回

成功：

```json
{
  "success": true,
  "SESSION_ID": "019cf442-479c-7ab1-8278-3b31ff38d7bf",
  "agent_messages": "Codex 的最终回复"
}
```

如果 `return_all_messages=true`，还会额外包含：

```json
{
  "all_messages": [
    {
      "type": "thinking",
      "text": "Planning the investigation",
      "tool_name": null,
      "tool_input": null,
      "timestamp": "2026-03-17T09:35:00.000000"
    }
  ]
}
```

失败：

```json
{
  "success": false,
  "error": "Task failed: ..."
}
```

---

### `codex_start`

异步启动任务并立即返回。

### 必填参数

| 参数 | 类型 | 说明 |
| --- | --- | --- |
| `prompt` | `string` | 发给 Codex 的任务指令 |
| `cwd` | `Path` | Codex 的工作目录 |

### 可选参数

与 `codex` 基本一致，但参数名是异步风格：

| 参数 | 类型 | 默认值 |
| --- | --- | --- |
| `sandbox` | `read-only \| workspace-write \| danger-full-access` | `read-only` |
| `session_id` | `string` | `""` |
| `skip_git_repo_check` | `bool` | `true` |
| `image` | `Path[]` | `[]` |
| `dangerously_bypass_approvals_and_sandbox` | `bool` | `false` |
| `profile` | `string` | `""` |
| `model_reasoning_effort` | `string` | `""` |

### 返回

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "running",
  "started_at": "2026-03-17T09:30:00.123456"
}
```

启动失败时：

```json
{
  "error": "Max concurrent tasks reached (5)"
}
```

---

### `codex_check`

查询异步任务状态，可选返回增量事件。

### 参数

| 参数 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `task_id` | `string` | - | `codex_start` 返回的任务 ID |
| `include_events` | `bool` | `true` | 是否返回事件 |
| `since_event_index` | `int` | `0` | 仅返回此索引之后的新事件 |

### 运行中返回

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "running",
  "elapsed_ms": 4212,
  "event_base_index": 0,
  "recent_events": [],
  "total_events": 0
}
```

### 完成后返回

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "completed",
  "elapsed_ms": 9123,
  "event_base_index": 0,
  "recent_events": [],
  "total_events": 4,
  "exit_code": 0,
  "result": "Codex 的最终回复",
  "session_id": "019cf442-479c-7ab1-8278-3b31ff38d7bf",
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 456,
    "cached_input_tokens": 0
  }
}
```

### 过期后返回

完成任务在“最近结果缓存”过期后，不再出现在 `codex_list` 中；此时若历史摘要仍在，会返回：

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "expired",
  "summary": {
    "task_id": "task_1742190000_ab12cd34",
    "status": "completed",
    "prompt": "检查当前仓库中的失败测试并给出修复建议",
    "elapsed_ms": 9123,
    "event_count": 4,
    "exit_code": 0,
    "completed_at": "2026-03-17T09:30:09.123456",
    "result_preview": "Codex 的最终回复前 200 个字符"
  }
}
```

查不到时：

```json
{
  "error": "Task not found: task_..."
}
```

---

### `codex_cancel`

取消运行中的任务。

成功时：

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "cancelled",
  "elapsed_ms": 1533
}
```

如果任务已结束，会返回当前状态和提示信息；如果任务不存在，则返回错误。

实现细节：

- 先发送 `SIGTERM`
- 宽限 `5` 秒
- 仍未退出则强制 `SIGKILL`

---

### `codex_list`

列出运行中和最近完成的任务：

```json
{
  "tasks": [
    {
      "task_id": "task_1742190000_ab12cd34",
      "status": "running",
      "prompt": "检查当前仓库中的失败测试并给出修复建议",
      "elapsed_ms": 4212,
      "event_count": 2
    }
  ]
}
```

这里的“最近完成”是指仍在内存保留期内的任务。

---

## 运行时行为

当前实现中，一些容易被旧文档写错的运行时限制如下：

| 项目 | 当前值 |
| --- | --- |
| 最大并发任务数 | `5` |
| 单任务超时 | `1800s` |
| 单任务保留事件数 | `100` |
| 最近完成结果保留时长 | `600s` |
| 历史摘要最大条数 | `50` |
| 取消任务强杀宽限 | `5s` |

---

## 参数使用建议

- `model_reasoning_effort`
  当前仓库只额外暴露了这一项底层配置；没有再暴露 `model` 参数。建议写代码任务使用 `high`，调试、审查、分析等非写代码任务使用 `xhigh`。
- `sandbox`
  只读分析用 `read-only`；真正需要改文件时再用 `danger-full-access`。
- `profile`
  会直接影响底层 Codex 行为，除非用户明确要求某个 profile，否则不建议默认传入。
- `dangerously_bypass_approvals_and_sandbox`
  这是当前实际参数名，不是旧文档里的 `yolo`。
- `skip_git_repo_check`
  当前默认值是 `true`，不是旧文档里常写错的 `false`。

---

## 开发

```bash
git clone https://github.com/shilong20/codexmcp.git
cd codexmcp
uv sync
```

最小验证：

```bash
./.venv/bin/python -m unittest discover -s tests -v
python3 -m build
```

---

## 许可证

本项目使用 [MIT License](./LICENSE)。
