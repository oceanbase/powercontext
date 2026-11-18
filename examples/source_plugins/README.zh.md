# Source 插件示例

每个目录是一个 Source 插件：`plugin.py` 把 `SourcePlugin` 赋给 `plugin`，目录名就是插件名。把目录复制到 Server 的
插件目录（`POWERCONTEXT_SERVER_PLUGINS_DIR`）即可加载；设备上采集时，再把同一目录放进 `powercontext source-agent
--plugins` 指向的目录。部署、权限、目标管理和升级见 [Source 插件指南](../../docs/zh/docs/workflows/source-plugins.md)。

| 目录 | Source 类型 | 采集 |
| --- | --- | --- |
| [tickets](tickets/) | `example-ticket`，带文本和结构化 Projection | 从 runner 本机的 JSON 文件采集工单，样例数据 `tickets.json` |
| [ticket-chat](ticket-chat/) | `example-chat`，带消息列表 Projection | 从 runner 本机的 JSON 文件采集聊天室，样例数据 `chats.json` |
| [images](images/) | `example-image`，校验 PNG/JPEG | 不采集，通过 Source 写入接口提交；Server 需要安装 `powercontext[source-images]` |

给 Server 自带的 runner 分配工单目标（需要 `server.admin`）：

```javascript
pc_source_add({"scope_id": "SCOPE_ID", "runner": "server", "capability": "tickets",
               "parameters": {"file": "/srv/data/tickets.json"}})
```

在设备上采集聊天：

```sh
export POWERCONTEXT_SOURCE_AGENT_TOKEN=...
powercontext source-agent --server https://memory.example.com --plugins ./plugins --name robot-7
```

```javascript
pc_source_add({"scope_id": "SCOPE_ID", "runner": "robot-7", "capability": "ticket-chat",
               "parameters": {"file": "/var/lib/robot/chats.json"}})
```
