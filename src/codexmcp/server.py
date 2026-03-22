"""FastMCP server — 4 tools backed by tmux + worktree task manager."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Dict, Literal

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from . import task_manager
from .models import SandboxMode, TaskMode, TaskStatus
from .command_builder import is_readonly_fallback
from .stream_processor import audit_readonly_violations

mcp = FastMCP("Codex MCP Server-from guda.studio")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _calc_elapsed(meta: task_manager.TaskMeta) -> float:
    try:
        start = datetime.fromisoformat(meta.start_time)
        end = (
            datetime.fromisoformat(meta.end_time)
            if meta.end_time
            else datetime.now()
        )
        return round((end - start).total_seconds(), 1)
    except (ValueError, TypeError):
        return 0.0


def _build_result(meta: task_manager.TaskMeta) -> Dict[str, Any]:
    """Build the standard result dict from completed task metadata."""
    result_text, session_id, usage = task_manager._parse_log(meta.log_file)
    resp: Dict[str, Any] = {
        "success": meta.status == TaskStatus.COMPLETED,
        "task_id": meta.task_id,
        "session_id": session_id or meta.session_id,
        "result": result_text or meta.result,
        "exit_code": meta.exit_code,
        "elapsed_seconds": _calc_elapsed(meta),
    }
    if usage:
        resp["usage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
    if meta.worktree_dir:
        resp["worktree_dir"] = meta.worktree_dir
        resp["agent_branch"] = meta.agent_branch
        resp["base_branch"] = meta.base_branch
    # readonly fallback audit
    if meta.sandbox == SandboxMode.READ_ONLY and is_readonly_fallback():
        resp["readonly_audit"] = audit_readonly_violations(meta.log_file)
    return resp


async def _enrich_worktree_info(
    resp: Dict[str, Any], meta: task_manager.TaskMeta
) -> None:
    """Add diff stats to the response for worktree-backed tasks."""
    if not meta.worktree_dir or not meta.base_branch:
        return
    try:
        from . import worktree

        commits = await worktree.get_commits_ahead(
            meta.worktree_dir, meta.base_branch
        )
        diff_stat = await worktree.get_diff_stat(
            meta.worktree_dir, meta.base_branch
        )
        resp["commits_ahead"] = commits
        if diff_stat:
            resp["diff_stat"] = diff_stat
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="codex",
    description=(
        "Execute a Codex task and block until completion. "
        "Use read-only sandbox for reviews/analysis, full-access for code modifications. "
        "full-access mode creates a git worktree for isolation. "
        "Returns the final result, session_id (for resume), and git diff stats."
    ),
)
async def codex(
    prompt: Annotated[str, "Task instruction to send to Codex."],
    cwd: Annotated[Path, "Workspace root directory for Codex."],
    topic: Annotated[
        str,
        "Task identifier used for tmux session, worktree branch, and task tracking.",
    ],
    sandbox: Annotated[
        Literal["read-only", "full-access"],
        Field(
            description=(
                "Permission mode. read-only for reviews/analysis, "
                "full-access for code writing (creates git worktree)."
            )
        ),
    ],
    session_id: Annotated[
        str,
        "Resume a previous Codex session by its ID. Leave empty to start new.",
    ] = "",
) -> Dict[str, Any]:
    """Execute a blocking Codex task — waits for completion and returns result."""
    resolved_cwd = str(cwd.expanduser().resolve())
    try:
        meta = await task_manager.start_task(
            prompt,
            resolved_cwd,
            topic,
            SandboxMode(sandbox),
            mode=TaskMode.BLOCKING,
            session_id=session_id,
        )
    except (RuntimeError, OSError, ValueError) as e:
        return {"success": False, "error": str(e)}

    meta = await task_manager.wait_for_completion(meta.task_id)
    resp = _build_result(meta)
    await _enrich_worktree_info(resp, meta)
    return resp


@mcp.tool(
    name="codex_dispatch",
    description=(
        "Dispatch a long-running Codex task to background and return immediately. "
        "The task runs in a persistent tmux session that survives disconnects. "
        "Use codex_status to check progress and codex_cancel to stop."
    ),
)
async def codex_dispatch(
    prompt: Annotated[str, "Task instruction to send to Codex."],
    cwd: Annotated[Path, "Workspace root directory for Codex."],
    topic: Annotated[
        str,
        "Task identifier used for tmux session, worktree branch, and task tracking.",
    ],
    sandbox: Annotated[
        Literal["read-only", "full-access"],
        Field(
            description=(
                "Permission mode. read-only for reviews/analysis, "
                "full-access for code writing (creates git worktree)."
            )
        ),
    ],
    session_id: Annotated[
        str,
        "Resume a previous Codex session by its ID. Leave empty to start new.",
    ] = "",
) -> Dict[str, Any]:
    """Start an async Codex task and return immediately with a task_id."""
    resolved_cwd = str(cwd.expanduser().resolve())
    try:
        meta = await task_manager.start_task(
            prompt,
            resolved_cwd,
            topic,
            SandboxMode(sandbox),
            mode=TaskMode.DISPATCH,
            session_id=session_id,
        )
    except (RuntimeError, OSError, ValueError) as e:
        return {"error": str(e)}

    return {
        "task_id": meta.task_id,
        "status": "running",
        "topic": meta.topic,
        "started_at": meta.start_time,
        "log_file": meta.log_file,
        "worktree_dir": meta.worktree_dir,
    }


@mcp.tool(
    name="codex_status",
    description=(
        "Check task status. Pass task_id for single task detail "
        "(with progress events if running, or result/diff if completed). "
        "Omit task_id to list all tasks."
    ),
)
async def codex_status(
    task_id: Annotated[
        str,
        "Task ID to query. Leave empty to list all tasks.",
    ] = "",
) -> Dict[str, Any]:
    """Check Codex task status or list all tasks."""
    if task_id:
        try:
            return await task_manager.get_task_status_detail(task_id)
        except ValueError as e:
            return {"error": str(e)}

    all_tasks = task_manager.list_tasks()
    summaries = []
    for t in all_tasks:
        if t.status == TaskStatus.RUNNING:
            t = await task_manager.resolve_status(t.task_id)
        summaries.append({
            "task_id": t.task_id,
            "topic": t.topic,
            "status": t.status.value,
            "mode": t.mode.value,
            "elapsed_seconds": _calc_elapsed(t),
        })
    return {"tasks": summaries}


@mcp.tool(
    name="codex_cancel",
    description="Cancel a running Codex task by killing its tmux session.",
)
async def codex_cancel(
    task_id: Annotated[str, "The task ID to cancel."],
) -> Dict[str, Any]:
    """Cancel a running Codex task."""
    try:
        meta = await task_manager.cancel_task(task_id)
    except ValueError as e:
        return {"error": str(e)}

    return {
        "task_id": task_id,
        "status": meta.status.value,
        "elapsed_seconds": _calc_elapsed(meta),
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run() -> None:
    """Start the MCP server over stdio transport."""
    mcp.run(transport="stdio")
