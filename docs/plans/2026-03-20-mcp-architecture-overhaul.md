# CodexMCP 架构重构
> 深度: Deep

## 背景

当前 CodexMCP 存在以下问题：

1. **异步模式在 Cursor/Windsurf 上表现不佳** — agent 只能不停 sleep/轮询 check 状态，无法实现"codex 后台跑 + 前台干别的"
2. **缺少真正的长期任务能力** — 无法分派持续数十分钟甚至数小时的任务
3. **进程生命周期脆弱** — 内存中的 `asyncio.create_subprocess_exec` 受 MCP 连接断开影响，窗口关闭即任务终止
4. **缺少并行隔离** — 多个 codex 任务操作同一工作区会冲突

## 参考

- `src/codexmcp/task_pool.py` — 旧的内存进程池，将被替代
- `src/codexmcp/task_manager.py` — 已部分实现的新任务管理器（tmux + worktree + 文件系统持久化）
- `src/codexmcp/tmux.py` — 已实现的 tmux 会话管理
- `src/codexmcp/worktree.py` — 已实现的 git worktree 辅助
- `skills/codex-dispatch/SKILL.md` — 现有 skill，提供了 worktree + tmux 的编排模式参考

## 设计决策

### 1. 统一执行引擎：tmux

所有任务（阻塞和异步）都通过 tmux session 执行。

- 进程持久化：MCP 连接断开后 codex 继续运行
- 可观测性：`tmux attach` 可直接查看运行中的任务
- 统一管理：`tmux ls` 查看所有 codex 会话

不做降级。tmux 和 git 作为前置条件，不满足时报错并引导安装。

### 2. MCP 工具集（4 个工具）

| 工具 | 模式 | 用途 |
|---|---|---|
| `codex` | 阻塞 | 审阅、短并行任务。等完返回结果 |
| `codex_dispatch` | 异步 | 长任务后台执行。立即返回 task_id |
| `codex_status` | 查询 | task_id 可选。有则查单个详情，无则列出所有任务 |
| `codex_cancel` | 操作 | 取消运行中的任务 |

### 3. 参数设计（codex 和 codex_dispatch 共享）

| 参数 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `prompt` | str | 是 | 任务指令 |
| `cwd` | Path | 是 | 工作目录 |
| `topic` | str | 是 | 任务标识，用于 tmux session / worktree / task_id |
| `sandbox` | `read-only` / `full-access` | 是 | 权限模式，决定是否创建 worktree |
| `session_id` | str | 否 | resume 已完成任务的 codex 会话 |

**精简理由：**
- `profile`、`model_reasoning_effort` 改为环境变量 `CODEX_PROFILE`、`CODEX_REASONING_EFFORT`
- `image` 去掉，codex 在执行时可自行读取工作区内的图片
- `dangerously_bypass` 去掉，由 sandbox 级别隐含
- `skip_git_repo_check` 去掉，总是跳过
- `return_all_messages` 去掉，不再暴露实时事件流

### 4. Sandbox 与 Worktree 绑定

| sandbox | worktree | 场景 |
|---|---|---|
| `read-only` | 不创建 | 代码审阅、分析、只读任务 |
| `full-access` | 创建（需 git repo） | 代码编写、调试、重构 |

非 git 目录下 full-access 不创建 worktree，直接在 cwd 执行并报 warning。

`full-access` 映射到 codex CLI 的 `danger-full-access` sandbox 模式。

### 5. 执行流程

```
codex（阻塞）:
  start_task() → tmux session 启动 codex
  → wait_for_completion() 轮询 tmux + 日志
  → 解析日志（StreamProcessor）
  → 返回结果

codex_dispatch（异步）:
  start_task() → tmux session 启动 codex
  → 立即返回 task_id
  → 用户通过 codex_status 查状态 / codex_cancel 取消
```

### 6. 日志与持久化

**双位置策略：**
- 主存储：`~/.codexmcp/tasks/<task_id>/`（meta.json + codex-exec.log + prompt.md）
- 工作区链接：`<cwd>/.codex-tasks/<topic>` → symlink 到主存储目录

用户在 IDE 中可直接点击 `.codex-tasks/<topic>/codex-exec.log` 查看日志。

**Shell 命令使用 `tee`：**
```bash
cd <cwd> && codex exec ... < prompt.md 2>&1 | tee codex-exec.log; echo EXIT_CODE=$? >> codex-exec.log
```

同时输出到 tmux pane（可 attach 查看）和日志文件（可解析进度）。

### 7. 进度反馈

运行中任务通过 `codex_status` 查询：
- 读日志尾部最近 N 行
- 用 StreamProcessor 解析最近的 JSON 事件，提取结构化进度（当前步骤、工具调用等）

已完成任务通过完整日志解析提取 result、session_id、usage。

### 8. 返回结构

阻塞 `codex` 完成后返回 / `codex_status` 查询已完成任务时：

```json
{
    "success": true,
    "task_id": "codex-<topic>",
    "session_id": "thread_xxx",
    "result": "codex 的最终回复文本",
    "exit_code": 0,
    "elapsed_seconds": 45.2,
    "usage": {"input_tokens": 5000, "output_tokens": 1200},
    "worktree_dir": "/path/to/worktree",
    "agent_branch": "agent/<topic>",
    "base_branch": "main",
    "diff_stat": "3 files changed, 42 insertions(+), 10 deletions(-)",
    "commits_ahead": 2
}
```

worktree 相关字段仅在 full-access 模式下出现。

### 9. Resume 支持

`session_id` 参数用于恢复 codex 会话。流程：
1. 任务完成后返回 `session_id`
2. 调用方拿 `session_id` 再次调用 `codex`/`codex_dispatch`，配合新的 prompt
3. 内部执行 `codex exec resume <session_id>`

主要场景：审阅后修改代码，再让同一个 codex 会话继续审阅，避免重读上下文。

### 10. 环境变量配置

| 变量 | 说明 | 示例 |
|---|---|---|
| `CODEX_PROFILE` | codex 配置文件名 | `fast`、`strong` |
| `CODEX_REASONING_EFFORT` | 推理强度 | `high`、`xhigh` |

读取自 MCP 服务进程的环境变量。未设置时使用 codex 默认配置。

### 11. Skill 设计

一个统一的 `codexmcp-using` skill，主文档 + 场景子文档：

```
skills/codexmcp-using/
├── SKILL.md              # 主文档：工具概览、参数、返回值、前置条件
├── scenarios/
│   ├── review.md         # 场景 1：代码审阅（阻塞 + read-only + resume）
│   ├── parallel.md       # 场景 2：并行模块实现（多阻塞 + full-access + 合并）
│   └── dispatch.md       # 场景 3：长任务后台分派（异步 + full-access + 状态查询）
└── troubleshoot.md       # 故障排查：tmux/git 安装、常见错误
```

Skill 文件写在项目内部，需要时通过插件管理器安装到 `~/.cursor/skills/`。

### 12. 文件变动清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `task_pool.py` | 删除 | task_manager.py 完全替代 |
| `server.py` | 重写 | 4 个新工具，接入 task_manager |
| `task_manager.py` | 调整 | topic 必填、sandbox 两档、tee 日志、symlink |
| `models.py` | 微调 | TaskMode 调整、清理不需要的字段 |
| `command_builder.py` | 微调 | sandbox 映射、环境变量读取、去掉多余参数 |
| `tmux.py` | 保持 | 无需改动 |
| `worktree.py` | 保持 | 无需改动 |
| `stream_processor.py` | 保持 | 无需改动 |
| skill 文件 | 新建 | codexmcp-using skill 全套文档 |

---

## 实现计划

### Phase 0: 清理模型层

**涉及文件：**
- 修改: `src/codexmcp/models.py`（简化 TaskMeta、调整枚举）
- 删除: `src/codexmcp/task_pool.py`

**关键改动：**

`models.py`:

```python
class SandboxMode(str, Enum):
    READ_ONLY = "read-only"
    FULL_ACCESS = "full-access"

class TaskMode(str, Enum):
    BLOCKING = "blocking"
    DISPATCH = "dispatch"
```

TaskMeta 字段调整：
- `sandbox` 类型从 `str` 改为 `SandboxMode`
- `topic` 从 `Optional[str]` 改为 `str`（必填）
- 删除：`profile`、`reasoning_effort`、`images` 字段（已移至环境变量或删除）

删除 `task_pool.py`：直接 `git rm`。

注意：删除 `task_pool.py` 后，`server.py` 的 import 会报错。这是预期行为 — Phase 3 会重写 `server.py`。中间态项目不可运行。

**验证：**
在 `/workspace/Tools/codexmcp` 执行 `python -c "from codexmcp.models import TaskMeta, SandboxMode, TaskMode, TaskStatus; print('OK')"`，预期输出 `OK`。

### Phase 1: 调整命令构建

**涉及文件：**
- 修改: `src/codexmcp/command_builder.py`（精简参数、添加环境变量支持、sandbox 映射）

**关键改动：**

新签名：

```python
def build_codex_command(
    cwd: str,
    sandbox: str,
    *,
    session_id: str = "",
) -> list[str]:
```

关键逻辑：
- sandbox 映射：`"full-access"` → CLI 参数 `"danger-full-access"`，`"read-only"` 不变
- 环境变量读取：`os.environ.get("CODEX_PROFILE")` → `--profile`；`os.environ.get("CODEX_REASONING_EFFORT")` → `--config model_reasoning_effort="..."`
- 删除：`images`、`profile`、`model_reasoning_effort`、`dangerously_bypass`、`skip_git_repo_check` 参数
- `--skip-git-repo-check` 总是添加
- prompt 仍不包含（stdin 传入）

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
python -c "
from codexmcp.command_builder import build_codex_command
cmd = build_codex_command('/tmp', 'full-access')
assert 'danger-full-access' in cmd, f'sandbox mapping failed: {cmd}'
assert '--skip-git-repo-check' in cmd, f'skip-git missing: {cmd}'
assert '--json' in cmd, f'json flag missing: {cmd}'
print('OK:', cmd)
"
```
预期输出包含 `OK:` 和正确的命令列表。

### Phase 2: 重构任务管理器

**涉及文件：**
- 修改: `src/codexmcp/task_manager.py`（适配新参数设计、tee 日志、symlink、进度解析）

**关键改动：**

`start_task` 签名精简：

```python
async def start_task(
    prompt: str,
    cwd: str,
    topic: str,
    sandbox: SandboxMode,
    *,
    mode: TaskMode = TaskMode.BLOCKING,
    session_id: str = "",
) -> TaskMeta:
```

Shell 命令改为 tee（用 `set -o pipefail` 确保 codex 的退出码不被 tee 覆盖）：
```python
shell_cmd = (
    f"set -o pipefail; cd {shlex.quote(effective_cwd)} && "
    f"{codex_cmd_str} < {shlex.quote(prompt_file)} "
    f"2>&1 | tee {shlex.quote(log_file)}; "
    f"echo 'EXIT_CODE='${{PIPESTATUS[0]}} >> {shlex.quote(log_file)}"
)
```

Symlink 创建（在 start_task 中）：
```python
link_dir = Path(cwd) / ".codex-tasks"
link_dir.mkdir(exist_ok=True)
link_path = link_dir / topic
if link_path.is_symlink() or link_path.exists():
    link_path.unlink()
link_path.symlink_to(task_dir)
```

Worktree 策略调整：
```python
needs_worktree = (
    sandbox == SandboxMode.FULL_ACCESS
    and await worktree.is_git_repo(cwd)
)
if sandbox == SandboxMode.FULL_ACCESS and not needs_worktree:
    # 非 git 目录，无法创建 worktree，返回 warning
    ...
```

前置条件检查（在 `start_task` 入口）：
```python
if not tmux.available():
    raise RuntimeError(
        "tmux is required but not found. Install: apt install tmux / brew install tmux"
    )
if sandbox == SandboxMode.FULL_ACCESS and not worktree.git_available():
    raise RuntimeError(
        "git is required for full-access mode. Install: apt install git / brew install git"
    )
```

.gitignore 自动追加（在 symlink 创建时检查）：
```python
gitignore = Path(cwd) / ".gitignore"
marker = ".codex-tasks/"
if gitignore.exists():
    content = gitignore.read_text()
    if marker not in content:
        with gitignore.open("a") as f:
            f.write(f"\n# CodexMCP task logs\n{marker}\n")
```

新增运行中进度解析函数（供 `codex_status` 使用）：
```python
def get_running_progress(log_file: str, recent_lines: int = 30) -> list[dict]:
    """Parse recent log lines into structured progress events."""
    sp = StreamProcessor()
    events: list[dict] = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        for line in all_lines[-recent_lines:]:
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
```

`_generate_task_id` 签名改为 topic 必填：
```python
def _generate_task_id(topic: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9_-]", "-", topic)
    return f"codex-{safe}"
```

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
python -c "
from codexmcp.task_manager import _generate_task_id, TASKS_ROOT
import inspect

# 验证 topic 必填
sig = inspect.signature(_generate_task_id)
params = list(sig.parameters.keys())
assert params == ['topic'], f'unexpected params: {params}'

# 验证生成逻辑
tid = _generate_task_id('test-review')
assert tid == 'codex-test-review', f'unexpected: {tid}'
print('OK:', tid)
"
```
预期输出 `OK: codex-test-review`。

### Phase 3: 重写 MCP 工具层

**涉及文件：**
- 修改: `src/codexmcp/server.py`（4 个新工具，接入 task_manager）

**关键改动：**

删除所有旧工具和 `TaskPool` 引用。重写为 4 个工具：

**codex（阻塞）：**
```python
@mcp.tool(name="codex", description=(
    "Execute a Codex task and block until completion. "
    "Use read-only sandbox for reviews/analysis, full-access for code modifications. "
    "full-access mode creates a git worktree for isolation. "
    "Returns the final result, session_id (for resume), and git diff stats."
))
async def codex(
    prompt: Annotated[str, "任务指令"],
    cwd: Annotated[Path, "工作目录"],
    topic: Annotated[str, "任务标识"],
    sandbox: Annotated[Literal["read-only", "full-access"], "权限模式"],
    session_id: Annotated[str, "Resume 已有会话"] = "",
) -> Dict[str, Any]:
    meta = await task_manager.start_task(
        prompt, str(cwd), topic, SandboxMode(sandbox),
        mode=TaskMode.BLOCKING, session_id=session_id,
    )
    meta = await task_manager.wait_for_completion(meta.task_id)
    return _build_result(meta)
```

**codex_dispatch（异步）：**
```python
@mcp.tool(name="codex_dispatch", description=(
    "Dispatch a long-running Codex task to background and return immediately. "
    "The task runs in a persistent tmux session that survives disconnects. "
    "Use codex_status to check progress and codex_cancel to stop."
))
async def codex_dispatch(
    prompt: Annotated[str, "任务指令"],
    cwd: Annotated[Path, "工作目录"],
    topic: Annotated[str, "任务标识"],
    sandbox: Annotated[Literal["read-only", "full-access"], "权限模式"],
    session_id: Annotated[str, "Resume 已有会话"] = "",
) -> Dict[str, Any]:
    meta = await task_manager.start_task(
        prompt, str(cwd), topic, SandboxMode(sandbox),
        mode=TaskMode.DISPATCH, session_id=session_id,
    )
    return {"task_id": meta.task_id, "status": "running", ...}
```

**codex_status（查询）：**
```python
@mcp.tool(name="codex_status", description=(
    "Check task status. Pass task_id for single task detail (with progress events "
    "if running, or result/diff if completed). Omit task_id to list all tasks."
))
async def codex_status(
    task_id: Annotated[str, "任务 ID。不传则列出所有任务"] = "",
) -> Dict[str, Any]:
    if task_id:
        return await task_manager.get_task_status_detail(task_id)
    tasks = task_manager.list_tasks()
    # resolve status for running tasks, return summary list
```

**codex_cancel（取消）：**
```python
@mcp.tool(name="codex_cancel", description="Cancel a running Codex task by killing its tmux session.")
async def codex_cancel(
    task_id: Annotated[str, "要取消的任务 ID"],
) -> Dict[str, Any]:
    meta = await task_manager.cancel_task(task_id)
    return {"task_id": task_id, "status": meta.status.value}
```

辅助函数 `_build_result`：
```python
def _build_result(meta: TaskMeta) -> Dict[str, Any]:
    """Build the standard result dict from completed task metadata."""
    result_text, session_id, usage = task_manager._parse_log(meta.log_file)
    elapsed = _calc_elapsed(meta)
    resp: Dict[str, Any] = {
        "success": meta.status == TaskStatus.COMPLETED,
        "task_id": meta.task_id,
        "session_id": session_id or meta.session_id,
        "result": result_text or meta.result,
        "exit_code": meta.exit_code,
        "elapsed_seconds": elapsed,
    }
    if usage:
        resp["usage"] = {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
        }
    if meta.worktree_dir:
        resp["worktree_dir"] = meta.worktree_dir
        resp["agent_branch"] = meta.agent_branch
        resp["base_branch"] = meta.base_branch
        # fetch git stats
        ...
    return resp
```

`run()` 函数去掉 `asyncio.run(_pool.dispose())`（不再需要清理内存进程池）。

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
python -c "
from codexmcp.server import mcp
# 验证 server 模块可导入且未引用已删除的 task_pool
import codexmcp.server as s
import inspect
source = inspect.getsource(s)
assert 'TaskPool' not in source, 'TaskPool reference still present'
assert 'task_pool' not in source, 'task_pool import still present'
print('OK: server module clean, no TaskPool references')
"
```
预期输出 `OK: server module clean, no TaskPool references`。

### Phase 4: 创建 Skill 文档

**涉及文件：**
- 新增: `skills/codexmcp-using/SKILL.md`（主文档）
- 新增: `skills/codexmcp-using/scenarios/review.md`（代码审阅场景）
- 新增: `skills/codexmcp-using/scenarios/parallel.md`（并行模块实现场景）
- 新增: `skills/codexmcp-using/scenarios/dispatch.md`（长任务后台分派场景）
- 新增: `skills/codexmcp-using/troubleshoot.md`（故障排查）

**关键改动：**

`SKILL.md` 主文档结构：
```markdown
---
name: codexmcp-using
description: "调用 CodexMCP 执行 AI 辅助编码任务..."
---

# CodexMCP 使用指南

## 前置条件
- tmux 已安装
- git 已安装（full-access 模式需要）

## 工具速查
（4 个工具的参数和返回值）

## 环境变量配置
（CODEX_PROFILE, CODEX_REASONING_EFFORT）

## 使用场景
按场景引用子文档：
- 代码审阅 → scenarios/review.md
- 并行模块实现 → scenarios/parallel.md
- 长任务后台分派 → scenarios/dispatch.md

## 故障排查
→ troubleshoot.md
```

各场景子文档包含：
- 适用条件
- 调用编排步骤（含示例参数）
- 结果处理（合并、追加审阅等）
- 注意事项

troubleshoot.md 包含：
- tmux 安装引导（apt/brew/yum）
- git 安装引导
- 常见错误及解决方案

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
ls skills/codexmcp-using/SKILL.md skills/codexmcp-using/scenarios/*.md skills/codexmcp-using/troubleshoot.md
```
预期全部文件存在。

## 状态
- [x] Phase 0: 清理模型层
- [x] Phase 1: 调整命令构建
- [x] Phase 2: 重构任务管理器
- [x] Phase 3: 重写 MCP 工具层
- [x] Phase 4: 创建 Skill 文档
- [x] 验证与审阅
