# 场景：并行模块实现

## 适用条件

- 计划已拆分为多个可并行的独立模块
- 各模块完成后逐个审阅、合并

## 基本流程

```
1. 确保工作区已 commit（worktree 基于 HEAD 创建）
2. 并发调用多个 codex (blocking, full-access, 不同 topic)
3. 等待所有完成
4. 逐个审阅 diff → 合并 → 清理 worktree
```

## Prompt 编写

四要素：**Goal → Context → Constraints → Done-when**

```markdown
# Codex 任务：<任务名>

## Goal
1-2 句话。**只改 `<文件列表>`。**

## Context
技术栈、参考实现、接口约定

## Constraints
只改指定文件、向后兼容、不加无意义注释

## Done-when
可验证条件（测试通过等）

## 提交
`type(scope): description`
```

| 原则 | 说明 |
|------|------|
| 文件范围先行 | Goal 第一句限定范围，防止扩散 |
| 改动可枚举 | 编号列表，不用模糊描述 |
| 完成标准可验证 | "测试通过" — 不是"代码正确" |
| 接口约定 | 模块间有依赖时说明签名 |
| 长度控制 | 150-200 行以内，超过则拆分 |

## 示例

```
# 模块 1
CallMcpTool(server="codex", toolName="codex", arguments={
  "prompt": "# Codex 任务：用户注册\n\n## Goal\n实现 POST /api/auth/register。只改：\n- src/auth/register.py（新建）\n- tests/test_register.py（新建）\n- src/auth/__init__.py（添加路由）\n\n## Context\nFastAPI + SQLAlchemy + PostgreSQL\nUser 模型在 src/models/user.py\n\n## Constraints\n只改上述文件，用 get_db()\n\n## Done-when\npytest tests/test_register.py 通过\n\n## 提交\nfeat(auth): add registration",
  "cwd": "/workspace/my-project",
  "topic": "implement-user_register-v1",
  "sandbox": "full-access"
})

# 模块 2（并行）
topic: "implement-user_login-v1"

# 模块 3（并行）
topic: "implement-password_reset-v1"
```

## 合并与清理

```bash
cd /workspace/my-project
git diff main...agent/implement-user_register  # 审阅
git merge agent/implement-user_register --no-edit

# 清理
git worktree remove /workspace/my-project-agent-implement-user_register --force
git branch -D agent/implement-user_register
```

## 注意事项

- **worktree 基于 HEAD 创建，不含未提交改动**，启动前先 commit
- 合并可能冲突，手动解决
- 并行建议不超过 5 个
- resume 时 topic 版本号 +1（`v1`→`v2`），worktree 自动复用（分支名不含版本号）
