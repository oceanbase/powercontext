---
title: 导入已有 Codex Memory
description: 预览并将 Codex memories 目录中的指定派生文件采集为 Source 快照。
---

# 导入已有 Codex Memory

Codex Memory Source 插件读取 `CODEX_HOME/memories` 中已经生成的文本，并将每个文件版本保存为不可变的
`codex-memory-snapshot` Source。它不会创建已审核的 Memory，也不会执行文件内容。保存的 Source 属于不可信证据，
Agent 使用前仍需自行判断。

## 预览文件

在 PowerContext 仓库中运行：

```sh
uv run python -m integrations.codex.memory_source_plugin preview \
  --codex-home "$HOME/.codex" --path .
```

`--path` 相对于 `memories/`，可用 `MEMORY.md` 选择单个文件，或指定子目录。预览会列出相对路径、预期 Source ID、
空文件和有限的失败原因，不会连接 Server。

## 安装插件并采集

将 `integrations/codex/memory_source_plugin/` 复制到 Server 插件目录，并命名为 `codex-memory-history/`。读取文件的
Server 进程或设备 Runner 必须设置 `CODEX_HOME`，指向该机器上的 Codex 主目录。通过当前的
[Source 插件工作流](source-plugins.md) 加载插件并管理目标。

在目标 Scope 中通过 `pc_source_add` 注册采集目标：

```javascript
pc_source_add({
  "scope_id": "SCOPE_ID",
  "runner": "server",
  "capability": "codex-memory-history",
  "parameters": {"path": "."}
})
```

如果文件在其他机器上，将 `server` 改成该设备 Runner 名称，并在 Runner 上安装相同插件。添加目标后即开始采集；插件
会保存发生变化的文件，并将进度记录在目标 checkpoint 中。只需导入一次时，等待目标状态显示成功后停用目标：

```javascript
pc_source_target_set_enabled({"scope_id":"SCOPE_ID", "target_id":"TARGET_ID", "enabled":false})
```

可通过 Scope 的 Source API 查看导入结果。重新启用目标会继续跟踪；删除目标会移除 checkpoint，但保留已采集的 Source。
