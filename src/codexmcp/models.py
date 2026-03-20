from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    FULL_ACCESS = "full-access"


class TaskMode(str, Enum):
    BLOCKING = "blocking"
    DISPATCH = "dispatch"


class TaskStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class EventType(str, Enum):
    THINKING = "thinking"
    TEXT = "text"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    COMMAND = "command"
    ERROR = "error"


class TaskMeta(BaseModel):
    """Persistent task metadata, serialised to ``~/.codexmcp/tasks/<id>/meta.json``."""

    task_id: str
    mode: TaskMode
    prompt: str
    cwd: str
    sandbox: SandboxMode = SandboxMode.READ_ONLY
    topic: str
    tmux_session: str
    log_file: str
    prompt_file: str
    start_time: str  # ISO-8601

    status: TaskStatus = TaskStatus.RUNNING

    # Worktree isolation (None when not used)
    worktree_dir: Optional[str] = None
    agent_branch: Optional[str] = None
    base_branch: Optional[str] = None

    # Codex session (populated after completion, used for resume)
    session_id: Optional[str] = None

    # Completion fields (populated after task finishes)
    end_time: Optional[str] = None
    exit_code: Optional[int] = None
    result: Optional[str] = None


@dataclass
class TaskEvent:
    timestamp: datetime
    type: EventType
    text: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[str] = None


@dataclass
class TaskUsage:
    input_tokens: int
    output_tokens: int
    cached_input_tokens: Optional[int] = None
