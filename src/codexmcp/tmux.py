"""tmux session management for persistent codex task execution."""

from __future__ import annotations

import asyncio
import shutil
from typing import Optional


async def _run(
    cmd: list[str], check: bool = False
) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    out = stdout.decode("utf-8", errors="replace")
    err = stderr.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{err}"
        )
    return proc.returncode, out, err


def available() -> bool:
    """Check if tmux is installed and on PATH."""
    return shutil.which("tmux") is not None


async def create_session(name: str, shell_command: str) -> None:
    """Create a detached tmux session that runs *shell_command*.

    The command is executed via ``bash -c`` inside the tmux session so that
    shell features (pipes, redirects, ``&&``) work as expected.
    """
    await _run(
        [
            "tmux",
            "new-session",
            "-d",
            "-s",
            name,
            "bash",
            "-c",
            shell_command,
        ],
        check=True,
    )


async def session_exists(name: str) -> bool:
    """Return True if a tmux session with *name* is alive."""
    rc, _, _ = await _run(["tmux", "has-session", "-t", name])
    return rc == 0


async def kill_session(name: str) -> bool:
    """Kill a tmux session.  Returns True if killed, False if not found."""
    rc, _, _ = await _run(["tmux", "kill-session", "-t", name])
    return rc == 0


async def list_sessions(prefix: str = "codex-") -> list[str]:
    """List tmux session names that start with *prefix*."""
    rc, stdout, _ = await _run(["tmux", "ls", "-F", "#{session_name}"])
    if rc != 0:
        return []
    return [
        line.strip()
        for line in stdout.splitlines()
        if line.strip().startswith(prefix)
    ]


async def capture_pane(name: str, lines: int = 50) -> Optional[str]:
    """Capture the last *lines* of visible output from the session's pane."""
    rc, stdout, _ = await _run(
        ["tmux", "capture-pane", "-t", name, "-p", "-S", f"-{lines}"]
    )
    if rc != 0:
        return None
    return stdout
