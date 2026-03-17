"""FastMCP server implementation for the Codex MCP project."""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Dict, List, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .models import TaskStatus
from .task_pool import TaskPool

mcp = FastMCP("Codex MCP Server-from guda.studio")

_pool = TaskPool()


@mcp.tool(
    name="codex",
    description="""
    Executes a non-interactive Codex session via CLI to perform AI-assisted coding tasks in a secure workspace.
    This tool wraps the `codex exec` command, enabling model-driven code generation, debugging, or automation based on natural language prompts.
    It supports resuming ongoing sessions for continuity and enforces sandbox policies to prevent unsafe operations. Ideal for integrating Codex into MCP servers for agentic workflows, such as code reviews or repo modifications.

    **Key Features:**
        - **Prompt-Driven Execution:** Send task instructions to Codex for step-by-step code handling.
        - **Workspace Isolation:** Operate within a specified directory, with optional Git repo skipping.
        - **Security Controls:** Three sandbox levels balance functionality and safety.
        - **Session Persistence:** Resume prior conversations via `SESSION_ID` for iterative tasks.

    **Edge Cases & Best Practices:**
        - Ensure `cd` exists and is accessible; tool fails silently on invalid paths.
        - If needed, set `return_all_messages` to `True` to parse "all_messages" for detailed tracing (e.g., reasoning, tool calls, etc.).
        - If you pass `model_reasoning_effort`, prefer `high` for actual code writing and `xhigh` for debugging, review, analysis, and other non-writing tasks.

    **Sandbox guidelines:**
        - For code reviews, analysis, or read-only tasks: use `read-only` (default).
        - For writing code, debugging, refactoring, or any task that needs to modify files: use `danger-full-access`.
        - When in doubt, prefer `danger-full-access` for coding/debug tasks to avoid sandbox permission errors.

    NOTE: This is the synchronous compatibility wrapper. For long-running tasks, prefer `codex_start` + `codex_check` for async polling.
    """,
    meta={"version": "0.0.0", "author": "guda.studio"},
)
async def codex(
    PROMPT: Annotated[str, "Instruction for the task to send to codex."],
    cd: Annotated[
        Path, "Set the workspace root for codex before executing the task."
    ],
    sandbox: Annotated[
        Literal["read-only", "workspace-write", "danger-full-access"],
        Field(
            description="Sandbox policy for model-generated commands. Defaults to `read-only`."
        ),
    ] = "read-only",
    SESSION_ID: Annotated[
        str,
        "Resume the specified session of the codex. Defaults to `None`, start a new session.",
    ] = "",
    skip_git_repo_check: Annotated[
        bool,
        "Allow codex running outside a Git repository (useful for one-off directories).",
    ] = True,
    return_all_messages: Annotated[
        bool,
        "Return all messages (e.g. reasoning, tool calls, etc.) from the codex session. Set to `False` by default, only the agent's final reply message is returned.",
    ] = False,
    image: Annotated[
        List[Path],
        Field(
            description="Attach one or more image files to the initial prompt.",
        ),
    ] = [],
    dangerously_bypass_approvals_and_sandbox: Annotated[
        bool,
        Field(
            description="Skip all confirmation prompts and execute commands without sandboxing. EXTREMELY DANGEROUS. Only use when `sandbox` couldn't be applied.",
        ),
    ] = False,
    profile: Annotated[
        str,
        "Configuration profile name to load from `~/.codex/config.toml`. This parameter is strictly prohibited unless explicitly specified by the user.",
    ] = "",
    model_reasoning_effort: Annotated[
        str,
        Field(
            description="Optional reasoning effort override passed through to Codex CLI config. Agent guidance: use `high` for actual code-writing tasks, and `xhigh` for debugging, review, analysis, and other non-writing tasks."
        ),
    ] = "",
) -> Dict[str, Any]:
    """Synchronous compatibility wrapper — blocks until the task completes."""
    try:
        task_id = await _pool.start(
            PROMPT,
            str(cd),
            sandbox,
            session_id=SESSION_ID,
            skip_git_repo_check=skip_git_repo_check,
            images=[str(p) for p in image] if image else None,
            profile=profile,
            model_reasoning_effort=model_reasoning_effort,
            dangerously_bypass=dangerously_bypass_approvals_and_sandbox,
        )
    except (RuntimeError, OSError) as e:
        return {"success": False, "error": str(e)}

    while True:
        task = _pool.get_task(task_id)
        if task and task.status != TaskStatus.RUNNING:
            break
        await asyncio.sleep(0.5)

    if task.status == TaskStatus.COMPLETED:
        result: Dict[str, Any] = {
            "success": True,
            "SESSION_ID": task.session_id,
            "agent_messages": task.result,
        }
    else:
        result = {
            "success": False,
            "error": f"Task {task.status.value}: {task.result or 'No output'}",
        }

    if return_all_messages:
        events = _pool.get_events(task_id)
        result["all_messages"] = [
            {
                "type": e.type.value,
                "text": e.text,
                "tool_name": e.tool_name,
                "tool_input": e.tool_input,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in events
        ]

    return result


@mcp.tool(
    name="codex_start",
    description="""
    Start an async Codex task and return immediately with a task_id.
    Use `codex_check` to poll for progress and results.
    Use `codex_cancel` to cancel a running task.

    This is the recommended approach for long-running tasks — it avoids
    blocking the MCP connection while Codex works.

    **Sandbox guidelines:**
        - For code reviews, analysis, or read-only tasks: use `read-only` (default).
        - For writing code, debugging, refactoring, or any task that needs to modify files: use `danger-full-access`.
        - When in doubt, prefer `danger-full-access` for coding/debug tasks to avoid sandbox permission errors.
        - If you pass `model_reasoning_effort`, prefer `high` for actual code writing and `xhigh` for debugging, review, analysis, and other non-writing tasks.
    """,
)
async def codex_start(
    prompt: Annotated[str, "Instruction for the task to send to codex."],
    cwd: Annotated[
        Path, "Set the workspace root for codex before executing the task."
    ],
    sandbox: Annotated[
        Literal["read-only", "workspace-write", "danger-full-access"],
        Field(
            description="Sandbox policy for model-generated commands. Defaults to `read-only`."
        ),
    ] = "read-only",
    session_id: Annotated[
        str, "Resume a previous session by its ID. Empty string starts new."
    ] = "",
    skip_git_repo_check: Annotated[
        bool,
        "Allow codex running outside a Git repository.",
    ] = True,
    image: Annotated[
        List[Path],
        Field(
            description="Attach one or more image files to the initial prompt.",
        ),
    ] = [],
    dangerously_bypass_approvals_and_sandbox: Annotated[
        bool,
        Field(
            description="Skip all confirmation prompts and execute without sandboxing. EXTREMELY DANGEROUS.",
        ),
    ] = False,
    profile: Annotated[
        str,
        "Configuration profile name to load from `~/.codex/config.toml`.",
    ] = "",
    model_reasoning_effort: Annotated[
        str,
        Field(
            description="Optional reasoning effort override passed through to Codex CLI config. Agent guidance: use `high` for actual code-writing tasks, and `xhigh` for debugging, review, analysis, and other non-writing tasks."
        ),
    ] = "",
) -> Dict[str, Any]:
    """Start an async Codex task."""
    try:
        task_id = await _pool.start(
            prompt,
            str(cwd),
            sandbox,
            session_id=session_id,
            skip_git_repo_check=skip_git_repo_check,
            images=[str(p) for p in image] if image else None,
            profile=profile,
            model_reasoning_effort=model_reasoning_effort,
            dangerously_bypass=dangerously_bypass_approvals_and_sandbox,
        )
    except (RuntimeError, OSError) as e:
        return {"error": str(e)}

    return {
        "task_id": task_id,
        "status": "running",
        "started_at": datetime.now().isoformat(),
    }


@mcp.tool(
    name="codex_check",
    description="""
    Check the status and progress of an async Codex task.
    Returns current status, elapsed time, recent events (text/commands/tool calls),
    and final result when completed.

    Use `since_event_index` for incremental polling — only new events are returned.
    """,
)
async def codex_check(
    task_id: Annotated[str, "The task_id returned by codex_start."],
    include_events: Annotated[
        bool, "Include recent events in the response."
    ] = True,
    since_event_index: Annotated[
        int,
        "Only return events after this index (for incremental polling). 0 returns all.",
    ] = 0,
) -> Dict[str, Any]:
    """Check async Codex task status."""
    task = _pool.get_task(task_id)

    if not task:
        hist = _pool.get_history_entry(task_id)
        if hist:
            return {"task_id": task_id, "status": "expired", "summary": hist}
        return {"error": f"Task not found: {task_id}"}

    elapsed = (task.end_time or datetime.now()) - task.start_time
    resp: Dict[str, Any] = {
        "task_id": task_id,
        "status": task.status.value,
        "elapsed_ms": int(elapsed.total_seconds() * 1000),
    }

    if include_events:
        events = _pool.get_events(task_id, since=since_event_index)
        resp["event_base_index"] = task.event_base_index
        resp["recent_events"] = [
            {
                "type": e.type.value,
                "text": e.text,
                "tool_name": e.tool_name,
                "tool_input": e.tool_input,
                "timestamp": e.timestamp.isoformat(),
            }
            for e in events
        ]
        resp["total_events"] = len(task.events) + task.event_base_index

    if task.status != TaskStatus.RUNNING:
        resp["exit_code"] = task.exit_code
        resp["result"] = task.result
        resp["session_id"] = task.session_id
        if task.usage:
            resp["usage"] = {
                "input_tokens": task.usage.input_tokens,
                "output_tokens": task.usage.output_tokens,
                "cached_input_tokens": task.usage.cached_input_tokens,
            }

    return resp


@mcp.tool(
    name="codex_cancel",
    description="Cancel a running Codex task. Sends SIGTERM then SIGKILL after 5s grace.",
)
async def codex_cancel(
    task_id: Annotated[str, "The task_id to cancel."],
) -> Dict[str, Any]:
    """Cancel a running Codex task."""
    ok = await _pool.cancel(task_id)
    if not ok:
        task = _pool.get_task(task_id)
        if task:
            return {
                "task_id": task_id,
                "status": task.status.value,
                "message": "Task is not running",
            }
        return {"error": f"Task not found: {task_id}"}

    task = _pool.get_task(task_id)
    elapsed = (datetime.now() - task.start_time) if task else None
    return {
        "task_id": task_id,
        "status": "cancelled",
        "elapsed_ms": int(elapsed.total_seconds() * 1000) if elapsed else 0,
    }


@mcp.tool(
    name="codex_list",
    description="List all Codex tasks (running + recently completed). No parameters needed.",
)
async def codex_list() -> Dict[str, Any]:
    """List all Codex tasks."""
    return {"tasks": _pool.list_all()}


def run() -> None:
    """Start the MCP server over stdio transport."""
    try:
        mcp.run(transport="stdio")
    finally:
        asyncio.run(_pool.dispose())
