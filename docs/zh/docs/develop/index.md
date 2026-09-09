---
title: 开发与 API
description: 通过 HTTP API、Python Client 或 Core SDK 接入应用。
---

# 开发与 API

通过 [API Quick Start](api-quickstart.md)连接应用、采集证据、保存 Memory，并为一次请求准备上下文。
该流程使用本地 Server，显式写入不需要模型。

按应用选择[接口](interfaces.md)：异步 Python Client 和 HTTP API 连接运行中的 Server，
Core SDK 支持进程内组合。MCP 向 Agent 暴露经过选择的工具子集。

- [HTTP 行为](http-api.md)：认证、请求 ID、错误与并发。
- [HTTP API](/api)：生成的端点与结构。
- [Python API](/zh/modules)：生成的 Python 文档。
- [Python Agent 框架](../integrations/index.md#python-agent-框架)：遵循各自生命周期的社区适配器。
