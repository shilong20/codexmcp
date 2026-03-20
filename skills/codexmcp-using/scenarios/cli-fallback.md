# 场景：MCP 不可用时的 CLI 降级

## 适用条件

- CodexMCP 服务未连接或不稳定
- MCP 工具调用返回错误
- 需要临时使用 Codex 完成任务

## 前置条件

- `codex` CLI 已安装且在 PATH 中：`which codex`
- 已认证：`codex auth` 已完成

如果未安装：`npm install -g @openai/codex`

## 只读任务（审阅、分析）

```bash
codex exec --sandbox read-only --cd <工作目录> --json --skip-git-repo-check \
  -- "<prompt>"
```

使用 Shell 工具执行，设置 `block_until_ms: 0` 后台运行，定期检查终端文件获取输出。

## 写入任务（代码生成、修复）

```bash
codex exec --sandbox danger-full-access --cd <工作目录> --json --skip-git-repo-check \
  -- "<prompt>"
```

## 续跑会话（Resume）

从上次执行输出中提取 SESSION_ID（`thread.started` 事件的 `thread_id`），然后：

```bash
codex exec --sandbox read-only --cd <工作目录> --json --skip-git-repo-check \
  resume <SESSION_ID> "<后续 prompt>"
```

## 解析输出

Codex `--json` 模式输出 JSONL，每行一个事件：
- `type: "thread.started"` → 包含 `thread_id`（SESSION_ID）
- `type: "item.completed"` + `item.type: "agent_message"` → Codex 的回复文本
- `type: "item.completed"` + `item.type: "command_execution"` → 执行的命令
- `type: "turn.completed"` → 包含 `usage`（token 用量）

最终结果在最后一条 `agent_message` 中。

## 与 MCP 模式的差异

| 维度 | MCP 模式 | CLI 降级 |
|------|----------|----------|
| 进程持久化 | tmux session | Shell 后台运行 |
| worktree 隔离 | 自动创建 | 需手动处理 |
| 日志管理 | 自动持久化 + symlink | 在终端文件中 |
| 任务管理 | codex_status / codex_cancel | 手动 |

## 环境变量

CLI 降级时，可通过 codex 自身的配置（`~/.codex/config.toml`）控制模型和参数，也可在命令中直接指定：

```bash
# 指定推理强度
--config 'model_reasoning_effort="xhigh"'

# 指定 profile
--profile fast
```

## 注意事项

- 长任务必须后台运行（`block_until_ms: 0`），否则会超时
- CLI 降级不提供 worktree 隔离，并行任务需自行管理分支
- 优先恢复 MCP 连接，CLI 降级仅作为临时方案
