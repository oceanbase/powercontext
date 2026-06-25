# OpenClaw {#openclaw}

通过 `memory-powermem` 插件将 [OpenClaw](https://github.com/openclaw/openclaw) 连接到 PowerMem。

## 推荐设置 {#recommended-setup}

安装 OpenClaw 插件：
```bash
openclaw plugins install memory-powermem
```
默认情况下，插件以CLI模式运行：它调用`pmem`，将数据存储在`~/.openclaw/`下，并使用OpenClaw已经注入的模型。对于单用户本地设置，不需要单独的PowerMem服务器。

## 手动设置 {#manual-setup}

当您希望OpenClaw共享团队PowerMem后端时，请使用HTTP模式：
```bash
powermem-server --host 0.0.0.0 --port 8848
```
然后配置插件的 `requestConfig.memory_db` 指向服务器的 URL，例如 `http://localhost:8848`。

## 验证 {#verify}

1. 启动启用了插件的 OpenClaw。
2. 让 OpenClaw 记住一个探针，例如 `PowerMem OpenClaw probe: dragonfruit-zx9`。
3. 让 OpenClaw 回忆 `dragonfruit-zx9`。
4. 确认探针出现在响应中。

## 故障排查 {#troubleshooting}

- 如果 CLI 模式失败，确认 `pmem` 在 `PATH` 中可用。
- 如果 HTTP 模式失败，确认 `http://localhost:8848/api/v1/system/health` 是健康的。
- 如果回忆返回为空，确认写入和搜索使用的是相同的 user/agent scope。

## 卸载 {#uninstall}

使用 OpenClaw 的插件管理命令移除 OpenClaw 插件。不要删除 `~/.openclaw/` 记忆数据，除非您明确希望移除存储的记忆。