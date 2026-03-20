# 场景：代码审阅

## 适用条件

- 写完代码后需要 Codex 审阅
- 需要多轮审阅（审阅→修改→再审阅）
- 只读操作，不修改代码

## 基本流程

```
1. 调用 codex（blocking, read-only）→ 等待审阅结果
2. 根据审阅意见修改代码
3. 用 session_id 再次调用 codex（resume）→ 继续审阅
4. 重复直到通过
```

## 示例：首次审阅

```
CallMcpTool(server="codexmcp", toolName="codex", arguments={
  "prompt": "请审阅以下文件的代码质量和潜在问题：\n- src/auth/handler.py\n- src/auth/middleware.py\n\n重点关注：安全性、错误处理、代码风格",
  "cwd": "/workspace/my-project",
  "topic": "review-auth",
  "sandbox": "read-only"
})
```

## 示例：追加审阅（Resume）

首次审阅返回 `session_id: "thread_abc123"`。修改代码后：

```
CallMcpTool(server="codexmcp", toolName="codex", arguments={
  "prompt": "我已根据你的建议修改了代码，请重新审阅 src/auth/handler.py",
  "cwd": "/workspace/my-project",
  "topic": "review-auth-round2",
  "sandbox": "read-only",
  "session_id": "thread_abc123"
})
```

## 注意事项

- `sandbox` 必须是 `read-only`，审阅不需要写权限
- 每次 resume 用不同的 `topic`（避免 task_id 冲突）
- `session_id` 让 Codex 保持上下文，不需要重新读取代码
