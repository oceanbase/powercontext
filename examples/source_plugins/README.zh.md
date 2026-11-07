# Source 插件示例

本目录按业务场景组织可复制的插件，每个目录就近放置代码、加载配置和样例数据。Server 部署、权限、启停与升级步骤见 [Source 插件指南](../../docs/zh/docs/workflows/source-plugins.md)。

| 目录 | 能力 | 配置及准备 |
| --- | --- | --- |
| [tickets](tickets/) | `ticket.write@1` 同步写入；`tickets.item@1` 持续采集 | write 使用 `write.yml`；collect 使用 `powercontext.yml` 和 `tickets.json` |
| [ticket_chat](ticket_chat/) | `ticket-chat.dataset@1` 采集工单和聊天 | `powercontext.yml`、`events.json`；修改 Scope 和初始目标 |
| [images](images/) | `images.write@1` 写入 PNG/JPEG | `write.yml`；Server 安装 `powercontext[source-images]` |

write 代码部署在 Server 机器，collect 代码部署在 Runtime 机器。清单中的 `entry: .` 或 `plugin: .` 都指向同目录的代码。以下命令使用已配置鉴权、MCP 和 PC Access 的 Server；Scope 须已存在，宿主须有对应读取和贡献权限。

## 工单同步写入

复制整个 `tickets` 目录到 Server 上的普通部署目录，使用管理员凭据加载：

```sh
powercontext source load --kind write \
  --config /path/to/tickets/write.yml \
  --server-url http://127.0.0.1:8000 \
  --credential-file /path/to/admin-token
```

宿主通过公共 MCP 发现能力并提交完整工单：

```javascript
pc_source_capabilities({"scope_id":"SCOPE_ID"})
pc_source_add({
  "scope_id": "SCOPE_ID",
  "capability": "ticket.write",
  "capability_version": "1",
  "input": {
    "namespace": "support",
    "project": "powercontext",
    "ticket_id": "PC-123",
    "title": "数据库恢复",
    "body": "重试连接后服务恢复。",
    "labels": ["数据库"],
    "metadata": {"owner": null, "attempts": 2}
  }
})
```

返回 `result.source_refs` 后可读取保存结果。同一完整快照复用 Source，内容变化产生新身份。[definition.py](tickets/definition.py) 同时提供结构化 `example.ticket-details@1` 和标准文本 `powercontext.text-evidence@1`。

## 工单持续采集

使用同一个 `tickets` 包，将 `powercontext.yml` 的 `scope_ids` 改为实际 Scope。按需修改同目录的 `tickets.json`，然后使用与 Server 一致的环境文件加载：

```sh
powercontext source load \
  --config /path/to/tickets/powercontext.yml \
  --env-file ./pc.env
```

宿主添加文件中 ID 为 A 的工单作为持续采集目标：

```javascript
pc_source_add({"scope_id":"SCOPE_ID","instance_id":"my-tickets","capability":"tickets.item","capability_version":"1","input":{"id":"A"}})
```

插件按 `config.poll_interval` 读取文件，跳过未变化的快照。write 与 collect 共用同一份 Definition 和 Projection，分别加载，不会互相启动。

## 其他示例的输入与输出

- **ticket_chat：** 按相同 collect 流程加载。清单中的初始目标输入为 `{}`；`events.json` 生成工单对象 `example.ticket-details@1` 和聊天数组 `example.chat-messages@2`。`receipts.jsonl` 记录已确认的 Scope 和 SourceRef。
- **images：** 按相同 write 流程加载 `images/write.yml`。MCP 输入为 `media_type`、`data_base64` 和可选 `title`，校验 PNG/JPEG 内容；大小和并发限制沿用 Server write 规则。
