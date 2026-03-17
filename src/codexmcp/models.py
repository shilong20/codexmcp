from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


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


@dataclass
class TaskInfo:
    task_id: str
    prompt: str
    cwd: str
    sandbox: str
    start_time: datetime
    status: TaskStatus = TaskStatus.RUNNING
    events: list[TaskEvent] = field(default_factory=list)
    event_base_index: int = 0
    result: Optional[str] = None
    exit_code: Optional[int] = None
    end_time: Optional[datetime] = None
    usage: Optional[TaskUsage] = None
    session_id: Optional[str] = None
    profile: Optional[str] = None
    reasoning_effort: Optional[str] = None
    images: list[str] = field(default_factory=list)
    _process: object = field(default=None, repr=False)
    _reader_task: object = field(default=None, repr=False)
