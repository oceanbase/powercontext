---
title: 接口
description: 在 Codex 插件、DeepSeek Harness 插件、CLI、Python SDK、HTTP 和 MCP 之间选择。
---

# 接口

所有远程接口都操作同一个 Server 和同一份持久化 Artifact 存储。

| 接口 | 适用场景 | 安装 |
| --- | --- | --- |
| Codex 插件 | 在 Codex 中跨会话恢复和显式维护 Memory | `powercontext setup codex` |
| DeepSeek Harness 插件 | 在 DeepSeek Harness 中跨会话恢复和显式维护 Memory | `powercontext setup dsh` |
| CLI | 配置、诊断、Server 控制、能力检查和人工 Candidate 审核 | `powercontext[cli,server]` |
| Python Client SDK | 对运行中的 Server 发起类型化异步调用 | `powercontext[client]` |
| Core SDK | 进程内 Source、Artifact、Trigger 和组合契约 | 基础包 |
| HTTP | 从任意语言集成服务 | `powercontext[server]` |
| MCP | 面向 Agent 的 Memory 与 Candidate Review 工具 | 由 Server 启用 |

## Codex 插件

project-context skill 指导 Codex 何时检索、记忆、修订或停用 Memory。Prompt Hook 会恢复相关条目，并把
用户输入采集为 Source 证据；MCP 工具执行显式操作。插件不会启动或内嵌 Server。

## DeepSeek Harness 插件

project-context skill 指导 DeepSeek Harness 何时检索、记忆、修订或停用 Memory。每轮模型开口前，插件会恢复相关
条目，并把用户输入采集为 Source 证据；具名 `pc_*` 工具执行显式 HTTP 操作。插件不会启动或内嵌 Server。

## CLI

```text
powercontext setup codex
powercontext setup dsh
powercontext doctor
powercontext doctor codex
powercontext doctor dsh
powercontext server run
powercontext ready
powercontext capabilities
powercontext candidate list --scope-id project:example
powercontext candidate list --scope-id project:example --family skill
powercontext candidate show --scope-id project:example CANDIDATE_ID
powercontext candidate approve --scope-id project:example --expected-version 1 CANDIDATE_ID
powercontext candidate reject --scope-id project:example --expected-version 1 --reason unsupported CANDIDATE_ID
powercontext candidate revise experience --scope-id project:example --expected-version 1 \
  --situation SITUATION --action ACTION --outcome OUTCOME --lesson LESSON CANDIDATE_ID
powercontext candidate revise skill --scope-id project:example --expected-version 1 \
  --name NAME --description DESCRIPTION --instructions-file instructions.md --validation CHECK CANDIDATE_ID
powercontext experience generate --scope-id project:example --source-ref content/SOURCE_ID
powercontext skill generate --scope-id project:example --origin experience \
  --artifact-ref experience/EXPERIENCE_ID@REVISION
powercontext skill show --scope-id project:example --revision 1 SKILL_ID
powercontext skill export --target codex --scope-id project:example --revision 1 \
  --destination .agents/skills/example-skill SKILL_ID
powercontext external-skill scan --scope-id project:example
powercontext external-skill list --scope-id project:example
powercontext external-skill resolve --scope-id project:example --fingerprint SHA256 EXTERNAL_SKILL_ID
powercontext external-skill import --scope-id project:example --fingerprint SHA256 \
  --mode import EXTERNAL_SKILL_ID
```

所有内容命令都调用已配置的 Server。可选的 `server` role 会增加 `powercontext server run`，但不会在 CLI
中创建第二套内容 profile。

`powercontext doctor` 检查安装包和 Server，不要求任何集成；`powercontext doctor codex` 显式检查 Codex CLI
和 PowerContext 插件；`powercontext doctor dsh` 检查 DeepSeek Harness CLI，以及 dump-config 是否列出插件 id
`powercontext-dsh`。

Generation 和 revision 命令通过可重复的 `--source-ref TYPE/ID` 与
`--artifact-ref FAMILY/ID@REVISION` 接收精确引用，不再读取序列化请求文件。
`--target FAMILY/ID@REVISION` 会自动把 target 纳入 Artifact 证据。修订 managed Skill 时，内联
`--instructions` 和 `--instructions-file` 必须且只能选择一个，`--validation` 可以重复提供。

## Python Client SDK

由 Server 管理持久化时，使用 Client SDK：

```python
import asyncio

from powercontext.http import PrepareContextRequest, RememberMemoryRequest, SearchMemoryRequest
from powercontext.client import PowerContextClient


async def main() -> None:
    async with PowerContextClient("http://127.0.0.1:8000") as client:
        await client.remember_memory(
            RememberMemoryRequest(
                scope_id="project:example",
                kind="decision",
                text="保持公开 API 异步化。",
            )
        )
        result = await client.search_memory(
            SearchMemoryRequest(
                scope_id="project:example",
                query="公开 API",
            )
        )
        print([hit.text for hit in result.hits])
        prepared = await client.prepare_context(
            PrepareContextRequest(scope_id="project:example", query="公开 API")
        )
        print(prepared.content)


asyncio.run(main())
```

变更操作的响应包含精确 citation。修订、停用或读取不可变条目版本时，应把该 citation 传回 Server。

Client 还提供 `generate_experience`、`propose_experience`、`get_experience`、`generate_skill`、
`propose_skill`、`get_skill`、`scan_external_skills`、`list_external_skills`、
`resolve_external_skill`、`import_external_skill` 和 Candidate Review 方法。Review 写操作都要求
`expected_version`。批准响应返回精确的 Experience 或 managed Skill `result_artifact`；pending 和 rejected
Candidate 不是 Artifact Revision。

`generate_experience` 和 `generate_skill` 接收调用方显式选择的精确 Source 与 Artifact 引用，返回一个 pending
Candidate 或明确的 `no_op`。replacement 必须把精确 target 同时放入 `artifact_refs` 并设置 `target`。managed
Skill generation 还必须声明 provenance 形态：

- `experience`：至少引用一个已批准的 Experience，也可以附带精确 Source；
- `source`：只引用精确 Source，包括官方资料或人工材料；
- `usage`：引用精确 target Skill 和有界 usage Source。

这些 generation operation 需要配置 `POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL`。已经拥有完整类型化内容和
精确证据的人或 integration 仍可使用低阶 `propose_*` operation。两条路径都不能自行批准 Candidate。
Experience 经 Review 批准后，确定性的 `searchable_text` 会写入现有通用 Artifact head 并进入 backend 可重建 FTS
索引，从而可在同一 scope 内被 `PreparedContext` 召回。pending/rejected Candidate、所有 managed Skill 和历史
Experience Revision 仍不会进入 PreparedContext。

## 后台 Experience 孵化

Integration 可以把已完成任务采集为 metadata 含 `"kind": "task-outcome"` 的 Content Source。启用
Experience schedule 后，APScheduler 会扫描有上限的 Source window，并让配置好的 schema-bound pipeline
生成可复用的 situation、action、outcome 和 lesson。每条 proposal 都引用精确 Source，并以 pending
Experience Candidate 进入 Review Inbox。

Experience 孵化使用独立于 Memory extraction 的持久化 Source cursor。Candidate 写入和 cursor 推进会在同一
事务提交；generation 或写入失败时，该 window 保留给下次重试。普通 Prompt Source 不是 Task Outcome，
不会进入这个 job。

后台流程止于审核边界：它不会批准 Experience、把 pending 内容放入 PreparedContext、派生 managed Skill、
把 Skill 导出给 Codex，或执行 instructions。只有支撑它的 Experience 获批后，Skill authoring 和导出才作为
显式步骤继续。

## 把 managed Skill 导出给 Codex

配置好的生成器可通过 `generate_skill` 生成完整 managed Skill；已经拥有完整类型化内容的人或 integration
可通过 `propose_skill` 提交。proposal 包括名称、用于发现的描述、instructions、validation，以及精确的
Source 或 Artifact lineage。在 reviewer 批准精确 Candidate version 之前，它始终只是 Candidate。

批准会创建不可变的 Skill Revision，但不会安装 Skill，也不会授予执行权限。要让 Codex 使用某个已批准
Revision，必须通过 `skill export --target codex` 将它显式导出到新的代码库级或用户级 Skill 目录。该命令生成
`SKILL.md` 和 `powercontext.json`；manifest 会记录精确 Artifact 引用和渲染内容哈希。目标目录已存在时命令会
拒绝覆盖，更新必须是一次明确的新导出，不能静默替换。

Codex 可以发现 `.agents/skills/<name>/SKILL.md` 下的代码库级导出。Artifact Revision 始终是内容权威，目录
只是 host-local projection，可以从同一个精确 Revision 重建。

## 外部 Agent-native Skill

外部 Skill 的原始本地 package 始终是内容权威。显式配置 Codex roots 后，Server 可以扫描 scope-local、
可重建的 Registry，并记录名称、描述、provider、Agent kind、host、installation scope、locator 和整个 package
的 fingerprint。只有同一 package 在已配置 host 上仍可读且 fingerprint 一致时，exact resolve 才成功；它不会
安装 package，也不会回退到其他版本。

Discovery 不进入 Review。显式调用 `import_external_skill` 并提供精确 identity 与 fingerprint 后，Runtime
才会把有界 `SKILL.md` 快照采集为 Source evidence，并让已配置模型提出新的 managed Skill Candidate。
`mode=import` 与 `mode=fork` 记录调用方意图；两者都必须经 Review 批准后才产生新的 managed identity，且不会
修改 external registration。package 中的脚本和 assets 不会复制进 managed Artifact。

## Authority 与门禁

| Surface | 内容权威 | 模型门禁 | Review 门禁 | 当前可用方式 |
| --- | --- | --- | --- | --- |
| 外部 Agent-native Skill | 原始 package | scan/list/resolve 不需要；import/fork 需要 | discovery 不需要；import/fork 后需要 | host-local Registry 和 exact resolve |
| Experience | 精确 approved Artifact Revision | generate/evolve 需要；类型化 `propose` 不需要 | 需要 | exact read 与 PreparedContext approved-head FTS recall |
| managed Skill | 精确 approved Artifact Revision | generate/evolve/import/fork 需要；类型化 `propose` 不需要 | 需要 | exact read 与显式 Codex projection |
| Codex projection | 对应的 managed Skill Revision | 不需要 | 不增加额外 Review | 可重建的 host-local copy |

## Core SDK

基础 `powercontext` 包为自行管理 composition root 的应用导出 Python 协议和模型。它不会替应用选择存储、
调度、传输或推理。需要在同一进程使用随附的 SQLite 或 OceanBase 实现时，安装 `builtin`。

## HTTP 和 MCP

Server 在 `/openapi.json` 提供 OpenAPI 文档，在 `/health/ready` 提供就绪检查，在 `/v1/capabilities`
提供能力信息，并默认在 `/mcp` 提供 Streamable HTTP MCP。HTTP 是完整应用契约，MCP 是面向 Agent 的
Memory 与 Candidate Review operation 子集。五个 Candidate Review operation 通过 HTTP 和 MCP 使用相同的
validation、`expected_version` 并发校验和 approval transaction。Experience/Skill generation、exact read、
external Registry operation 和低阶 proposal operation 仍只通过 HTTP 提供。
所有检查通过时 readiness 为 HTTP 200 的 `ready`；只有已配置的推理检查失败时为 HTTP 200 的 `degraded`；
Runtime 或数据库失败时为 HTTP 503 的 `not_ready`。依赖检查使用 `ready`、`unavailable`、`timeout` 或
`misconfigured`；有意不绑定 Runtime 时，`runtime` 检查使用 `not_ready`。
`POST /v1/context/prepare` 及对应的 Python Client method 通过 HTTP 提供最终的临时 `PreparedContext`；
Runtime 召回 active Memory 与 approved Experience head，统一负责选择和总输出预算；该 operation 不会投影为
MCP tool。public schema 仍是 `powercontext.prepared-context.v1`，Experience item 在 prepared content 内携带精确
Artifact 引用。
