"""Task lifecycle management with filesystem-based persistence.

Every task is persisted as a JSON file under
``~/.codexmcp/tasks/<task_id>/meta.json`` so that status survives MCP
server restarts.  The actual codex process runs inside a tmux session.
"""

from __future__ import annotations

import asyncio
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from . import tmux, worktree
from .command_builder import build_codex_command
from .models import SandboxMode, TaskMeta, TaskMode, TaskStatus, TaskUsage
from .stream_processor import StreamProcessor

TASKS_ROOT = Path.home() / ".codexmcp" / "tasks"


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _ensure_task_dir(task_id: str) -> Path:
    d = TASKS_ROOT / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _generate_task_id(topic: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", topic).strip("-")
    return f"codex-{safe}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def save_task(meta: TaskMeta) -> None:
    """Persist task metadata to disk."""
    path = TASKS_ROOT / meta.task_id / "meta.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(meta.model_dump_json(indent=2))


def load_task(task_id: str) -> Optional[TaskMeta]:
    """Load task metadata from disk.  Returns None if not found."""
    path = TASKS_ROOT / task_id / "meta.json"
    if not path.exists():
        return None
    try:
        return TaskMeta.model_validate_json(path.read_text())
    except Exception:
        return None


def list_tasks() -> list[TaskMeta]:
    """Return all persisted tasks (any status)."""
    if not TASKS_ROOT.exists():
        return []
    result: list[TaskMeta] = []
    for d in sorted(TASKS_ROOT.iterdir()):
        meta_file = d / "meta.json"
        if meta_file.exists():
            try:
                result.append(TaskMeta.model_validate_json(meta_file.read_text()))
            except Exception:
                continue
    return result


# ---------------------------------------------------------------------------
# Symlink + gitignore
# ---------------------------------------------------------------------------


def _create_workspace_symlink(cwd: str, topic: str, task_dir: Path) -> None:
    """Create a symlink from ``<cwd>/.codex-tasks/<topic>`` → *task_dir*."""
    cwd_path = Path(cwd)
    if not cwd_path.is_dir():
        return
    link_dir = cwd_path / ".codex-tasks"
    link_dir.mkdir(exist_ok=True)
    link_path = link_dir / topic
    if link_path.is_symlink() or link_path.exists():
        link_path.unlink()
    link_path.symlink_to(task_dir)

    gitignore = Path(cwd) / ".gitignore"
    marker = ".codex-tasks/"
    if gitignore.exists():
        content = gitignore.read_text()
        if marker not in content:
            with gitignore.open("a") as f:
                f.write(f"\n# CodexMCP task logs\n{marker}\n")


# ---------------------------------------------------------------------------
# Task start
# ---------------------------------------------------------------------------


async def start_task(
    prompt: str,
    cwd: str,
    topic: str,
    sandbox: SandboxMode,
    *,
    mode: TaskMode = TaskMode.BLOCKING,
    session_id: str = "",
) -> TaskMeta:
    """Start a codex task inside a tmux session.

    When *sandbox* is ``FULL_ACCESS`` and *cwd* is inside a git repo, a
    worktree is created for isolation.
    """
    if not tmux.available():
        raise RuntimeError(
            "tmux is required but not found. "
            "Install: apt install tmux / brew install tmux"
        )
    if sandbox == SandboxMode.FULL_ACCESS and not worktree.git_available():
        raise RuntimeError(
            "git is required for full-access mode. "
            "Install: apt install git / brew install git"
        )

    safe_topic = re.sub(r"[^a-zA-Z0-9_-]", "-", topic).strip("-")
    if not safe_topic:
        raise RuntimeError(
            f"Invalid topic '{topic}': must contain at least one "
            "alphanumeric character (a-z, A-Z, 0-9) or underscore."
        )
    task_id = f"codex-{safe_topic}"

    existing = load_task(task_id)
    if existing and existing.status == TaskStatus.RUNNING:
        if await tmux.session_exists(task_id):
            raise RuntimeError(
                f"Task '{task_id}' is already running. "
                "Cancel it first or choose a different topic."
            )

    # --- worktree ---
    wt_dir: Optional[str] = None
    agent_branch: Optional[str] = None
    base_branch: Optional[str] = None
    effective_cwd = cwd

    needs_worktree = (
        sandbox == SandboxMode.FULL_ACCESS
        and await worktree.is_git_repo(cwd)
    )
    if needs_worktree:
        repo_root = await worktree.get_repo_root(cwd)
        if repo_root:
            base_branch = await worktree.get_current_branch(repo_root)
            wt_dir, agent_branch = await worktree.create_worktree(
                repo_root, safe_topic, base_branch
            )
            effective_cwd = wt_dir

    # --- files ---
    task_dir = _ensure_task_dir(task_id)
    log_file = str(task_dir / "codex-exec.log")
    prompt_file = str(task_dir / "prompt.md")
    Path(prompt_file).write_text(prompt, encoding="utf-8")

    _create_workspace_symlink(cwd, safe_topic, task_dir)

    # --- codex command ---
    codex_args = build_codex_command(
        effective_cwd,
        sandbox.value,
        session_id=session_id,
    )
    codex_cmd_str = " ".join(shlex.quote(a) for a in codex_args)

    shell_cmd = (
        f"set -o pipefail; cd {shlex.quote(effective_cwd)} && "
        f"{codex_cmd_str} < {shlex.quote(prompt_file)} "
        f"2>&1 | tee {shlex.quote(log_file)}; "
        f"echo 'EXIT_CODE='${{PIPESTATUS[0]}} >> {shlex.quote(log_file)}"
    )

    # --- tmux ---
    await tmux.create_session(task_id, shell_cmd)

    # --- persist ---
    meta = TaskMeta(
        task_id=task_id,
        mode=mode,
        prompt=prompt,
        cwd=cwd,
        sandbox=sandbox,
        topic=topic,
        tmux_session=task_id,
        log_file=log_file,
        prompt_file=prompt_file,
        start_time=datetime.now().isoformat(),
        worktree_dir=wt_dir,
        agent_branch=agent_branch,
        base_branch=base_branch,
    )
    save_task(meta)
    return meta


# ---------------------------------------------------------------------------
# Status resolution
# ---------------------------------------------------------------------------


def _read_exit_code(log_file: str) -> Optional[int]:
    """Scan the last few lines of the log for ``EXIT_CODE=<n>``."""
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        for line in reversed(lines[-20:]):
            stripped = line.strip()
            if stripped.startswith("EXIT_CODE="):
                return int(stripped.split("=", 1)[1])
    except (FileNotFoundError, ValueError, IndexError):
        pass
    return None


def _parse_log(log_file: str) -> tuple[Optional[str], Optional[str], Optional[TaskUsage]]:
    """Parse the JSONL log, returning ``(result_text, session_id, usage)``."""
    sp = StreamProcessor()
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().startswith("EXIT_CODE="):
                    continue
                sp.process_line(line)
    except FileNotFoundError:
        return None, None, None
    return (
        sp.result_text or sp.diagnostic_text or None,
        sp.session_id,
        sp.usage,
    )


def _read_log_tail(log_file: str, lines: int = 30) -> str:
    """Return the last *lines* of the log file."""
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except FileNotFoundError:
        return ""


def get_running_progress(log_file: str, recent_lines: int = 30) -> list[dict]:
    """Parse recent log lines into structured progress events."""
    sp = StreamProcessor()
    events: list[dict] = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        for line in all_lines[-recent_lines:]:
            if line.strip().startswith("EXIT_CODE="):
                continue
            event = sp.process_line(line)
            if event:
                events.append({
                    "type": event.type.value,
                    "text": event.text,
                    "tool_name": event.tool_name,
                })
    except FileNotFoundError:
        pass
    return events


async def resolve_status(task_id: str) -> TaskMeta:
    """Determine the real status of a task by inspecting tmux + log file.

    If the task has finished, its metadata is updated on disk.
    """
    meta = load_task(task_id)
    if meta is None:
        raise ValueError(f"Task not found: {task_id}")

    if meta.status != TaskStatus.RUNNING:
        return meta

    alive = await tmux.session_exists(meta.tmux_session)
    exit_code = _read_exit_code(meta.log_file)

    if exit_code is not None:
        result_text, sid, _usage = _parse_log(meta.log_file)
        meta.exit_code = exit_code
        meta.status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED
        meta.end_time = datetime.now().isoformat()
        meta.result = result_text
        meta.session_id = sid
        save_task(meta)
    elif not alive:
        meta.status = TaskStatus.FAILED
        meta.end_time = datetime.now().isoformat()
        meta.result = "tmux session terminated unexpectedly"
        save_task(meta)

    return meta


# ---------------------------------------------------------------------------
# Cancel
# ---------------------------------------------------------------------------


async def cancel_task(task_id: str) -> TaskMeta:
    """Kill the tmux session and mark the task as cancelled."""
    meta = load_task(task_id)
    if meta is None:
        raise ValueError(f"Task not found: {task_id}")

    if meta.status != TaskStatus.RUNNING:
        return meta

    await tmux.kill_session(meta.tmux_session)
    meta.status = TaskStatus.CANCELLED
    meta.end_time = datetime.now().isoformat()
    save_task(meta)
    return meta


# ---------------------------------------------------------------------------
# Blocking wait
# ---------------------------------------------------------------------------


async def wait_for_completion(
    task_id: str, poll_interval: float = 2.0
) -> TaskMeta:
    """Block (async) until the task is no longer RUNNING."""
    while True:
        meta = await resolve_status(task_id)
        if meta.status != TaskStatus.RUNNING:
            return meta
        await asyncio.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Rich status for codex_status tool
# ---------------------------------------------------------------------------


async def get_task_status_detail(task_id: str) -> dict[str, Any]:
    """Return a detailed status dict suitable for the ``codex_status`` tool."""
    meta = await resolve_status(task_id)

    elapsed_s = 0.0
    try:
        start = datetime.fromisoformat(meta.start_time)
        end = (
            datetime.fromisoformat(meta.end_time)
            if meta.end_time
            else datetime.now()
        )
        elapsed_s = (end - start).total_seconds()
    except (ValueError, TypeError):
        pass

    detail: dict[str, Any] = {
        "task_id": meta.task_id,
        "mode": meta.mode.value,
        "status": meta.status.value,
        "topic": meta.topic,
        "elapsed_seconds": round(elapsed_s, 1),
        "cwd": meta.cwd,
        "sandbox": meta.sandbox.value,
    }

    if meta.worktree_dir:
        detail["worktree_dir"] = meta.worktree_dir
    if meta.agent_branch:
        detail["agent_branch"] = meta.agent_branch

    if meta.status == TaskStatus.RUNNING:
        progress = get_running_progress(meta.log_file, recent_lines=20)
        if progress:
            detail["recent_events"] = progress
        tail = _read_log_tail(meta.log_file, lines=10)
        if tail:
            detail["log_tail"] = tail

    if meta.status != TaskStatus.RUNNING:
        detail["exit_code"] = meta.exit_code
        detail["result"] = meta.result
        detail["session_id"] = meta.session_id

        if meta.worktree_dir and meta.base_branch:
            try:
                commits = await worktree.get_commits_ahead(
                    meta.worktree_dir, meta.base_branch
                )
                diff_stat = await worktree.get_diff_stat(
                    meta.worktree_dir, meta.base_branch
                )
                uncommitted = await worktree.get_uncommitted_changes(
                    meta.worktree_dir
                )
                detail["commits_ahead"] = commits
                if diff_stat:
                    detail["diff_stat"] = diff_stat
                if uncommitted:
                    detail["uncommitted_changes"] = uncommitted
            except Exception:
                pass

    return detail
