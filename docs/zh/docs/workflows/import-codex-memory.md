---
title: 导入已有 Codex Memory
description: 用 Source 插件把 .codex/memories 中的派生资料导入为可追溯、可重复提交的文件快照。
---

# 导入已有 Codex Memory

此功能读取 Codex home 下 `memories/` 中已经生成的文本资料，解决 [#1384](https://github.com/oceanbase/powercontext/issues/1384)。
历史会话中的用户 Prompt 使用另一项 [Codex 历史会话采集能力](source-plugins.md#codex-历史会话)。

每个导入文件形成 `codex-memory-snapshot` Source，包含 `namespace`、相对文件路径、原始文件字节的 SHA-256、
脱敏后的正文及是否发生脱敏。其 TextEvidence Projection 明确标记 `origin=codex-memory` 和 `evidence_kind=derived_memory`。
它保留的是已有派生资料，不直接创建 Memory、Skill、Handoff 或已审核 Artifact，也不执行正文中的指令或脚本。
文件里的引用保持原文，不据此伪造原始会话引用；文件摘要证明所读版本的指纹，不证明内容真实或已审核。
远程 Source 的证据声明默认仍是 `untrusted` / `unknown`。

## 准备

插件代码位于 checkout 的 `integrations/codex/memory_source_plugin/`。在持有文件的机器上使用仓库 Python 环境：

```sh
cd /path/to/powercontext
uv sync
```

准备一个运行中的 Server、已有目标 Scope，以及管理员和该 Scope 贡献者分别使用的凭据文件。文件只包含对应 bearer token；
Unix 下设置为 `600`，不能使用符号链接。管理员负责登记 Definition，普通导入身份需要 `scope.read` 和 `scope.contribute`，
不需要 `server.admin`。Server 不会自动获得客户端文件系统的访问权。

`namespace` 是用户为这份资料集合选择的稳定名称，例如 `personal-codex-memory`。不同设备或资料集合使用不同名称，
同一集合的重复导入保持名称不变。不要用凭据或敏感绝对路径作为名称。

## 一次性导入：macOS 或 Linux

先在本机预览。该命令不连接 Server、不写 Source，仅输出相对路径、预计 Source ID、失败原因和空文件清单：

```sh
uv run python -m integrations.codex.memory_source_plugin preview \
  --codex-home "$HOME/.codex" --namespace personal-codex-memory
```

不传 `--codex-home` 时采用 `CODEX_HOME`，未设置则采用 `~/.codex`。`--path` 默认为 `.`，表示整个 `memories/`；
例如 `--path MEMORY.md` 只选择一个文件，`--path rollout_summaries` 只选择该子目录。

管理员登记不可变的 Source Definition。此命令只登记数据契约，不上传或在 Server 加载插件代码：

```sh
uv run python -m integrations.codex.memory_source_plugin register \
  --server-url http://127.0.0.1:8000 --credential-file /path/to/admin.token
```

然后用 Scope 贡献者凭据导入：

```sh
uv run python -m integrations.codex.memory_source_plugin import \
  --codex-home "$HOME/.codex" --namespace personal-codex-memory \
  --scope-id SCOPE_ID --binding-id personal-codex-memory \
  --server-url http://127.0.0.1:8000 --credential-file /path/to/contributor.token
```

`SCOPE_ID` 必须替换为真实 ID；导入不推断默认 Scope。`binding-id` 是 Server checkpoint 的稳定身份，
重复导入同一集合和选择范围时保持不变。不同导入任务使用不同 binding。远程连接优先使用 HTTPS；
连接策略与显式 HTTP 选择见[连接远程 Server](../operate/connect-remote-server.md)。
也可通过 `POWERCONTEXT_CLIENT_SERVER_URL` 和 `POWERCONTEXT_CLIENT_API_TOKEN` 配置连接；不要把 token 放进命令参数。

成功时 JSON `status` 为 `complete`，`items` 中的 `source_ref` 是真实提交回执；重复运行可能没有新项。
预览、导入发现失败时退出码为 `1`，参数语法错误为 `2`。预览不输出正文；导入结果不输出正文或 checkpoint。

## 持续采集：Linux 托管 Runtime

需要持续跟踪文件时，使用 [Source 插件部署流程](source-plugins.md#在运行中的-server-上加载-collect)，
在执行机器准备可信代码和凭据，修改插件目录中的 `powercontext.yml`：

```yaml
id: codex-memory
plugin: .
scope_ids: [SCOPE_ID]
config:
  codex_home: /absolute/path/to/codex-home
  namespace: personal-codex-memory
  poll_interval: 30
```

`codex_home` 必须是执行机器上的绝对路径。按前述部署流程准备环境文件后加载：

```sh
powercontext source load \
  --config /path/to/powercontext/integrations/codex/memory_source_plugin/powercontext.yml \
  --env-file /path/to/pc.env
```

清单没有初始目标，加载本身不会读取记忆资料。通过 Server 公共 MCP 显式添加目标：

```javascript
pc_source_capabilities({"scope_id":"SCOPE_ID"})
pc_source_instances({"scope_id":"SCOPE_ID"})
pc_source_add({
  "scope_id":"SCOPE_ID","instance_id":"codex-memory",
  "capability":"codex.memory-history","capability_version":"1",
  "input":{"path":"."}
})
```

保留返回的 `target_id`。查询、停用及恢复：

```javascript
pc_source_status({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","view":"control"})
pc_source_target_set_enabled({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","enabled":false})
pc_source_target_set_enabled({"scope_id":"SCOPE_ID","target_id":"TARGET_ID","enabled":true})
```

`tracking=stopped` 表示 Runtime 已确认目标停用。停用不删除既有 Source；重新启用也不会隐式启用已停用的实例。
托管部署仅支持 Linux；一次性导入支持 macOS 和 Linux。Windows 本地读取暂不支持，可在 Unix 执行机器上导入显式转移的资料。

## 检查保存结果及后续使用

使用回执的 `source_type`、`source_id` 通过
`GET /v1/scopes/{scope_id}/sources/{source_type}/{source_id}` 读回；
`GET /v1/scopes/{scope_id}/sources` 可浏览所有已保存快照。管理服务目标状态只保留最近一次提交报告，不能代替完整 Source 清单。

需要把资料处理为可召回的 Memory 时，按[启用提取与向量搜索](../get-started/configure-models.md)配置模型与处理策略，
再检查实际生成的 Memory 及其 Source 引用。导入回执只证明 Source 保存成功，不证明 Memory 已生成、已审核或召回有效。

## 文件、版本和恢复规则

- 递归选择 UTF-8（允许 BOM）的 `.md`、`.markdown` 和 `.txt` 文件，例如 `MEMORY.md`、`memory_summary.md`、
  `raw_memories/`、`rollout_summaries/` 和 `skills/` 下的文本。正文按文件原样保存，除 BOM 解码和已知凭据脱敏外不重写。
  目录中的 JSON、数据库、脚本和其他格式不导入；显式选择不支持的文件会报 `unsupported_format`。
- 拒绝绝对选择路径、`..` 越界、符号链接、二进制文本、无效 UTF-8 和超限文件。空白文件列为跳过项。
  环境中的已知凭据及所选 Codex home 的 `auth.json` 中的已知凭据会被脱敏；这不是通用敏感信息识别器。
- 相同 namespace、相对路径和文件快照复用同一 Source。修改产生新 Source，旧快照仍可精确读回；
  恢复到先前完全相同的快照会复用先前 Source，不把重复快照当作新事件。删除原文件不会删除历史 Source。
- checkpoint 按目标 Server、binding/instance 与 Scope 隔离，只在整批成功后 CAS 推进。
  部分文件失败时，其他文件可能已保存，但 checkpoint 不推进；修复后重跑通过 Source 身份去重。
  checkpoint 丢失、进程中断或首次切换到托管实例也可以安全重交。并发 checkpoint 冲突不会覆盖已有进度，应串行重试。
- 每轮完整扫描所选范围并重新读取文件；不按追加字节增量读取。默认每文件最多 256 KiB，
  每目标每轮文件读取预算 16 MiB，最多 1,000 个文本文件和 10,000 个目录项。读取失败也保守占用预算，探测文件增长最多额外读取一个字节。
  文件数或目录项超限时不接受该目标的部分目录枚举。checkpoint 只记录各路径最近成功的快照，不累计所有历史版本。
  可用配置或一次性命令的 `--max-file-bytes`、`--max-total-bytes`、`--max-files`、`--max-entries` 调整限额。
