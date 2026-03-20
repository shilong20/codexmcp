# 场景：代码审阅

## 适用条件

- 写完代码/计划后需要 Codex 审阅
- 只读操作，不修改代码
- 可能需要多轮审阅（审阅→修改→再审阅）

## 基本流程

```
1. codex (blocking, read-only) → 等待审阅结果
2. 根据意见修改代码
3. 用 session_id + topic 版本号 +1 再次调用 → 继续审阅
4. 重复直到通过
```

## Prompt 编写

模板：

```
你是一个 [角色]，针对 [目标文件] 进行 [具体任务]。

先用 rg/read 搜索定位相关代码，再基于搜索结果分析。

重点关注：[维度1]、[维度2]
不需要关注：[排除项]

输出格式：
[期望结构]

不要编造不存在的文件，无法确认时标注"未验证"。
```

| 原则 | 说明 |
|------|------|
| 搜索优先 | 要求先用 rg/read 定位再分析 |
| 绝对路径 | 文件路径用绝对路径 |
| 聚焦维度 | 每个 prompt 只关注 1-2 个核心维度 |
| 明确排除 | 告诉 Codex 不需要看什么 |
| 输出格式 | 指定输出结构 |

## 示例：首次审阅

```
CallMcpTool(server="codex", toolName="codex", arguments={
  "prompt": "你是一个资深 Python 后端工程师，审阅以下文件的代码质量。\n\n目标文件：\n- /workspace/my-project/src/auth/handler.py\n- /workspace/my-project/src/auth/middleware.py\n\n先用 rg 搜索这些文件中的关键函数和类定义，再逐一分析。\n\n重点关注：\n1. 安全性（SQL 注入、XSS、认证绕过）\n2. 错误处理\n\n不需要关注：代码风格、import 顺序\n\n输出格式：按严重程度分级（Critical / Warning / Info），每个问题含文件名、行号、描述、修复建议。",
  "cwd": "/workspace/my-project",
  "topic": "review-auth_handler-v1",
  "sandbox": "read-only"
})
```

## 示例：追加审阅（Resume）

topic 版本号 +1，传入上次的 session_id：

```
CallMcpTool(server="codex", toolName="codex", arguments={
  "prompt": "我已修改了：\n1. handler.py 第 42 行：SQL 参数化查询\n2. middleware.py 第 78 行：token 过期检查\n\n请确认修复并检查是否引入新问题。",
  "cwd": "/workspace/my-project",
  "topic": "review-auth_handler-v2",
  "sandbox": "read-only",
  "session_id": "019d0aa8-..."
})
```

## 多视角并行审阅

不同维度用不同 topic 并行：

```
topic: "review-security_api-v1"   # 安全审阅
topic: "review-perf_api-v1"       # 性能审阅（并行）
```

## 注意事项

- read-only 不走 tmux，直接 subprocess，通常几分钟完成
- Codex 的 shell 工具可能受 sandbox 限制，但代码阅读和分析不受影响
- resume 时 topic 版本号 +1（`v1`→`v2`），session_id 保持上下文。各版本有独立的日志和任务记录
