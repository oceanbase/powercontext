---
title: 输出标准上下文文本
description: 选择 Memory、Experience、Profile 和 Topic Memory 的输出类别、顺序、条数，以及展示的元数据。
---

# 输出标准上下文文本

在 `POST /v1/context/prepare` 中传入 `assembly`，即可获得按制品类别组织的 Markdown。可以选择 Memory、
已批准的 Experience、正式 Profile 快照和 Topic Memory，调整章节顺序、限制条数，并展示召回位置和置信度状态。请使用已存在且具有读取权限的
Scope；读取其引用的其他 Scope 也需要对应权限。

## 选择类别和顺序

把下面的请求保存为 `prepare.json`，将 `scope_id` 替换成实际 Scope ID：

```json
{
  "scope_id": "project:demo",
  "query": "修改 OpenAPI 后，如何修复客户端契约不一致的问题？",
  "max_bytes": 8000,
  "assembly": {
    "format": "markdown",
    "sections": [
      {"family": "experience", "limit": 2},
      {"family": "memory", "limit": 5}
    ],
    "show": ["confidence", "recall_rank"]
  }
}
```

从未启用鉴权的本地 Server 导出实际文本。先写入临时文件，确认响应成功后再替换目标文件；这样 HTTP 错误不会被 `jq`
误报为空结果，也不会先清空已有的 `context.md`：

```bash
set -euo pipefail
tmp_context="$(mktemp "${TMPDIR:-/tmp}/powercontext-context.XXXXXX")"
trap 'rm -f "$tmp_context"' EXIT
curl --fail-with-body -sS http://127.0.0.1:8000/v1/context/prepare \
  -H 'Content-Type: application/json' --data-binary @prepare.json \
  | jq -er 'if .status == "empty" then "" elif .status == "ready" and (.content | type) == "string" then .content else error("unexpected prepare response") end' \
  > "$tmp_context"
mv "$tmp_context" context.md
```

未启用鉴权时使用上面的请求；启用鉴权时为 `curl` 增加与其他 API 请求相同的
`--header "$POWERCONTEXT_AUTH_HEADER"`。`status: "empty"` 是成功的空结果，会生成空文件；HTTP 4xx/5xx 或响应结构错误
会保留原有文件并返回非零退出码。

HTTP 外层仍然是 `schema`、`status`、
`content`、`content_bytes` 四个字段。直接写出或注入 `content` 即可：它已经包含历史证据提示、章节标题、按字面
展示的正文、精确引用和截断标记。空结果为 `status: "empty"`、`content: null`、`content_bytes: 0`。

Python Client 的等价调用：

```python
import asyncio
import json
from pathlib import Path

from powercontext.client import PowerContextClient
from powercontext.http import PrepareContextRequest

async def export_context() -> None:
    request = PrepareContextRequest.model_validate(json.loads(Path("prepare.json").read_text(encoding="utf-8")))
    async with PowerContextClient("http://127.0.0.1:8000") as client:
        prepared = await client.prepare_context(request)
    Path("context.md").write_text(prepared.content or "", encoding="utf-8")

asyncio.run(export_context())
```

## 设置输出策略

| 配置 | 行为 |
| --- | --- |
| 省略 `assembly` | 保持原有输出和选择行为。 |
| `"assembly": {}` | Markdown，先 Memory 最多 6 条，再 Experience 最多 2 条，不显示可选元数据。 |
| `"assembly": {"sections": []}` | 完成请求和当前 Scope 检查后返回空结果，不召回候选。 |
| 只配置 `memory` | 只召回 Memory，limit 为 1–8。 |
| 只配置 `experience` | 只召回已批准的 Experience，limit 为 1–2。 |
| 只配置 `profile` | 读取所选 Scope 的最新正式画像快照，limit 为 1–8。 |
| 仅配置 `topic-memory` section | 在当前 Scope 检索 Topic Memory；limit 为 1–8。 |
| 配置两到四个 section | 数组顺序决定展示顺序和字节预算优先级；limit 之和不得超过配置的 `context_assembly_max_entries`（默认 8）。 |
| `show: ["recall_rank"]` | 展示条目在该类别去重后候选列表中的位置。 |
| `show: ["confidence"]` | 显示 `unknown (not assessed)`，目前没有评估数字置信度。 |

省略 `assembly` 的默认 prepare 请求还会召回当前 Scope 中可用的 Topic Memory。
显式 `assembly` 通过选择 `topic-memory` 包含主题记忆；`assembly: {}` 仍只选择 Memory 和 Experience。

同一类别内保留召回顺序，包括已有 Memory reranker 的排序。前面的候选因预算无法装入时，rank 可能不连续。
被排除的类别不会参与召回。重复类别、非法 limit、`sort_by` 或 `min_confidence` 等不支持的字段，以及显式
`assembly: null`，都会返回 HTTP 422。

`max_bytes` 限制 Server 完整文本的 UTF-8 字节数，范围为 512–32768，默认 8000。每条正文上限为 2000 bytes。
空间不足时，Server 会缩短正文或跳过条目，同时保留完整引用与边界，因此实际输出可能少于请求条数。
接入端应校验外层结构和预算，原样使用正文，不应二次裁剪；可以在正文外添加自己的提示。

## 配置组合条数上限

在服务端环境变量中设置以下值，重启服务后即可放宽组合总量：

```dotenv
POWERCONTEXT_SERVER_RUNTIME_CONTEXT_ASSEMBLY_MAX_ENTRIES=16
```

该值必须是正整数，默认 8。嵌入式 Runtime 可以通过
`BuiltinConfig(runtime=RuntimeConfig(context_assembly_max_entries=16))` 配置。
实际接收请求的 Runtime 会在召回前检查 limit 总和，超出配置值时返回 HTTP 422。
各类别单独上限仍为 Memory、Profile、Topic Memory 各 8 条，Experience 2 条，所以四类合计最多可请求 26 条。
调高组合上限不会扩大各类别召回池或字节预算。若将配置调低到 8 以下，需要显式提供总和符合要求的章节配置：
`assembly: {}` 仍表示 Memory 6 加 Experience 2，总和超过配置值时会被拒绝；`sections: []` 仍然有效。
省略 `assembly` 的请求保持原有选择行为和最多 8 条的限制。

## 加入 Profile 画像

显式选择 Profile，可以先输出当前 Scope 的画像，再输出任务相关记忆：

```json
{
  "sections": [
    {"family": "profile", "limit": 1},
    {"family": "memory", "limit": 6}
  ]
}
```

将此对象放入 `assembly` 或插件的上下文组装配置。省略 `assembly` 或传入 `{}` 均不包含 Profile。
每条 Profile 是一个 Scope 的完整画像快照，再按单条正文和总字节预算截断；`limit` 统计快照数量，
不统计画像中的偏好条数。没有正式画像的 Scope 不贡献条目；`limit: 1` 选择“当前 Scope、直接 Context
References”顺序中的首个可用快照。

服务端读取每个所选 Scope 的 `family=profile, artifact_id=profile` 最新正式 Revision，不触发画像生成，
不按 `query` 搜索画像，也不包含待审或已拒绝 Candidate。不遍历间接引用或主体绑定；所有引用 Scope 都需要
读取权限。输出保留精确 Revision 引用，正文截断时显示 `Truncated: yes`。`recall_rank` 仅表示 Profile
候选列表中的位置，不代表相关度或置信度。

## 组合 Topic Memory 和 Profile

使用以下 `assembly`，将画像偏好放在相关主题和其他证据之前：

```json
{
  "sections": [
    {"family": "profile", "limit": 1},
    {"family": "topic-memory", "limit": 2},
    {"family": "memory", "limit": 3},
    {"family": "experience", "limit": 2}
  ]
}
```

Topic Memory 沿用基于 `query` 的检索，只搜索当前 Scope，不遍历 Context References，也不在 prepare 时生成新主题。
每条输出包含标题、摘要、可选命中片段，以及 Scope 和精确 Artifact Revision。完整详情可用该引用通过
`POST /v1/topic-memory/get` 读取。章节内保留检索顺序，条数限制、可选元数据和 UTF-8 字节预算同样适用。

## 为插件的自动召回启用配置

以 Codex 为例，在新会话启动前设置 JSON 对象：

```bash
export POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY='{"sections":[{"family":"experience","limit":2},{"family":"memory","limit":5}],"show":["confidence","recall_rank"]}'
codex
```

各接入端的配置入口如下，值使用同一个组装对象：

| 接入端 | 配置入口 |
| --- | --- |
| Codex | `POWERCONTEXT_CODEX_CONTEXT_ASSEMBLY` |
| Claude Code | `POWERCONTEXT_CLAUDE_CONTEXT_ASSEMBLY` |
| WorkBuddy | `POWERCONTEXT_WORKBUDDY_CONTEXT_ASSEMBLY` |
| DeepSeek Harness | `POWERCONTEXT_DSH_CONTEXT_ASSEMBLY` 或插件 `contextAssembly` |
| OpenCode | `POWERCONTEXT_OPENCODE_CONTEXT_ASSEMBLY` |
| Pi | `POWERCONTEXT_PI_CONTEXT_ASSEMBLY` |
| Hermes | `POWERCONTEXT_HERMES_CONTEXT_ASSEMBLY` 或 Provider `context_assembly` |
| LangChain | `POWERCONTEXT_LANGCHAIN_CONTEXT_ASSEMBLY` 或 settings `context_assembly` |
| LangGraph | `POWERCONTEXT_LANGGRAPH_CONTEXT_ASSEMBLY` 或 settings `context_assembly` |
| Pydantic AI | `POWERCONTEXT_PYDANTIC_AI_CONTEXT_ASSEMBLY` 或 settings `context_assembly` |
| Bub | `POWERCONTEXT_BUB_CONTEXT_ASSEMBLY` 或 settings `context_assembly` |
| OpenClaw | `plugins.entries.memory-powercontext.config.contextAssembly` 对象 |

环境变量使用 JSON 字符串，对象配置使用等价对象。移除配置即可恢复原有输出。启用前需要升级 Server 和对应
接入端；旧 Server 会拒绝 `assembly`，插件通过已有诊断报告失败，不会自动扩大选择范围重试。

LangChain、LangGraph 和 Pydantic AI 适配器在未设置 `context_assembly` 时，可以搭配 `powercontext==0.2.0`
使用原有召回。启用该配置需要支持文本组装的 core Client 和 Server；旧 Client 会明确报配置校验错误。
在包含该功能的仓库检出目录下，同时安装 core 和所使用的适配器，选择对应命令：

```bash
uv pip install ".[client]" ./integrations/langchain
uv pip install ".[client]" ./integrations/langgraph
uv pip install ".[client]" ./integrations/pydantic-ai
```

Hermes 的自动召回、`powercontext_prepare_context` 工具和 `/pc call prepare_context` 都使用组装配置。
手动请求显式提供的参数优先于配置默认值。

LangGraph 和 Hermes 的缓存同时区分组装配置和字节预算，切换策略后不会复用其他选择或预算下的结果。
该功能不新增数据库表或 HTTP 接口。
