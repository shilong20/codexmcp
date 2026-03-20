# 故障排查

## 前置条件安装

### tmux

tmux 是必须的，所有任务都通过 tmux session 运行。

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

### git

git 在 full-access 模式下必须（用于创建 worktree）。

```bash
# Ubuntu/Debian
sudo apt install git

# macOS
brew install git

# 验证
git --version
```

## 常见错误

### "tmux is required but not found"

**原因**：tmux 未安装或不在 PATH 中。
**解决**：按上面的说明安装 tmux。如果已安装但 MCP 进程找不到，检查 MCP 服务进程的 PATH 环境变量。

### "git is required for full-access mode"

**原因**：选择了 full-access sandbox 但 git 未安装。
**解决**：安装 git，或改用 read-only sandbox（如果任务不需要修改代码）。

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

### 阻塞任务（codex）长时间无响应

**原因**：Codex 进程可能挂死或陷入死循环，导致 tmux session 一直存活但不产出结果。
**解决**：
1. 在终端中手动终止：`tmux kill-session -t codex-<topic>`
2. 任务会自动标记为 failed
3. 如果 MCP 调用仍在阻塞，可能需要重启 MCP 服务或 Cursor

### 任务状态显示 "failed"，result 为 "tmux session terminated unexpectedly"

**原因**：tmux session 在 codex 完成前意外退出。可能是系统 OOM、手动 kill 等。
**解决**：
1. 检查日志：`cat ~/.codexmcp/tasks/<task_id>/codex-exec.log`
2. 检查系统日志：`dmesg | tail` 或 `journalctl -xe`
3. 重新分派任务

### codex 命令未找到

**原因**：codex CLI 未安装或不在 PATH 中。
**解决**：
```bash
npm install -g @openai/codex
# 或
npx @openai/codex --version
```

## 日志位置

| 文件 | 位置 |
|------|------|
| 任务元数据 | `~/.codexmcp/tasks/<task_id>/meta.json` |
| 执行日志 | `~/.codexmcp/tasks/<task_id>/codex-exec.log` |
| 任务指令 | `~/.codexmcp/tasks/<task_id>/prompt.md` |
| 工作区链接 | `<cwd>/.codex-tasks/<topic>/`（symlink） |

## 手动清理

```bash
# 清理特定任务
rm -rf ~/.codexmcp/tasks/codex-<topic>
rm -f <cwd>/.codex-tasks/<topic>

# 清理所有任务
rm -rf ~/.codexmcp/tasks/

# 清理 worktree
cd /path/to/repo
git worktree list  # 查看所有 worktree
git worktree remove ../repo-agent-<topic> --force
git branch -D agent/<topic>

# 清理 tmux session
tmux kill-session -t codex-<topic>
tmux ls | grep codex-  # 列出所有 codex session
```
