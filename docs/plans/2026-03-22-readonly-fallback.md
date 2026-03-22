# Read-Only Sandbox Fallback 机制
> 深度: Deep

## 背景

Docker 等容器环境中，codex CLI 的 `read-only` sandbox 依赖 Linux sandbox 机制（seccomp/namespace），在容器内受限无法正常工作。需要一个 fallback 机制：在这类环境中，将 `read-only` 请求实际以 `full-access` 执行，但通过强约束提示词 + 事后日志审计来保障只读语义。

## 参考

- `src/codexmcp/command_builder.py` — sandbox 映射逻辑（`_SANDBOX_MAP`），是 CLI 参数的生成点
- `src/codexmcp/task_manager.py` — `start_task()` 中根据 sandbox 决定执行路径（tmux vs 直接子进程），prompt 写入点
- `src/codexmcp/stream_processor.py` — JSONL log 解析器，可扩展审计能力
- `src/codexmcp/server.py` — 返回结果构建（`_build_result()`），需附加 audit 字段

## 设计决策

### 1. 环境变量 `CODEXMCP_READONLY_FALLBACK`

| 值 | 行为 |
|---|---|
| 未设置 / 空 | 完全不变，真正的 `read-only` sandbox |
| 非空（如 `1`） | 启用 fallback 模式 |

Fallback 模式仅影响 `read-only` 请求。`full-access` 请求的行为完全不变。

### 2. Fallback 模式执行路径

启用 fallback 时，`read-only` 请求的变化：

| 维度 | 正常模式 | Fallback 模式 |
|---|---|---|
| CLI sandbox 参数 | `read-only` | `danger-full-access` |
| 执行路径 | `_run_direct()`（无 tmux/worktree） | `_run_direct()`（无 tmux/worktree）— **不变** |
| Prompt | 原始 prompt | 约束提示词 + 原始 prompt |
| 任务完成后 | 返回结果 | 返回结果 + `readonly_audit` 字段 |

关键决策：**执行路径保持 read-only 的轻量模式**。审阅任务本质是阻塞等结果，不需要 tmux 持久化和 worktree 隔离。

### 3. 强约束提示词

硬编码在 `task_manager.py` 中，在 fallback 模式下自动 prepend 到用户 prompt 前面：

```
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
```

### 4. 日志安全审计

任务完成后，扫描 codex JSONL log 检测违规操作：

**检测源：**
- `command_execution` 事件 — 匹配危险命令关键词
- `function_call` 事件 — 匹配文件写入类工具名

**危险命令关键词（正则/子串匹配）：**
- 写入类：`sed -i`, `tee `, `> `, `>>`, `mv `, `cp `, `rm `, `chmod `, `chown `, `patch `, `install `
- 编辑器类：`vim `, `nano `, `emacs `
- Git 修改类：`git commit`, `git push`, `git checkout -b`, `git merge`, `git rebase`

**危险工具名（精确匹配）：**
- `write_file`, `edit_file`, `create_file`, `apply_patch`, `delete_file`, `rename_file`, `move_file`

**返回格式：**
```json
{
  "readonly_audit": {
    "mode": "fallback",
    "violations_detected": 2,
    "violations": [
      "command: sed -i 's/old/new/' file.py",
      "tool_call: write_file(path=src/main.py)"
    ],
    "verdict": "VIOLATION"
  }
}
```

`verdict` 为 `"CLEAN"` 或 `"VIOLATION"`。

### 5. 实现入口与判断函数

在 `command_builder.py` 中新增：

```python
def is_readonly_fallback() -> bool:
    return bool(os.environ.get("CODEXMCP_READONLY_FALLBACK", ""))
```

此函数被 `command_builder.build_codex_command()` 和 `task_manager.start_task()` 共同使用。

### 6. 文件变动清单

| 文件 | 操作 | 说明 |
|---|---|---|
| `command_builder.py` | 修改 | 新增 `is_readonly_fallback()`；fallback 时将 `read-only` 映射为 `danger-full-access` |
| `task_manager.py` | 修改 | fallback 模式下 prepend 约束提示词到 prompt；任务完成后调用审计并返回 |
| `stream_processor.py` | 修改 | 新增 `audit_readonly_violations()` 函数，扫描 log 事件检测违规 |
| `server.py` | 修改 | `_build_result()` 中在 fallback 模式下附加 `readonly_audit` 字段 |

---

> 执行模式: parallel

## 实现计划

### Module 1: command_builder — fallback 判断与 sandbox 重映射

**涉及文件：**
- 修改: `src/codexmcp/command_builder.py`

**关键改动：**

新增 `is_readonly_fallback()` 公开函数：

```python
def is_readonly_fallback() -> bool:
    """Return True when read-only sandbox should fall back to full-access + prompt constraint."""
    return bool(os.environ.get("CODEXMCP_READONLY_FALLBACK", ""))
```

修改 `build_codex_command()` 中的 sandbox 映射逻辑：当 fallback 启用且 sandbox 为 `"read-only"` 时，映射为 `"danger-full-access"`：

```python
def build_codex_command(
    cwd: str,
    sandbox: str,
    *,
    session_id: str = "",
) -> list[str]:
    effective_sandbox = sandbox
    if sandbox == "read-only" and is_readonly_fallback():
        effective_sandbox = "full-access"
    cli_sandbox = _SANDBOX_MAP.get(effective_sandbox, effective_sandbox)
    # ... 其余不变
```

**接口约定：**
- `is_readonly_fallback() -> bool` — 供 Module 3（task_manager）和 Module 4（server）使用

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
python -c "
import os
os.environ.pop('CODEXMCP_READONLY_FALLBACK', None)
from codexmcp.command_builder import build_codex_command, is_readonly_fallback

# 未设置环境变量时
assert not is_readonly_fallback(), 'should be False when unset'
cmd = build_codex_command('/tmp', 'read-only')
assert 'read-only' in cmd, f'expected read-only: {cmd}'

# 设置环境变量后
os.environ['CODEXMCP_READONLY_FALLBACK'] = '1'
# 需要重新导入或直接调用（函数每次读 env）
assert is_readonly_fallback(), 'should be True when set'
cmd2 = build_codex_command('/tmp', 'read-only')
assert 'danger-full-access' in cmd2, f'expected danger-full-access: {cmd2}'

# full-access 不受影响
cmd3 = build_codex_command('/tmp', 'full-access')
assert 'danger-full-access' in cmd3, f'full-access should stay: {cmd3}'

os.environ.pop('CODEXMCP_READONLY_FALLBACK', None)
print('OK')
"
```
预期输出 `OK`。

### Module 2: stream_processor — 日志安全审计

**涉及文件：**
- 修改: `src/codexmcp/stream_processor.py`

**关键改动：**

在文件末尾新增独立函数 `audit_readonly_violations()`，不修改已有的 `StreamProcessor` 类：

```python
import re

_DANGEROUS_CMD_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bsed\s+-i\b"),
    re.compile(r"\btee\s+"),
    re.compile(r"[^<]>{1,2}\s*\S"),       # > file or >> file (exclude <<)
    re.compile(r"\bmv\s+"),
    re.compile(r"\bcp\s+"),
    re.compile(r"\brm\s+"),
    re.compile(r"\bchmod\s+"),
    re.compile(r"\bchown\s+"),
    re.compile(r"\bpatch\s+"),
    re.compile(r"\binstall\s+"),
    re.compile(r"\bvim\s+"),
    re.compile(r"\bnano\s+"),
    re.compile(r"\bemacs\s+"),
    re.compile(r"\bgit\s+commit\b"),
    re.compile(r"\bgit\s+push\b"),
    re.compile(r"\bgit\s+checkout\s+-b\b"),
    re.compile(r"\bgit\s+merge\b"),
    re.compile(r"\bgit\s+rebase\b"),
]

_DANGEROUS_TOOL_NAMES: set[str] = {
    "write_file", "edit_file", "create_file", "apply_patch",
    "delete_file", "rename_file", "move_file",
}


def audit_readonly_violations(log_file: str) -> dict:
    """Scan a codex JSONL log for file-modifying operations.

    Returns a dict with keys: mode, violations_detected, violations, verdict.
    """
    violations: list[str] = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    data = json.loads(stripped)
                except (json.JSONDecodeError, ValueError):
                    continue

                item = data.get("item", data)
                item_type = item.get("type", "")

                if item_type == "command_execution":
                    cmd_text = item.get("command", "")
                    for pat in _DANGEROUS_CMD_PATTERNS:
                        if pat.search(cmd_text):
                            violations.append(f"command: {cmd_text[:200]}")
                            break

                if item_type == "function_call":
                    tool_name = item.get("name", "")
                    if tool_name in _DANGEROUS_TOOL_NAMES:
                        args_str = str(item.get("arguments", ""))[:100]
                        violations.append(f"tool_call: {tool_name}({args_str})")
    except FileNotFoundError:
        pass

    return {
        "mode": "fallback",
        "violations_detected": len(violations),
        "violations": violations[:20],  # cap to avoid huge payloads
        "verdict": "VIOLATION" if violations else "CLEAN",
    }
```

**接口约定：**
- `audit_readonly_violations(log_file: str) -> dict` — 供 Module 4（server）使用
- 返回 `{"mode": "fallback", "violations_detected": int, "violations": list[str], "verdict": "CLEAN"|"VIOLATION"}`

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
python -c "
import tempfile, json, os
from codexmcp.stream_processor import audit_readonly_violations

# 创建一个模拟 log 文件
with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
    # 正常事件
    f.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'review done'}}) + '\n')
    # 危险命令
    f.write(json.dumps({'type': 'item.completed', 'item': {'type': 'command_execution', 'command': 'sed -i s/old/new/ file.py'}}) + '\n')
    # 危险工具
    f.write(json.dumps({'type': 'item.completed', 'item': {'type': 'function_call', 'name': 'write_file', 'arguments': {'path': 'src/main.py'}}}) + '\n')
    log_path = f.name

result = audit_readonly_violations(log_path)
assert result['verdict'] == 'VIOLATION', f'expected VIOLATION: {result}'
assert result['violations_detected'] == 2, f'expected 2: {result}'
os.unlink(log_path)

# 干净 log
with tempfile.NamedTemporaryFile(mode='w', suffix='.log', delete=False) as f:
    f.write(json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'all good'}}) + '\n')
    clean_path = f.name

result2 = audit_readonly_violations(clean_path)
assert result2['verdict'] == 'CLEAN', f'expected CLEAN: {result2}'
os.unlink(clean_path)
print('OK')
"
```
预期输出 `OK`。

### Module 3: task_manager — 提示词注入

**涉及文件：**
- 修改: `src/codexmcp/task_manager.py`

**关键改动：**

1. 新增 import：`from .command_builder import is_readonly_fallback`

2. 新增常量 `_READONLY_CONSTRAINT_PROMPT`（硬编码约束提示词）：

```python
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
```

3. 在 `start_task()` 中，写入 prompt 文件前，检测 fallback 并 prepend 约束：

```python
    # --- prompt injection for readonly fallback ---
    effective_prompt = prompt
    if sandbox == SandboxMode.READ_ONLY and is_readonly_fallback():
        effective_prompt = _READONLY_CONSTRAINT_PROMPT + prompt

    # --- files ---
    task_dir = _ensure_task_dir(task_id)
    log_file = str(task_dir / "codex-exec.log")
    prompt_file = str(task_dir / "prompt.md")
    Path(prompt_file).write_text(effective_prompt, encoding="utf-8")
```

**接口约定：**
- 使用 Module 1 的 `is_readonly_fallback() -> bool`
- `TaskMeta` 不变，不新增字段

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
python -c "
import os
os.environ['CODEXMCP_READONLY_FALLBACK'] = '1'
from codexmcp.task_manager import _READONLY_CONSTRAINT_PROMPT
assert 'STRICTLY FORBIDDEN' in _READONLY_CONSTRAINT_PROMPT
assert 'READ-ONLY MODE' in _READONLY_CONSTRAINT_PROMPT
print('OK: constraint prompt exists')
os.environ.pop('CODEXMCP_READONLY_FALLBACK', None)
"
```
预期输出 `OK: constraint prompt exists`。

### Module 4: server — 审计结果注入

**涉及文件：**
- 修改: `src/codexmcp/server.py`

**关键改动：**

1. 新增 import：`from .command_builder import is_readonly_fallback` 和 `from .stream_processor import audit_readonly_violations`

2. 修改 `_build_result()` 函数，在 fallback + read-only 模式下追加审计：

```python
def _build_result(meta: task_manager.TaskMeta) -> Dict[str, Any]:
    """Build the standard result dict from completed task metadata."""
    result_text, session_id, usage = task_manager._parse_log(meta.log_file)
    resp: Dict[str, Any] = {
        # ... 现有字段不变 ...
    }
    # ... 现有 usage/worktree 逻辑不变 ...

    # readonly fallback audit
    if meta.sandbox == SandboxMode.READ_ONLY and is_readonly_fallback():
        resp["readonly_audit"] = audit_readonly_violations(meta.log_file)

    return resp
```

**接口约定：**
- 使用 Module 1 的 `is_readonly_fallback() -> bool`
- 使用 Module 2 的 `audit_readonly_violations(log_file: str) -> dict`

**验证：**
在 `/workspace/Tools/codexmcp` 执行：
```bash
python -c "
import inspect
from codexmcp import server
source = inspect.getsource(server)
assert 'readonly_audit' in source, 'readonly_audit not found in server.py'
assert 'audit_readonly_violations' in source, 'audit function not imported'
assert 'is_readonly_fallback' in source, 'fallback check not imported'
print('OK')
"
```
预期输出 `OK`。

## 状态
- [x] Module 1: command_builder — fallback 判断与 sandbox 重映射
- [x] Module 2: stream_processor — 日志安全审计
- [x] Module 3: task_manager — 提示词注入
- [x] Module 4: server — 审计结果注入
- [x] 验证与审阅
