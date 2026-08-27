---
title: 使用 OpenDAL 采集文本文件
description: 用独立 OpenDAL Connector worker 把 UTF-8 文件捕获为类型化 Source。
---

# 使用 OpenDAL 采集文本文件

`powercontext-connector-opendal` 是独立于 PowerContext Server 部署的 worker。它拥有 OpenDAL credential、
provider configuration、可执行 Source Definition 和文件读取逻辑。Server 只保存声明式 Definition manifest、
已经物化的 Source observation、named projection 与 opaque checkpoint。

## 前置条件

该集成要求 Python 3.12 或更高版本。先启动 PowerContext Server，再从 checkout 安装 worker：

```bash
uv tool install ./integrations/opendal
```

选择稳定的 `source_namespace` 来区分不同 storage authority。不要把 credential 写进 namespace、Source payload 或
checkpoint。Server 启用 authentication 时，通过 `POWERCONTEXT_TOKEN` 环境变量提供 bearer token。

## 运行一个 binding

下面的独立进程扫描 `/absolute/path/to/project/docs`。`binding_id` 标识 checkpoint continuity，`scope_id` 决定
接受后的 Source 属于哪个 Scope：

```bash
powercontext-connector-opendal \
  --base-url http://127.0.0.1:8765 \
  --scope-id project:example \
  --binding-id project-docs \
  --service fs \
  --storage-option root=/absolute/path/to/project \
  --root docs \
  --source-namespace project-docs
```

访问远端存储时，替换 OpenDAL service 与对应的 `--storage-option KEY=VALUE`。这些 option 只存在于 worker 进程，
不会通过摄取 API 发送给 Server。

Worker 每次运行都会幂等注册 `text-file-snapshot` Definition manifest，读取 binding checkpoint，提交本轮变化的
Source observation，并在所有 durable receipt 返回后 compare-and-swap checkpoint。可以由 cron、Kubernetes Job
或其他外部 scheduler 周期执行该命令。

## 嵌入自定义 worker

需要自定义进程监管或多 binding 调度时，可直接使用通用远程 lifecycle：

```python
from powercontext.client import PowerContextClient, RemoteConnectorWorker
from powercontext.sources import ConnectorBinding, SourceDefinitionRegistry
from powercontext_connector_opendal import (
    TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,
    OpenDALTextFileConnector,
)

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
registry = SourceDefinitionRegistry((TEXT_FILE_SNAPSHOT_SOURCE_DEFINITION,))

async with PowerContextClient("http://127.0.0.1:8765") as client:
    result = await RemoteConnectorWorker(client=client, registry=registry).run(connector, binding)
```

## 运行语义

每个 item outcome 是 `accepted`、`replayed`、`rejected` 或 `failed`。只有本轮完整结束并且没有 rejected 或 failed
item 时 checkpoint 才会前移。否则保留旧 checkpoint，下一轮从同一位置安全重试。与已提交 checkpoint 中 digest
相同的文件会被跳过。

接受的 Source 进入目标 Scope 的 Source journal。Worker 同时计算标准 `powercontext.text-evidence` projection，
因此不了解 `text-file-snapshot` native schema 的 Memory consumer 仍可消费文本。Connector run 不直接创建 Memory；
Memory 仍由常规 source-window flush 或调度任务生成。

## 限制

- 默认选择 Markdown、纯文本、reStructuredText 与 AsciiDoc 文件。
- 默认每轮最多选择 10,000 个文件，每个文件最多读取 2 MiB。
- 只接受 UTF-8 内容。
- 内容变化会生成新的精确 snapshot Source，旧 snapshot 继续保留。
- 全量扫描会从下一 checkpoint 移除已消失 path，但不会删除 Source，也不声明 authoritative deletion。
- Connector 不提供 change feed；后续变化依赖外部 scheduler 再次运行 worker。
