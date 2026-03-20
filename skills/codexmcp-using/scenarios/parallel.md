# 场景：并行模块实现

## 适用条件

- 计划已拆分为多个可并行的模块
- 每个模块由独立的 Codex 实例实现
- 各模块完成后逐个审阅、合并

## 基本流程

```
1. 并发调用多个 codex（blocking, full-access, 不同 topic）
2. 等待所有完成
3. 逐个审阅 diff
4. 逐个合并到主分支
5. 清理 worktree（可选）
```

## 示例：并行启动 3 个模块

每个调用使用不同的 `topic`，自动创建独立的 git worktree：

```
# 模块 1
CallMcpTool(server="codexmcp", toolName="codex", arguments={
  "prompt": "实现用户注册模块，包含邮箱验证...\n只修改 src/auth/register.py 和 tests/test_register.py",
  "cwd": "/workspace/my-project",
  "topic": "impl-register",
  "sandbox": "full-access"
})

# 模块 2（并行）
CallMcpTool(server="codexmcp", toolName="codex", arguments={
  "prompt": "实现用户登录模块，包含 JWT 生成...\n只修改 src/auth/login.py 和 tests/test_login.py",
  "cwd": "/workspace/my-project",
  "topic": "impl-login",
  "sandbox": "full-access"
})

# 模块 3（并行）
CallMcpTool(server="codexmcp", toolName="codex", arguments={
  "prompt": "实现密码重置模块...\n只修改 src/auth/reset.py 和 tests/test_reset.py",
  "cwd": "/workspace/my-project",
  "topic": "impl-reset",
  "sandbox": "full-access"
})
```

## 合并结果

每个任务完成后返回 `worktree_dir`、`agent_branch`、`diff_stat`。审阅 diff 后合并：

```bash
cd /workspace/my-project
git merge agent/impl-register --no-edit
git merge agent/impl-login --no-edit
git merge agent/impl-reset --no-edit
```

## Prompt 编写要点

- **明确限定修改范围**：列出只允许修改的文件，避免 Codex 改动不相关代码
- **提供接口约定**：如果模块间有依赖，在 prompt 中说明接口签名
- **包含测试要求**：要求 Codex 同时编写测试

## 注意事项

- 每个 `topic` 创建独立的 worktree 和分支（`agent/<topic>`）
- **worktree 基于当前 HEAD commit 创建，不含未提交的改动**。启动前请先 commit 或 stash 你的修改
- 合并时可能有冲突，需要手动解决
- 并行数量受 tmux session 上限和 API 限制影响，建议不超过 5 个
- 合并后可清理 worktree：`git worktree remove ../project-agent-<topic>`
