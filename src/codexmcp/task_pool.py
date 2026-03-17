from __future__ import annotations

import asyncio
import os
import signal
import time
from datetime import datetime
from typing import Any, Optional

from .command_builder import build_codex_command
from .models import EventType, TaskEvent, TaskInfo, TaskStatus
from .stream_processor import StreamProcessor


class TaskPool:
    MAX_CONCURRENT = 5
    MAX_EVENTS_PER_TASK = 100
    RESULT_TTL_S = 600
    MAX_HISTORY = 50
    KILL_GRACE_S = 5

    def __init__(self) -> None:
        self._running: dict[str, TaskInfo] = {}
        self._completed: dict[str, TaskInfo] = {}
        self._history: list[dict[str, Any]] = []
        self._ttl_handles: dict[str, asyncio.TimerHandle] = {}
        self._kill_handles: dict[str, asyncio.TimerHandle] = {}
        self._start_lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(self.MAX_CONCURRENT)
        self._slot_holders: set[str] = set()
        self._disposed = False

    async def start(
        self,
        prompt: str,
        cwd: str,
        sandbox: str = "read-only",
        *,
        session_id: str = "",
        skip_git_repo_check: bool = True,
        images: Optional[list[str]] = None,
        profile: str = "",
        model_reasoning_effort: str = "",
        dangerously_bypass: bool = False,
    ) -> str:
        """Start a codex subprocess and return the task_id."""
        task_id = f"task_{int(time.time())}_{os.urandom(4).hex()}"
        task = TaskInfo(
            task_id=task_id,
            prompt=prompt,
            cwd=cwd,
            sandbox=sandbox,
            start_time=datetime.now(),
            profile=profile or None,
            reasoning_effort=model_reasoning_effort or None,
            images=images or [],
        )

        cmd = build_codex_command(
            prompt,
            cwd,
            sandbox,
            session_id=session_id,
            skip_git_repo_check=skip_git_repo_check,
            images=images,
            profile=profile,
            model_reasoning_effort=model_reasoning_effort,
            dangerously_bypass=dangerously_bypass,
        )

        async with self._start_lock:
            if self._disposed:
                raise RuntimeError("Task pool has been disposed")
            if len(self._running) >= self.MAX_CONCURRENT:
                raise RuntimeError(
                    f"Max concurrent tasks reached ({self.MAX_CONCURRENT})"
                )
            await self._slots.acquire()
            self._slot_holders.add(task_id)
            self._running[task_id] = task

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
        except BaseException:
            self._rollback_pending_task(task_id)
            raise

        task._process = proc
        task._reader_task = asyncio.create_task(self._read_stdout(task))

        return task_id

    def get_task(self, task_id: str) -> Optional[TaskInfo]:
        return self._running.get(task_id) or self._completed.get(task_id)

    def get_events(
        self, task_id: str, since: Optional[int] = None
    ) -> list[TaskEvent]:
        task = self.get_task(task_id)
        if not task:
            return []
        if since is not None:
            adjusted = since - task.event_base_index
            if adjusted < 0:
                return list(task.events)
            return task.events[adjusted:]
        return list(task.events)

    async def cancel(self, task_id: str) -> bool:
        task = self._running.get(task_id)
        if not task or task.status != TaskStatus.RUNNING:
            return False
        task.status = TaskStatus.CANCELLED
        self._kill_process(task)
        return True

    def list_running(self) -> list[dict[str, Any]]:
        return [
            self._to_summary(t)
            for t in self._running.values()
            if t.status == TaskStatus.RUNNING
        ]

    def list_all(self) -> list[dict[str, Any]]:
        result = [self._to_summary(t) for t in self._running.values()]
        result.extend(self._to_summary(t) for t in self._completed.values())
        return result

    def get_history_entry(self, task_id: str) -> Optional[dict[str, Any]]:
        return next(
            (h for h in self._history if h.get("task_id") == task_id), None
        )

    async def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True

        for h in self._ttl_handles.values():
            h.cancel()
        self._ttl_handles.clear()
        for h in self._kill_handles.values():
            h.cancel()
        self._kill_handles.clear()

        running_tasks = list(self._running.values())
        reader_tasks: list[asyncio.Task[Any]] = []
        processes: list[asyncio.subprocess.Process] = []

        for task in running_tasks:
            self._kill_process(task)
            reader = task._reader_task
            if isinstance(reader, asyncio.Task):
                reader.cancel()
                reader_tasks.append(reader)
            proc = task._process
            if isinstance(proc, asyncio.subprocess.Process):
                processes.append(proc)

        if reader_tasks:
            await asyncio.gather(*reader_tasks, return_exceptions=True)

        if processes:
            waits = [
                asyncio.wait_for(proc.wait(), timeout=self.KILL_GRACE_S + 1)
                for proc in processes
                if proc.returncode is None
            ]
            if waits:
                await asyncio.gather(*waits, return_exceptions=True)

        for task in running_tasks:
            proc = task._process
            if (
                isinstance(proc, asyncio.subprocess.Process)
                and proc.returncode is None
            ):
                self._force_kill(task)

        final_waits = [
            asyncio.wait_for(proc.wait(), timeout=1)
            for proc in processes
            if proc.returncode is None
        ]
        if final_waits:
            await asyncio.gather(*final_waits, return_exceptions=True)

        for task in running_tasks:
            self._release_slot(task.task_id)

        self._running.clear()
        self._completed.clear()

    # --- private ---

    async def _read_stdout(self, task: TaskInfo) -> None:
        proc: asyncio.subprocess.Process = task._process  # type: ignore[assignment]
        sp = StreamProcessor()

        try:
            async for raw_line in proc.stdout:  # type: ignore[union-attr]
                line = raw_line.decode("utf-8", errors="replace")

                event = sp.process_line(line)
                if event:
                    self._append_event(task, event)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            if task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.FAILED
            self._append_event(
                task,
                TaskEvent(
                    timestamp=datetime.now(),
                    type=EventType.ERROR,
                    text=f"reader failed: {type(exc).__name__}: {exc}"[:500],
                ),
            )

        await proc.wait()
        self._on_process_complete(task, sp, proc.returncode or 0)

    def _on_process_complete(
        self, task: TaskInfo, sp: StreamProcessor, exit_code: int
    ) -> None:
        if task.end_time:
            return

        self._clear_kill_handle(task.task_id)

        task.exit_code = exit_code
        task.result = sp.result_text or sp.diagnostic_text or "No output"
        task.session_id = sp.session_id
        task.usage = sp.usage

        if task.status == TaskStatus.RUNNING:
            task.status = (
                TaskStatus.COMPLETED if exit_code == 0 else TaskStatus.FAILED
            )

        task.end_time = datetime.now()
        if self._disposed:
            self._running.pop(task.task_id, None)
            self._release_slot(task.task_id)
            return
        self._move_to_completed(task)

    def _kill_process(self, task: TaskInfo) -> None:
        proc: asyncio.subprocess.Process = task._process  # type: ignore[assignment]
        if not proc or proc.returncode is not None:
            return
        try:
            if hasattr(os, "killpg") and proc.pid:
                os.killpg(proc.pid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, PermissionError):
            return

        loop = asyncio.get_running_loop()
        handle = loop.call_later(self.KILL_GRACE_S, self._force_kill, task)
        self._kill_handles[task.task_id] = handle

    def _force_kill(self, task: TaskInfo) -> None:
        proc: asyncio.subprocess.Process = task._process  # type: ignore[assignment]
        if not proc or proc.returncode is not None:
            return
        try:
            if hasattr(os, "killpg") and proc.pid:
                os.killpg(proc.pid, signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError):
            pass

    def _move_to_completed(self, task: TaskInfo) -> None:
        self._running.pop(task.task_id, None)
        self._release_slot(task.task_id)
        self._completed[task.task_id] = task

        old_handle = self._ttl_handles.pop(task.task_id, None)
        if old_handle:
            old_handle.cancel()

        loop = asyncio.get_running_loop()
        handle = loop.call_later(
            self.RESULT_TTL_S, self._demote_to_history, task.task_id
        )
        self._ttl_handles[task.task_id] = handle

    def _demote_to_history(self, task_id: str) -> None:
        task = self._completed.pop(task_id, None)
        if not task:
            return
        self._ttl_handles.pop(task_id, None)
        summary = self._to_summary(task)
        if task.result:
            summary["result_preview"] = task.result[:200]
        self._history.append(summary)
        if len(self._history) > self.MAX_HISTORY:
            self._history = self._history[-self.MAX_HISTORY :]

    def _trim_events(self, task: TaskInfo) -> None:
        if len(task.events) > self.MAX_EVENTS_PER_TASK:
            excess = len(task.events) - self.MAX_EVENTS_PER_TASK
            del task.events[:excess]
            task.event_base_index += excess

    def _append_event(self, task: TaskInfo, event: TaskEvent) -> None:
        task.events.append(event)
        self._trim_events(task)

    def _release_slot(self, task_id: str) -> None:
        if task_id in self._slot_holders:
            self._slot_holders.remove(task_id)
            self._slots.release()

    def _rollback_pending_task(self, task_id: str) -> None:
        self._running.pop(task_id, None)
        self._release_slot(task_id)

    def _clear_kill_handle(self, task_id: str) -> None:
        handle = self._kill_handles.pop(task_id, None)
        if handle:
            handle.cancel()

    def _to_summary(self, task: TaskInfo) -> dict[str, Any]:
        elapsed = (task.end_time or datetime.now()) - task.start_time
        d: dict[str, Any] = {
            "task_id": task.task_id,
            "status": task.status.value,
            "prompt": task.prompt[:200],
            "elapsed_ms": int(elapsed.total_seconds() * 1000),
            "event_count": len(task.events) + task.event_base_index,
        }
        if task.exit_code is not None:
            d["exit_code"] = task.exit_code
        if task.end_time:
            d["completed_at"] = task.end_time.isoformat()
        return d
