# 场景：长任务后台分派

## 适用条件

- 任务预计执行时间较长（数十分钟到数小时）
- 分派后可以结束当前对话
- 下次打开时通过 codex_status 查看结果

## 基本流程

```
1. 调用 codex_dispatch（async, full-access）→ 立即返回 task_id
2. 结束当前对话
3. 下次打开对话，调用 codex_status 查看状态
4. 完成后审阅 diff，合并到主分支
```

## 示例：分派长任务

```
CallMcpTool(server="codexmcp", toolName="codex_dispatch", arguments={
  "prompt": "对整个项目进行以下重构：\n1. 将所有同步 IO 改为异步\n2. 添加类型注解\n3. 补充单元测试覆盖率到 80%\n\n详细计划见 docs/plans/2026-03-20-refactor.md",
  "cwd": "/workspace/my-project",
  "topic": "refactor-async",
  "sandbox": "full-access"
})
```

返回：
```json
{
  "task_id": "codex-refactor-async",
  "status": "running",
  "topic": "refactor-async",
  "log_file": "/home/user/.codexmcp/tasks/codex-refactor-async/codex-exec.log"
}
```

## 查看进度

下次打开对话时：

```
CallMcpTool(server="codexmcp", toolName="codex_status", arguments={
  "task_id": "codex-refactor-async"
})
```

运行中返回：
```json
{
  "status": "running",
  "elapsed_seconds": 1234.5,
  "recent_events": [
    {"type": "command", "text": "git add src/..."},
    {"type": "text", "text": "Now working on async IO conversion..."}
  ]
}
```

完成后返回：
```json
{
  "status": "completed",
  "exit_code": 0,
  "result": "Codex 的完成报告...",
  "session_id": "thread_xyz",
  "diff_stat": "42 files changed, 1500 insertions(+), 800 deletions(-)",
  "commits_ahead": 15
}
```

## 列出所有任务

```
CallMcpTool(server="codexmcp", toolName="codex_status", arguments={})
```

## 实时查看日志

两种方式：
- IDE 中打开 `.codex-tasks/refactor-async/codex-exec.log`
- 终端：`tmux attach -t codex-refactor-async`（Ctrl+B, D 退出）

## 注意事项

- 分派任务运行在 tmux 中，MCP 服务重启后仍可通过 codex_status 查询
- **full-access worktree 基于当前 HEAD commit 创建，不含未提交的改动**。分派前请先 commit
- 长任务建议在 prompt 中附上详细计划文档
- 任务完成后仍需人工审阅 diff 再合并
- 取消：`codex_cancel(task_id="codex-refactor-async")`
