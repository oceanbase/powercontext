---
title: 使用 OpenDAL 采集文本文件
description: 通过 OpenDAL 存储后端把 UTF-8 文件捕获为类型化 Source。
---

# 使用 OpenDAL 采集文本文件

使用 `OpenDALTextFileConnector` 从 OpenDAL 支持的存储后端捕获有界 UTF-8 文件。每个接受的文件都会成为不可变的
`text-file-snapshot` Source，保留 path、namespace、content digest 和后端能够提供的 annotation。

## 前置条件

OpenDAL 集成要求 Python 3.12 或更高版本。安装可选依赖：

```bash
uv add "powercontext[opendal]"
```

为存储位置选择稳定的 `source_namespace`。它用于区分来自不同 authority、但 path 和内容相同的文件。不要把凭据写进
namespace。

## 运行本地文件系统 binding

下面的 binding 扫描 `/absolute/path/to/project` 下的 `docs` 目录，并把 checkpoint 与捕获的 Source 持久化到同一个
PowerContext 数据库：

```python
import asyncio

from powercontext.builtin.connectors import OpenDALTextFileConnector
from powercontext.builtin.persistence.sqlite import SQLiteConfig
from powercontext.builtin.runtime import BuiltinConfig, open_builtin_contexts
from powercontext.sources import ConnectorBinding


async def main() -> None:
    connector = OpenDALTextFileConnector.from_service(
        "fs",
        source_namespace="project-docs",
        root="docs",
        storage_options={"root": "/absolute/path/to/project"},
    )
    binding = ConnectorBinding(
        scope_id="project:example",
        binding_id="project-docs",
        connector_name=connector.name,
        connector_version=connector.version,
    )
    config = BuiltinConfig(
        database=SQLiteConfig(url="sqlite+aiosqlite:///powercontext.db"),
    )

    async with open_builtin_contexts(config) as contexts:
        result = await contexts.run_connector(connector, binding)
        print(result.model_dump_json(indent=2))


asyncio.run(main())
```

访问远端存储时，换用对应的 OpenDAL service 及其 backend option。`storage_options` 只在运行期使用，不会复制进 Source
payload 或 checkpoint。

## 理解运行结果

每个 item outcome 有四种状态：

- `accepted`：Source 已持久化；
- `replayed`：sink 识别到已接受的 Source；
- `rejected`：provider item 无法满足 Source Definition，例如不是有效 UTF-8；
- `failed`：无法安全读取或存储该 item。

只有所有选中 item 都被接受且本轮完整结束后，checkpoint 才会前移。出现 rejected 或 failed item 时保留旧 checkpoint，
下一轮会安全重试扫描。digest 与已提交 checkpoint 相同的文件会被跳过。

接受的 Source 会进入同一 scope 的 Source journal。Runtime 配置了 Memory candidate pipeline 后，常规 source-window flush
或调度任务可以通过共享的 `powercontext.builtin.text-evidence` projection 消费这些 Source。Connector 完成采集并不直接创建
Memory。

## 当前限制

- 默认 pattern 选择 Markdown、纯文本、reStructuredText 和 AsciiDoc 文件。
- 除非显式调整，每轮最多选择 10,000 个文件，每个文件最多读取 2 MiB。
- 只接受 UTF-8 内容。
- 文件内容变化会生成新的精确 snapshot Source；旧 snapshot 仍然可读，以保留 lineage。
- 全量扫描会从下一 checkpoint 移除已消失的 path，但不会删除 Source，也不声明 authoritative deletion。
- Connector 不提供 change feed。需要通过周期运行观察后续变化。
