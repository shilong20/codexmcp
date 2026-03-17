from __future__ import annotations

import json
from datetime import datetime
from typing import Optional

from .models import EventType, TaskEvent, TaskUsage


class StreamProcessor:
    """Parse codex exec --json JSONL output into TaskEvent objects."""

    NON_JSON_BUFFER_LINES = 20

    def __init__(self) -> None:
        self._agent_messages: list[str] = []
        self._non_json_lines: list[str] = []
        self._session_id: Optional[str] = None
        self._usage: Optional[TaskUsage] = None
        self._done = False

    @property
    def session_id(self) -> Optional[str]:
        return self._session_id

    @property
    def usage(self) -> Optional[TaskUsage]:
        return self._usage

    @property
    def is_done(self) -> bool:
        return self._done

    @property
    def result_text(self) -> str:
        return "".join(self._agent_messages)

    @property
    def diagnostic_text(self) -> str:
        return "\n".join(self._non_json_lines)

    def process_line(self, line: str) -> Optional[TaskEvent]:
        """Parse one JSONL line, returning a TaskEvent or None."""
        stripped = line.strip()
        if not stripped:
            return None

        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            self._remember_non_json_line(stripped)
            return None

        event_type = data.get("type", "")

        if event_type == "thread.started":
            self._session_id = data.get("thread_id")
            return None

        if event_type == "turn.completed":
            self._extract_usage(data.get("usage"))
            self._done = True
            return None

        if event_type in ("item.completed", "item.started"):
            return self._process_item(data.get("item", {}))

        if "error" in event_type or "fail" in event_type:
            msg = data.get("message", "") or data.get("error", {}).get(
                "message", ""
            )
            if msg:
                return TaskEvent(
                    timestamp=datetime.now(),
                    type=EventType.ERROR,
                    text=str(msg)[:500],
                )

        return None

    def _process_item(self, item: dict) -> Optional[TaskEvent]:
        item_type = item.get("type", "")
        now = datetime.now()

        if item_type == "agent_message":
            text = item.get("text", "")
            self._agent_messages.append(text)
            return TaskEvent(timestamp=now, type=EventType.TEXT, text=text[:500])

        if item_type == "reasoning":
            return TaskEvent(
                timestamp=now,
                type=EventType.THINKING,
                text=item.get("text", "")[:300],
            )

        if item_type == "command_execution":
            cmd = item.get("command", "")
            exit_code = item.get("exit_code")
            suffix = f" (exit={exit_code})" if exit_code is not None else ""
            return TaskEvent(
                timestamp=now,
                type=EventType.COMMAND,
                text=f"{cmd}{suffix}"[:500],
            )

        if item_type == "function_call":
            return TaskEvent(
                timestamp=now,
                type=EventType.TOOL_CALL,
                tool_name=item.get("name", "unknown"),
                tool_input=str(item.get("arguments", ""))[:100],
            )

        if item_type == "function_call_output":
            return TaskEvent(
                timestamp=now,
                type=EventType.TOOL_RESULT,
                text=str(item.get("output", ""))[:300],
            )

        return None

    def _extract_usage(self, usage: Optional[dict]) -> None:
        if not usage or not isinstance(usage, dict):
            return
        inp = usage.get("input_tokens")
        out = usage.get("output_tokens")
        if isinstance(inp, int) and isinstance(out, int):
            self._usage = TaskUsage(
                input_tokens=inp,
                output_tokens=out,
                cached_input_tokens=usage.get("cached_input_tokens"),
            )

    def _remember_non_json_line(self, line: str) -> None:
        self._non_json_lines.append(line[:500])
        if len(self._non_json_lines) > self.NON_JSON_BUFFER_LINES:
            self._non_json_lines = self._non_json_lines[-self.NON_JSON_BUFFER_LINES :]
