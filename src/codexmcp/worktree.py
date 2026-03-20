"""Git worktree helpers for parallel codex task isolation."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import Optional


async def _run(
    cmd: list[str],
    cwd: Optional[str] = None,
    check: bool = False,
) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{err}"
        )
    return proc.returncode, out, err


def git_available() -> bool:
    """Check if git is on PATH."""
    return shutil.which("git") is not None


async def is_git_repo(path: str) -> bool:
    """Return True if *path* is inside a git repository."""
    if not git_available():
        return False
    rc, _, _ = await _run(["git", "rev-parse", "--git-dir"], cwd=path)
    return rc == 0


async def get_repo_root(path: str) -> Optional[str]:
    """Return the top-level directory of the git repo containing *path*."""
    rc, stdout, _ = await _run(
        ["git", "rev-parse", "--show-toplevel"], cwd=path
    )
    if rc != 0:
        return None
    return stdout.strip()


async def get_current_branch(path: str) -> Optional[str]:
    """Return the current branch name, or None if detached / not a repo."""
    rc, stdout, _ = await _run(
        ["git", "branch", "--show-current"], cwd=path
    )
    if rc != 0:
        return None
    return stdout.strip() or None


async def create_worktree(
    repo_path: str,
    topic: str,
    base_branch: Optional[str] = None,
) -> tuple[str, str]:
    """Create a git worktree for an agent task.

    Returns ``(worktree_path, agent_branch)``.
    If the worktree already exists on the correct branch it is reused.
    """
    repo_name = os.path.basename(repo_path)
    agent_branch = f"agent/{topic}"
    worktree_dir = os.path.join(
        os.path.dirname(repo_path), f"{repo_name}-agent-{topic}"
    )

    if base_branch is None:
        base_branch = await get_current_branch(repo_path) or "HEAD"

    if os.path.isdir(worktree_dir):
        existing = await get_current_branch(worktree_dir)
        if existing == agent_branch:
            return worktree_dir, agent_branch
        raise RuntimeError(
            f"Worktree already exists at {worktree_dir} on branch "
            f"'{existing}', expected '{agent_branch}'"
        )

    branch_exists = (
        await _run(
            ["git", "rev-parse", "--verify", f"refs/heads/{agent_branch}"],
            cwd=repo_path,
        )
    )[0] == 0

    if branch_exists:
        await _run(
            ["git", "worktree", "add", worktree_dir, agent_branch],
            cwd=repo_path,
            check=True,
        )
    else:
        await _run(
            [
                "git",
                "worktree",
                "add",
                "-b",
                agent_branch,
                worktree_dir,
                base_branch,
            ],
            cwd=repo_path,
            check=True,
        )
    return worktree_dir, agent_branch


async def get_diff_stat(
    worktree_path: str, base_branch: str
) -> str:
    """Return ``git diff --stat`` between HEAD and *base_branch*."""
    rc, stdout, _ = await _run(
        ["git", "diff", base_branch, "--stat"],
        cwd=worktree_path,
    )
    return stdout.strip() if rc == 0 else ""


async def get_commits_ahead(
    worktree_path: str, base_branch: str
) -> int:
    """Count commits on HEAD that are not on *base_branch*."""
    rc, stdout, _ = await _run(
        ["git", "rev-list", "--count", f"{base_branch}..HEAD"],
        cwd=worktree_path,
    )
    if rc != 0:
        return 0
    try:
        return int(stdout.strip())
    except ValueError:
        return 0


async def get_uncommitted_changes(worktree_path: str) -> str:
    """Return short status of uncommitted changes (excluding docs/)."""
    rc, stdout, _ = await _run(
        [
            "git",
            "status",
            "--short",
            "--",
            ":(exclude)docs/",
            ":(exclude)codex-*",
        ],
        cwd=worktree_path,
    )
    return stdout.strip() if rc == 0 else ""


async def remove_worktree(
    repo_path: str,
    worktree_path: str,
    branch: Optional[str] = None,
) -> None:
    """Remove a worktree directory and optionally delete the branch."""
    await _run(
        ["git", "worktree", "remove", worktree_path, "--force"],
        cwd=repo_path,
    )
    if branch:
        await _run(["git", "branch", "-D", branch], cwd=repo_path)
