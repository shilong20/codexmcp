# 故障排查

## 前置条件

### tmux（仅 full-access 模式需要）

read-only 模式不使用 tmux，直接 subprocess 执行。full-access 模式（含 dispatch）需要 tmux：

```bash
# Ubuntu/Debian
sudo apt install tmux

# macOS
brew install tmux

# CentOS/RHEL
sudo yum install tmux

# 验证
tmux -V
```

### git（仅 full-access 模式需要）

full-access 模式通过 git worktree 创建隔离开发环境：

```bash
# Ubuntu/Debian
sudo apt install git

# macOS
brew install git

# 验证
git --version
```

### codex CLI

```bash
# 安装
npm install -g @openai/codex

# 验证
codex --version
```

## 常见错误

### "tmux is required for full-access mode but not found"

**原因**：选择了 full-access sandbox 但 tmux 未安装。
**解决**：安装 tmux（见上方），或改用 read-only sandbox（如果任务不需要修改代码）。
**注意**：read-only 模式不需要 tmux。

### "git is required for full-access mode"

**原因**：选择了 full-access sandbox 但 git 未安装或 cwd 不在 git 仓库内。
**解决**：安装 git 并确保 cwd 在 git 仓库内，或改用 read-only sandbox。

### "Task 'codex-xxx' is already running"

**原因**：同名 topic 的任务已在运行。
**解决**：
1. 用 `codex_cancel` 取消旧任务
2. 或使用不同的 `topic`

### "Worktree already exists at /path on branch 'xxx'"

**原因**：上次任务的 worktree 未清理。
**解决**：
```bash
git worktree remove /path/to/worktree --force
git branch -D agent/<topic>
```

### bwrap: No permissions to create a new namespace

**原因**：容器环境中 bubblewrap sandbox 无法创建 namespace。
**影响**：read-only 模式下 Codex 可以正常推理和返回结果，但其内部 shell 工具执行会受限。
**解决**：
- 对于审阅/分析任务：正常使用 read-only，Codex 可以阅读代码但无法执行 shell 命令
- 对于需要执行命令的任务：改用 full-access 模式
- 已内置 `--ephemeral` 跳过持久化 sandbox 创建

### 阻塞任务长时间无响应

**原因**：Codex 进程可能挂死或陷入死循环。
**解决**：
1. full-access 模式：在终端 `tmux kill-session -t codex-<topic>` 终止
2. read-only 模式：进程会在完成后自动返回，如需强制终止需重启 MCP 服务
3. dispatch 模式：用 `codex_cancel` 取消

### 任务状态显示 "failed"，result 为 "tmux session terminated unexpectedly"

**原因**：tmux session 在 codex 完成前意外退出（系统 OOM、手动 kill 等）。
**解决**：
1. 检查日志：`~/.codexmcp/tasks/<task_id>/codex-exec.log`
2. 检查系统日志：`dmesg | tail` 或 `journalctl -xe`
3. 重新分派任务

### uvx 拉取到旧版本 / 缓存问题

**原因**：`uvx` 缓存了旧版本的包。
**解决**：
```bash
# 刷新缓存
uvx --refresh --from codex-mcp-server codexmcp --help

# 如果镜像源尚未同步最新版，临时使用官方源
uvx --refresh --index-url https://pypi.org/simple/ --from codex-mcp-server codexmcp --help
```

## 日志位置

| 文件 | 位置 |
|------|------|
| 任务元数据 | `~/.codexmcp/tasks/<task_id>/meta.json` |
| 执行日志 | `~/.codexmcp/tasks/<task_id>/codex-exec.log` |
| 任务指令 | `~/.codexmcp/tasks/<task_id>/prompt.md` |
| 工作区链接 | `<cwd>/.codex-tasks/<topic>/`（symlink） |

## 手动清理

示例以 topic `implement-user_register-v1` 为例：

```bash
# 清理特定任务（task_id 含版本号）
rm -rf ~/.codexmcp/tasks/codex-implement-user_register-v1
rm -f <cwd>/.codex-tasks/implement-user_register-v1

# 清理所有任务
rm -rf ~/.codexmcp/tasks/

# 清理 worktree（分支名不含版本号）
cd /path/to/repo
git worktree list
git worktree remove ../repo-agent-implement-user_register --force
git branch -D agent/implement-user_register

# 清理 tmux session（仅 full-access，session 名含版本号）
tmux kill-session -t codex-implement-user_register-v1
tmux ls | grep codex-
```
