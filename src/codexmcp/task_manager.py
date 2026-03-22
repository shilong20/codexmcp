"""Task lifecycle management with filesystem-based persistence.

Every task is persisted as a JSON file under
``~/.codexmcp/tasks/<task_id>/meta.json`` so that status survives MCP
server restarts.  The actual codex process runs inside a tmux session.
"""

from __future__ import annotations

import asyncio
import os
import re
import shlex
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from . import tmux, worktree
from .command_builder import build_codex_command, is_readonly_fallback
from .models import SandboxMode, TaskMeta, TaskMode, TaskStatus, TaskUsage
from .stream_processor import StreamProcessor

TASKS_ROOT = Path.home() / ".codexmcp" / "tasks"

_READONLY_CONSTRAINT_PROMPT = """\
[CRITICAL SYSTEM CONSTRAINT — READ-ONLY MODE]
You are operating in READ-ONLY review/analysis mode.
Although the sandbox is set to full-access due to environment limitations,
you are STRICTLY FORBIDDEN from modifying the codebase in any way.

PROHIBITED actions (non-exhaustive):
- Writing, creating, editing, moving, copying, or deleting any file
- Running shell commands that modify files (sed -i, tee, >, >>, patch, mv, cp, rm, chmod, etc.)
- Using any tool/function that writes to the filesystem (write_file, edit_file, create_file, apply_patch, etc.)
- Creating or modifying git commits, branches, or tags

If you encounter something that needs fixing, REPORT it in your response.
Do NOT attempt to fix it yourself. Any file modification is a critical violation.

"""


# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

_KEEP_RECENT_COUNT = int(os.environ.get("CODEXMCP_KEEP_TASKS", "10"))
_KEEP_RECENT_SECONDS = int(os.environ.get("CODEXMCP_KEEP_SECONDS", "3600"))


def _cleanup_old_tasks() -> None:
    """Remove completed task dirs beyond the retention policy.

    Retention policy: ``max(last _KEEP_RECENT_COUNT tasks, tasks within
    _KEEP_RECENT_SECONDS)``.  Running tasks are never removed.
    Also cleans stale workspace symlinks.
    """
    if not TASKS_ROOT.exists():
        return
    completed: list[tuple[str, str, Path]] = []  # (end_time, task_id, dir)
    for d in TASKS_ROOT.iterdir():
        meta_file = d / "meta.json"
        if not meta_file.exists():
            continue
        try:
            meta = TaskMeta.model_validate_json(meta_file.read_text())
        except Exception:
            continue
        if meta.status == TaskStatus.RUNNING:
            continue
        completed.append((meta.end_time or meta.start_time, meta.task_id, d))

    if not completed:
        return

    completed.sort(key=lambda x: x[0], reverse=True)
    cutoff = (datetime.now() - timedelta(seconds=_KEEP_RECENT_SECONDS)).isoformat()

    # keep = max(last N, within 1h)
    keep_by_count = set(tid for _, tid, _ in completed[:_KEEP_RECENT_COUNT])
    keep_by_time = set(tid for ts, tid, _ in completed if ts >= cutoff)
    keep = keep_by_count | keep_by_time

    for _ts, task_id, task_dir in completed:
        if task_id in keep:
            continue
        _remove_symlinks_for(task_dir)
        import shutil as _shutil
        _shutil.rmtree(task_dir, ignore_errors=True)


def _remove_symlinks_for(task_dir: Path) -> None:
    """Remove any workspace symlinks that point to *task_dir*."""
    try:
        meta = TaskMeta.model_validate_json((task_dir / "meta.json").read_text())
    except Exception:
        return
    cwd = Path(meta.cwd)
    link_dir = cwd / ".codex-tasks"
    if not link_dir.is_dir():
        return
    for link in link_dir.iterdir():
        try:
            if link.is_symlink() and link.resolve() == task_dir.resolve():
                link.unlink()
        except OSError:
            continue


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _ensure_task_dir(task_id: str) -> Path:
    d = TASKS_ROOT / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d


_VALID_TYPES = {"review", "implement", "longrun", "test"}

_TOPIC_RE = re.compile(
    r"^(?P<type>[a-z]+)-(?P<desc>[a-zA-Z0-9_]+(?:-[a-zA-Z0-9_]+)*)-v(?P<ver>\d+)$"
)


def parse_topic(topic: str) -> tuple[str, str, str]:
    """Parse ``<type>-<description>-v<version>`` and return (type, desc, version).

    Raises ``ValueError`` if the format is invalid.
    """
    m = _TOPIC_RE.match(topic)
    if not m:
        raise ValueError(
            f"Invalid topic '{topic}'. "
            "Expected format: <type>-<description>-v<version>  "
            f"(type: {'/'.join(sorted(_VALID_TYPES))}, "
            "description: words_joined_by_underscores, "
            "version: integer).  "
            "Example: review-auth_handler-v1"
        )
    t, desc, ver = m.group("type"), m.group("desc"), m.group("ver")
    if t not in _VALID_TYPES:
        raise ValueError(
            f"Unknown topic type '{t}'. Must be one of: "
            f"{', '.join(sorted(_VALID_TYPES))}"
        )
    return t, desc, ver


def _worktree_key(topic_type: str, topic_desc: str) -> str:
    """Return the version-independent key used for worktree dirs and branches."""
    return f"{topic_type}-{topic_desc}"


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
    _cleanup_old_tasks()

    topic_type, topic_desc, _topic_ver = parse_topic(topic)

    use_tmux = sandbox == SandboxMode.FULL_ACCESS
    if use_tmux and not tmux.available():
        raise RuntimeError(
            "tmux is required for full-access mode but not found. "
            "Install: apt install tmux / brew install tmux"
        )
    if sandbox == SandboxMode.FULL_ACCESS and not worktree.git_available():
        raise RuntimeError(
            "git is required for full-access mode. "
            "Install: apt install git / brew install git"
        )

    task_id = _generate_task_id(topic)
    wt_key = _worktree_key(topic_type, topic_desc)

    existing = load_task(task_id)
    if existing and existing.status == TaskStatus.RUNNING:
        if use_tmux and await tmux.session_exists(task_id):
            raise RuntimeError(
                f"Task '{task_id}' is already running. "
                "Cancel it first or choose a different topic."
            )

    if sandbox == SandboxMode.FULL_ACCESS:
        for t in list_tasks():
            if t.status != TaskStatus.RUNNING or t.task_id == task_id:
                continue
            try:
                t_type, t_desc, _ = parse_topic(t.topic)
            except ValueError:
                continue
            if _worktree_key(t_type, t_desc) == wt_key:
                raise RuntimeError(
                    f"Another task '{t.task_id}' is still running on the "
                    f"same worktree (agent/{wt_key}). Wait for it to finish "
                    "or cancel it first."
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
                repo_root, wt_key, base_branch
            )
            effective_cwd = wt_dir

    # --- prompt injection for readonly fallback ---
    effective_prompt = prompt
    if sandbox == SandboxMode.READ_ONLY and is_readonly_fallback():
        effective_prompt = _READONLY_CONSTRAINT_PROMPT + prompt

    # --- files ---
    task_dir = _ensure_task_dir(task_id)
    log_file = str(task_dir / "codex-exec.log")
    prompt_file = str(task_dir / "prompt.md")
    Path(prompt_file).write_text(effective_prompt, encoding="utf-8")

    safe_topic = re.sub(r"[^a-zA-Z0-9_-]", "-", topic).strip("-")
    _create_workspace_symlink(cwd, safe_topic, task_dir)

    # --- codex command ---
    codex_args = build_codex_command(
        effective_cwd,
        sandbox.value,
        session_id=session_id,
    )
    codex_cmd_str = " ".join(shlex.quote(a) for a in codex_args)

    # --- persist (before launch so meta exists for status checks) ---
    meta = TaskMeta(
        task_id=task_id,
        mode=mode,
        prompt=prompt,
        cwd=cwd,
        sandbox=sandbox,
        topic=topic,
        tmux_session=task_id if use_tmux else "",
        log_file=log_file,
        prompt_file=prompt_file,
        start_time=datetime.now().isoformat(),
        worktree_dir=wt_dir,
        agent_branch=agent_branch,
        base_branch=base_branch,
    )
    save_task(meta)

    if use_tmux:
        shell_cmd = (
            f"set -o pipefail; cd {shlex.quote(effective_cwd)} && "
            f"{codex_cmd_str} < {shlex.quote(prompt_file)} "
            f"2>&1 | tee {shlex.quote(log_file)}; "
            f"echo 'EXIT_CODE='${{PIPESTATUS[0]}} >> {shlex.quote(log_file)}"
        )
        await tmux.create_session(task_id, shell_cmd)
    else:
        await _run_direct(codex_args, prompt_file, log_file, effective_cwd, meta)

    return meta


# ---------------------------------------------------------------------------
# Direct subprocess execution (read-only, no tmux)
# ---------------------------------------------------------------------------


async def _run_direct(
    codex_args: list[str],
    prompt_file: str,
    log_file: str,
    cwd: str,
    meta: TaskMeta,
) -> None:
    """Run codex directly as a subprocess, writing output to *log_file*.

    Used for read-only tasks that don't need tmux persistence.
    The function waits for the process to complete and updates *meta* in place.
    """
    with open(prompt_file, "r") as stdin_f, open(log_file, "w") as log_f:
        proc = await asyncio.create_subprocess_exec(
            *codex_args,
            stdin=stdin_f,
            stdout=log_f,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )
        exit_code = await proc.wait()

    with open(log_file, "a") as f:
        f.write(f"\nEXIT_CODE={exit_code}\n")

    result_text, sid, _usage = _parse_log(log_file)
    meta.exit_code = exit_code
    meta.status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED
    meta.end_time = datetime.now().isoformat()
    meta.result = result_text
    meta.session_id = sid
    save_task(meta)


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

    if not meta.tmux_session:
        exit_code = _read_exit_code(meta.log_file)
        if exit_code is not None:
            result_text, sid, _usage = _parse_log(meta.log_file)
            meta.exit_code = exit_code
            meta.status = TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED
            meta.end_time = datetime.now().isoformat()
            meta.result = result_text
            meta.session_id = sid
            save_task(meta)
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

    if meta.tmux_session:
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
