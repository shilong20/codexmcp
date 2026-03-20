# 场景：长任务后台分派

## 触发条件

仅在以下情况使用 `codex_dispatch`：
1. 用户**明确要求**分派/后台执行/长任务
2. 你有清晰计划，Codex 后台执行期间你自己有实质性工作

其他情况一律用阻塞模式 `codex`。

## 基本流程

```
1. 确保工作区已 commit
2. codex_dispatch (full-access) → 立即返回 task_id
3. 继续做其他工作 / 结束对话
4. codex_status 查看状态
5. 完成后审阅 diff → 合并
```

## Prompt 编写

在四要素基础上，dispatch prompt 必须**自包含**（分派后无交互）：

| 原则 | 说明 |
|------|------|
| 引用计划文档 | `详细计划见 docs/plans/...`，Codex 可读 worktree 中的文件 |
| 分步描述 | 每步编号，减少歧义 |
| 分步提交 | `每完成一个模块 commit`，不堆到最后 |
| 完成标志 | 创建特定文件（如 CHANGELOG.md）作为完成标识 |

## 示例

```
CallMcpTool(server="codex", toolName="codex_dispatch", arguments={
  "prompt": "# Codex 任务：数据库层异步化\n\n## Goal\n将 src/db/ 下同步操作改为异步。\n\n## 步骤\n1. src/db/session.py：create_engine → create_async_engine\n2. 逐个修改 src/db/ 下模块\n3. 每完成一个模块 commit\n4. 更新 requirements.txt\n\n## Done-when\npytest tests/test_db/ 通过\n根目录创建 CHANGELOG.md\n\n## 提交\n每模块：refactor(db): async <module>",
  "cwd": "/workspace/my-project",
  "topic": "longrun-async_refactor-v1",
  "sandbox": "full-access"
})
```

## 查看与取消

```
# 查看
CallMcpTool(server="codex", toolName="codex_status", arguments={
  "task_id": "codex-longrun-async_refactor-v1"
})

# 列出所有任务
CallMcpTool(server="codex", toolName="codex_status", arguments={})

# 取消
CallMcpTool(server="codex", toolName="codex_cancel", arguments={
  "task_id": "codex-longrun-async_refactor-v1"
})
```

## 实时日志

- IDE：`.codex-tasks/longrun-async_refactor-v1/codex-exec.log`（含版本号）
- 终端：`tmux attach -t codex-longrun-async_refactor-v1`（tmux session 含版本号）

## 注意事项

- 运行在 tmux 中，MCP 重启/断连后仍可查询
- **worktree 基于 HEAD 创建**，先 commit
- 完成后仍需人工审阅 diff 再合并
