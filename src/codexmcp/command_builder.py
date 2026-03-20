from __future__ import annotations

import os
import shutil

_SANDBOX_MAP = {
    "full-access": "danger-full-access",
    "read-only": "read-only",
}


def build_codex_command(
    cwd: str,
    sandbox: str,
    *,
    session_id: str = "",
) -> list[str]:
    """Build the ``codex exec`` CLI command as a list of arguments.

    The prompt is **not** included — it is piped via stdin from a file
    by the caller (typically the tmux shell command).

    Environment variables ``CODEX_PROFILE`` and ``CODEX_REASONING_EFFORT``
    are read at call time and forwarded to the CLI when set.
    """
    codex_path = shutil.which("codex") or "codex"
    cli_sandbox = _SANDBOX_MAP.get(sandbox, sandbox)
    cmd = [codex_path, "exec", "--sandbox", cli_sandbox, "--cd", cwd, "--json"]

    profile = os.environ.get("CODEX_PROFILE", "")
    if profile:
        cmd.extend(["--profile", profile])

    effort = os.environ.get("CODEX_REASONING_EFFORT", "")
    if effort:
        cmd.extend(["--config", f'model_reasoning_effort="{effort}"'])

    cmd.append("--skip-git-repo-check")

    if session_id:
        cmd.extend(["resume", session_id, "-"])

    return cmd
