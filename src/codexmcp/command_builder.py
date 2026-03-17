from __future__ import annotations

import shutil


def build_codex_command(
    prompt: str,
    cwd: str,
    sandbox: str = "read-only",
    *,
    session_id: str = "",
    skip_git_repo_check: bool = True,
    images: list[str] | None = None,
    profile: str = "",
    model_reasoning_effort: str = "",
    dangerously_bypass: bool = False,
) -> list[str]:
    """Build the codex exec CLI command as a list of arguments."""
    codex_path = shutil.which("codex") or "codex"
    cmd = [codex_path, "exec", "--sandbox", sandbox, "--cd", cwd, "--json"]

    for img in images or []:
        cmd.extend(["--image", img])
    if profile:
        cmd.extend(["--profile", profile])
    if model_reasoning_effort:
        cmd.extend(
            [
                "--config",
                f'model_reasoning_effort="{model_reasoning_effort}"',
            ]
        )
    if dangerously_bypass:
        cmd.append("--dangerously-bypass-approvals-and-sandbox")
    if skip_git_repo_check:
        cmd.append("--skip-git-repo-check")
    if session_id:
        cmd.extend(["resume", session_id])

    cmd += ["--", prompt]
    return cmd
