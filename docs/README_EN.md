<div align="center">

A thin MCP wrapper around `codex exec --json`, with a synchronous compatibility tool, an async task pool, session resume support, and pollable structured events.

English | [简体中文](../README.md)

</div>

---

## What This Repository Actually Does

The current implementation keeps the surface area intentionally small:

- expose a stable MCP server with `FastMCP`
- run real work through `codex exec --json`
- normalize Codex JSONL output into pollable task events
- provide both a blocking compatibility tool and a recommended async workflow

The five tools that matter in the current codebase are:

| Tool | Purpose | Best for |
| --- | --- | --- |
| `codex` | Blocking wrapper that waits for completion | short tasks, legacy clients |
| `codex_start` | Start an async task and return `task_id` immediately | long tasks, parallel work |
| `codex_check` | Poll task status and incremental events | progress tracking |
| `codex_cancel` | Cancel a running task | user aborts, timeout handling |
| `codex_list` | List running and recently completed tasks | debugging and operations |

---

## Current Capabilities

- built directly on `codex exec --json`
- resume existing Codex sessions via `SESSION_ID` / `session_id`
- pass images through to the underlying `--image` CLI flag
- pass `model_reasoning_effort` through to `codex exec --config`
- keep up to `5` async tasks running concurrently
- expose structured event types: `thinking`, `text`, `tool_call`, `tool_result`, `command`, `error`
- keep recently finished task results in memory for a short time

---

## Prerequisites

- Python `3.12+`
- `codex` installed and available in `PATH`
- `uv` / `uvx`
- Claude Code CLI if you plan to register this server through Claude Code

Quick sanity checks:

```bash
codex --version
uvx --version
```

---

## Installation

### Claude Code

If you already installed an older version, remove it first:

```bash
claude mcp remove codex
```

Then add the current repository:

```bash
claude mcp add codex -s user --transport stdio -- \
  uvx --from git+https://github.com/shilong20/codexmcp.git codexmcp
```

Verify:

```bash
claude mcp list
```

### Generic stdio MCP configuration

If your client accepts a raw stdio command, the core process is:

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

### Refreshing cached `uvx` installs

Restarting the client does not guarantee that `uvx --from git+...` will fetch fresh code. If you suspect cached code is being reused, force a refresh once in a terminal:

```bash
uvx --refresh --from git+https://github.com/shilong20/codexmcp.git codexmcp --help
```

---

## Recommended Usage Pattern

### For short tasks or old clients

Use `codex`.

### For long-running work, parallel jobs, or progress reporting

Prefer the async workflow:

1. call `codex_start`
2. poll with `codex_check`
3. stop with `codex_cancel` if needed

This is the most reliable path in the current implementation because it does not hold the MCP connection open for the full task duration.

---

## Minimal Async Example

### 1. Start a task

```json
{
  "prompt": "Inspect the failing tests in the repository and propose a fix",
  "cwd": "/absolute/path/to/repo",
  "sandbox": "read-only",
  "model_reasoning_effort": "xhigh"
}
```

Response:

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "running",
  "started_at": "2026-03-17T09:30:00.123456"
}
```

### 2. Poll progress

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "include_events": true,
  "since_event_index": 0
}
```

Typical in-flight response:

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

When the task completes, the response also includes:

- `exit_code`
- `result`
- `session_id`
- `usage`

### 3. Incremental polling

Use the previous `total_events` value as the next `since_event_index`.

Notes:

- each task keeps at most `100` recent events
- if older events are trimmed, `event_base_index` increases
- clients should treat `since_event_index` as a global event index, not as an array offset within `recent_events`

---

## Tool Reference

One compatibility detail is worth calling out explicitly:

- synchronous `codex` uses `PROMPT` / `cd` / `SESSION_ID`
- async `codex_start` uses `prompt` / `cwd` / `session_id`

This asymmetry is real in the current codebase and not a documentation typo.

### `codex`

Blocking compatibility wrapper. Internally it still uses the task pool, but it waits until completion before returning.

### Required arguments

| Argument | Type | Description |
| --- | --- | --- |
| `PROMPT` | `string` | task instruction sent to Codex |
| `cd` | `Path` | working directory |

### Optional arguments

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `sandbox` | `read-only \| workspace-write \| danger-full-access` | `read-only` | execution sandbox |
| `SESSION_ID` | `string` | `""` | resume an existing session; empty string starts a new one |
| `skip_git_repo_check` | `bool` | `true` | allow non-Git directories |
| `return_all_messages` | `bool` | `false` | include the full normalized event stream |
| `image` | `Path[]` | `[]` | images attached to the initial prompt |
| `dangerously_bypass_approvals_and_sandbox` | `bool` | `false` | extremely dangerous; bypass approvals and sandboxing |
| `profile` | `string` | `""` | profile name from `~/.codex/config.toml`; do not pass this unless the user explicitly asked for a non-default profile |
| `model_reasoning_effort` | `string` | `""` | passed through to `codex exec --config` |

### Success response

```json
{
  "success": true,
  "SESSION_ID": "019cf442-479c-7ab1-8278-3b31ff38d7bf",
  "agent_messages": "Codex final reply"
}
```

With `return_all_messages=true`, the response also contains:

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

### Failure response

```json
{
  "success": false,
  "error": "Task failed: ..."
}
```

---

### `codex_start`

Starts an async task and returns immediately.

### Required arguments

| Argument | Type | Description |
| --- | --- | --- |
| `prompt` | `string` | task instruction sent to Codex |
| `cwd` | `Path` | working directory |

### Optional arguments

The semantics are almost the same as `codex`, but the naming follows the async form:

| Argument | Type | Default |
| --- | --- | --- |
| `sandbox` | `read-only \| workspace-write \| danger-full-access` | `read-only` |
| `session_id` | `string` | `""` |
| `skip_git_repo_check` | `bool` | `true` |
| `image` | `Path[]` | `[]` |
| `dangerously_bypass_approvals_and_sandbox` | `bool` | `false` |
| `profile` | `string` | `""` |
| `model_reasoning_effort` | `string` | `""` |

### Response

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "running",
  "started_at": "2026-03-17T09:30:00.123456"
}
```

Startup failure:

```json
{
  "error": "Max concurrent tasks reached (5)"
}
```

---

### `codex_check`

Checks async task state and optionally returns incremental events.

### Arguments

| Argument | Type | Default | Description |
| --- | --- | --- | --- |
| `task_id` | `string` | - | task ID returned by `codex_start` |
| `include_events` | `bool` | `true` | include normalized events |
| `since_event_index` | `int` | `0` | return only events after this index |

### Running response

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

### Completed response

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "completed",
  "elapsed_ms": 9123,
  "event_base_index": 0,
  "recent_events": [],
  "total_events": 4,
  "exit_code": 0,
  "result": "Codex final reply",
  "session_id": "019cf442-479c-7ab1-8278-3b31ff38d7bf",
  "usage": {
    "input_tokens": 1234,
    "output_tokens": 456,
    "cached_input_tokens": 0
  }
}
```

### Expired response

Once a completed task ages out of the "recently completed" cache, it no longer appears in `codex_list`. If its history summary is still retained, `codex_check` returns:

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "expired",
  "summary": {
    "task_id": "task_1742190000_ab12cd34",
    "status": "completed",
    "prompt": "Inspect the failing tests in the repository and propose a fix",
    "elapsed_ms": 9123,
    "event_count": 4,
    "exit_code": 0,
    "completed_at": "2026-03-17T09:30:09.123456",
    "result_preview": "First 200 characters of the final result"
  }
}
```

If the task cannot be found at all:

```json
{
  "error": "Task not found: task_..."
}
```

---

### `codex_cancel`

Cancels a running task.

Successful cancellation:

```json
{
  "task_id": "task_1742190000_ab12cd34",
  "status": "cancelled",
  "elapsed_ms": 1533
}
```

If the task already finished, the current state and a message are returned instead. If the task does not exist, an error is returned.

Implementation details:

- send `SIGTERM`
- wait `5` seconds
- escalate to `SIGKILL` if the process is still alive

---

### `codex_list`

Lists running and recently completed tasks:

```json
{
  "tasks": [
    {
      "task_id": "task_1742190000_ab12cd34",
      "status": "running",
      "prompt": "Inspect the failing tests in the repository and propose a fix",
      "elapsed_ms": 4212,
      "event_count": 2
    }
  ]
}
```

"Recently completed" here means tasks that are still within the in-memory retention window.

---

## Runtime Limits

These are the current code-backed limits that older docs often get wrong:

| Item | Current value |
| --- | --- |
| max concurrent tasks | `5` |
| per-task timeout | `1800s` |
| retained events per task | `100` |
| recent completed-result retention | `600s` |
| max history entries | `50` |
| kill grace period on cancel | `5s` |

---

## Parameter Guidance

- `model_reasoning_effort`
  This is the only extra Codex config exposed explicitly by the wrapper today. The wrapper no longer exposes a `model` argument. Use `high` for code-writing tasks and `xhigh` for debugging, review, analysis, or other non-writing work.
- `sandbox`
  Use `read-only` for analysis and review. Switch to `danger-full-access` only when file edits are actually needed.
- `profile`
  This can materially change Codex behavior. Avoid passing it unless the user explicitly asked for a named profile.
- `dangerously_bypass_approvals_and_sandbox`
  This is the real argument name in the current implementation. Older docs calling this `yolo` are stale.
- `skip_git_repo_check`
  The current default is `true`, not `false`.

---

## Development

```bash
git clone https://github.com/shilong20/codexmcp.git
cd codexmcp
uv sync
```

Minimal verification:

```bash
./.venv/bin/python -m unittest discover -s tests -v
python3 -m build
```

---

## License

This project is licensed under the [MIT License](../LICENSE).
