- Proposal Name: `explainable_prepared_context_receipts`
- Start Date: 2026-09-02
- RFC PR: [oceanbase/powercontext#0000](https://github.com/oceanbase/powercontext/pull/0000)
- Tracking Issue: [oceanbase/powercontext#1356](https://github.com/oceanbase/powercontext/issues/1356)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、[RFC 0028](0028_context_pack.md)、
  [RFC 0046](0046_observability_foundations.md)、[RFC 0080](0080_memory_search_reranking.md)

# Summary

本 RFC 为请求时召回增加可选、有界的 PreparedContext Receipt。现有 `powercontext.prepared-context.v1` 注入值仍只有
`status`、`content` 和 `content_bytes`。选择加入的调用方会得到一份伴随 Receipt，说明 Runtime 选中了什么、省略了什么、
使用了哪条检索路径、消耗了多少 byte 预算，且不保留 query 原文或 Memory/Experience 正文。

首个策略为 `powercontext.prepared-context-receipt.v1`。Receipt 是附着在一次 `prepare` 响应上的短暂诊断，不是
Artifact，没有 Revision，默认不持久化，也不是事实的第二权威。OpenTelemetry span 仍负责耗时与结果；Receipt 是一份
注入字节串的精确选择契约。

# Motivation

`POST /v1/context/prepare` 已经负责选择、引用、渲染和 UTF-8 预算。Runtime 在 `PreparedContextBuild` 上保留精确
origins，并在 trace 上记录 search/build 阶段属性，但两者都在公开边界被丢弃。集成和运维因此只能看到一段不透明的
注入字符串。

这个缺口会挡住三类产品用途：

- 运维无法区分 Agent 是拿到了决策、撞上预算，还是因为别的原因得到空结果。
- 评测无法在注入文本之外，单独给精确引用、省略类别或检索回退打分。
- 多分辨率装箱等后续工作，只有在存在稳定、无正文的选择记录之后，才能比较策略。

Span 回答的是“哪一阶段跑了、花了多久”。它们不能作为公开 API 携带精确 Memory 引用，也不应膨胀成选择 schema。
Memory 搜索的进程内 rerank trace 也不是正确表面：它描述一次搜索，而不是宿主实际注入的、经过交错和预算裁剪的
PreparedContext。

没有 Receipt，每个宿主或 benchmark 都会自己发明召回解释。那种解释要么泄漏内容，要么和真正注入的字节对不上。

# Guide-level explanation

## 请求一份 Receipt

默认 `prepare` 请求不变：

```python
prepared = await client.prepare_context(
    PrepareContextRequest(scope_id="project:payments", query="Why did we choose SQLite?", max_bytes=8000)
)
```

响应仍是 `powercontext.prepared-context.v1`。注入 `content` 的宿主保持现有解析。

需要解释同一次结果的调用方将 `include_receipt` 设为 true：

```python
prepared = await client.prepare_context(
    PrepareContextRequest(
        scope_id="project:payments",
        query="Why did we choose SQLite?",
        max_bytes=8000,
        include_receipt=True,
    )
)
```

Runtime 生成 ready 上下文时，响应仍包含注入字符串，并附加 Receipt。Receipt 列出精确选中引用，按封闭枚举分组省略
候选，记录实际使用的检索模式，并对注入字节做哈希。它不会重复 query 或被选中条目的正文。

请求 Receipt 时，空结果也会带 Receipt。空 Receipt 仍然报告 query digest、预算、检索路径和省略计数，从而区分
“没有 Memory”和“全部超出预算”。

自动宿主召回不得设置 `include_receipt`。官方 Pi、DSH、OpenCode、Codex、Claude Code 和 WorkBuddy 校验器当前要求注入
对象恰好包含 `schema`、`status`、`content` 和 `content_bytes`。默认或宿主召回带上 Receipt 会被当成非法响应，fail-open
且不注入上下文。运维、评测 harness 和后续 CLI 在另一次 prepare 上请求 Receipt，或使用已选择加入该字段的更新校验器。

## 把 Receipt 当作不可信元数据

Receipt 只证明 PowerContext 在该策略和预算下渲染了这些精确引用。它不证明历史内容当前为真，也不能压过 system、
developer、仓库或当前用户指令。宿主不得把 Receipt JSON 注入模型 prompt。注入值仍然是 `content`。

PreparedContext Receipt 不是 Handoff Receipt。后者是 Work Continuity 对精确 Handoff Revision 的确认；前者解释一次
短暂召回。

## 不增加新的内容 API 也能逐级查看

紧凑 Receipt 是第一层披露。更细的查看复用现有精确读取：

1. Receipt：选中引用、省略计数、检索路径、digest、预算。
2. 按引用身份精确读取 Memory 或 Experience。
3. 在调用方有权读取时，查看该 Artifact 上已有的精确 Source 证据。

Receipt 不为以后展开而缓存条目正文。需要正文时，通过普通 Memory/Experience API 加载当前精确身份。若该身份已被
retire，精确读取失败就是解释；Receipt 不是时光机。

## 失败保持 fail-open

组装 Receipt 不得改变或阻断注入。`include_receipt` 为 true 但构造失败时，Server 仍返回未加该标志时本应返回的
PreparedContext，省略 `receipt`，并记录无正文诊断。忽略新字段的集成继续可用。

# Reference-level explanation

## 公开请求

`PrepareContextRequest` 增加一个可选字段：

| 字段 | 默认 | 契约 |
| --- | ---: | --- |
| `include_receipt` | `false` | 为 true 时，Runtime 尝试在本次响应上附加 `powercontext.prepared-context-receipt.v1`。 |

省略、null 和 false 等价。现有客户端既不发送该字段，也不解析 Receipt。v1 不提供把每次 prepare 都带上 Receipt 的
Server 部署默认值。

`query`、`scope_id` 和 `max_bytes` 保持现有边界。`query_digest` 对 Memory 搜索已使用的同一规范化 query 做哈希
（`normalize_text`：trimmed query 的 NFC Unicode，UTF-8）。Receipt 只保存 `sha256:<hex>`。

## 公开响应

`PreparedContext` 保留 `schema`、`status`、`content` 和 `content_bytes`，并增加：

| 字段 | 出现条件 | 契约 |
| --- | --- | --- |
| `receipt` | 仅在请求且成功构造时出现 | `PreparedContextReceipt` |

当 `include_receipt` 为 false、null 或省略时，JSON 对象不得包含 `receipt` 键。`null` 不等于省略。官方宿主校验器和
OpenAPI `additionalProperties: false` 会拒绝未知键，包括 `"receipt": null`。

注入 schema 名称仍为 `powercontext.prepared-context.v1`。因此默认 prepare 响应仍是四字段对象。设置
`include_receipt` 为 true 的调用方必须解析可选的 `receipt` 字段；官方生成客户端在实现 PR 中再生。宿主召回插件在
明确选择加入之前，保持恰好四字段的校验。

## Receipt schema

策略 ID：`powercontext.prepared-context-receipt.v1`。

```text
PreparedContextReceipt
  schema: powercontext.prepared-context-receipt.v1
  receipt_id: opaque UUID
  policy_id: powercontext.prepared-context-receipt.v1
  query_digest: 规范化 query 的 sha256 hex
  content_digest: 注入 UTF-8 内容的 sha256 hex；status=empty 时为 null
  requested_max_bytes: integer
  used_bytes: integer，等于 content_bytes
  truncated: 任一选中条目被按大小截断时为 true
  retrieval:
    memory_mode: auto | fts | vector | hybrid | none
    rerank_policy_id: string | null
    rerank_fallback: boolean
    experience_configured: boolean
  selected: [SelectedItem]  # schema 最多 16；当前 Builder 最多产出 8
  omitted: [OmittedGroup]   # 最多 16 组
  stages: [StageTiming]     # memory.search, experience.search, context.build
```

`receipt_id` 用于在日志中与 HTTP `X-PowerContext-Request-ID` 关联。它不是 Artifact ID，v1 也不得把它当作可持久
获取的键。

`SelectedItem`：

| 字段 | 契约 |
| --- | --- |
| `kind` | `memory` 或 `experience` |
| `memory_citation` 或 `artifact_ref` | Builder 已接纳的精确身份 |
| `rendered_bytes` | 该条目渲染片段的 UTF-8 大小，不是源正文 |
| `truncated` | Builder 是否为适配预算截断了该条目 |

选中条目按注入顺序排列。选中身份集合必须等于 `PreparedContextBuild.origins`。若 Receipt 的选中引用与注入 origins
不一致，则该 Receipt 无效，不得返回；Server 走 Receipt 组装失败路径。

OpenAPI 数组上限为 16，避免以后调整 Builder 时立刻改 schema。当前 Coding Agent Builder 最多接纳 8 条 Memory 和
2 条 Experience，Memory 优先交错，注入列表上限为 8。v1 Receipt 列出的就是这份注入列表，不是超集。

`OmittedGroup`：

| 字段 | 契约 |
| --- | --- |
| `reason` | 下列封闭枚举 |
| `count` | 被丢掉的候选身份数；空集标志固定为 `1` |

封闭 `reason` 值：

| 原因 | 含义 |
| --- | --- |
| `duplicate` | 同一 Memory entry version 或 Experience revision 已被接纳 |
| `blank` | 空身份或无可渲染文本 |
| `family_limit` | 超过 Builder 的 Memory 或 Experience 接纳上限 |
| `entry_limit` | 超过合计注入条目上限 |
| `over_budget` | 即使按最小截断大小也无法放入 |
| `rerank_not_selected` | 出现在粗排 Memory 池中，在进入 Builder 前被 listwise rerank 丢掉 |
| `memory_not_retrieved` | 没有 Memory head，或 Memory 搜索返回零命中 |
| `experience_not_retrieved` | 未配置 Experience 召回，或召回返回零命中 |

`memory_not_retrieved` 和 `experience_not_retrieved` 是空集标志，不是对 Artifact 的扫描。各自最多出现一次，`count`
固定为 `1`。该来源已经产生非空候选列表时省略这组，即使这些候选后来因其他原因被丢掉。其他原因统计到达 Builder、
或在接纳前立即排除的候选身份数。省略计数从不试图统计整个 Memory Artifact。

`StageTiming` 记录 `memory.search`、`experience.search` 和 `context.build` 的毫秒耗时。缺失阶段省略。阶段名与现有
Runtime span 名一致，以便在不复制 span 载荷的情况下与 trace 关联。

Rerank：当 Memory 搜索产生 `MemoryRerankTrace` 时，Receipt 只复制 `policy_id` 和 `used_fallback`。不复制 candidate
hits、selected ranks、usage 或任何 Memory 文本。

## 边界

| 限制 | 取值 |
| ---: | ---: |
| `selected` | schema 16 条；当前 Builder 输出最多 8 条 |
| `omitted` 分组 | 16 |
| `stages` | 8 |
| `receipt` JSON UTF-8 大小 | 8192 bytes |
| 条目正文、query 原文、prompt、向量、密钥、token、绝对路径 | 禁止 |

若一份合法 Receipt 将超过 8192 bytes，Server 丢弃 Receipt，而不是截断选中身份。截断后的身份列表会与注入字节不一致。

## 持久化与 MCP

v1 不持久化 Receipt，也不新增按 `receipt_id` 获取的操作。需要耐久记录的评测在 harness 中保存响应本身。以后带 TTL
的可选存储需要单独 RFC。

`prepare_context` 仍不进入默认 MCP 工具面。Receipt 不是把 prepare 投影成 Agent 工具的理由。

## CLI

Client SDK 在现有 prepare 操作上暴露 `include_receipt`。后续 CLI（例如
`powercontext context prepare --include-receipt`）可以把 Receipt 打成 JSON。那是本 RFC 之后的实现工作；不得把
Receipt 注入 Agent prompt。

## 兼容性

| 表面 | 变化 |
| --- | --- |
| 默认 `prepare` | 仍是四字段 JSON 对象；没有 `receipt` 键 |
| OpenAPI `PrepareContextRequest` | 可选 `include_receipt` |
| OpenAPI `PreparedContext` | 可选 `receipt`，仅在请求且成功构造时出现 |
| SQLite / OceanBase | v1 无 schema 变更 |
| 宿主召回插件 | 只要不发送 `include_receipt` 就无需修改 |
| 宿主校验器 | 默认路径上恰好四字段的检查仍然有效 |
| Tracing | 不要求新 span；现有阶段名复用到 `stages` |

生成的 Python、DSH、Pi、OpenCode operation 表在实现 PR 中再生。自动召回必须保持当前请求形状。宿主若要记录
Receipt，在同一改动里更新校验器并设置 `include_receipt`。

## 实现要点

1. 在 OpenAPI 中扩展 `PrepareContextRequest` 和 `PreparedContext`，再生成绑定。
2. 继续以 `PreparedContextBuilder.build_result()` 作为选中条目来源。在 Builder 已遍历的同一候选列表上统计省略身份。
3. 从 `ScopedContextApplication._prepare` 已得到的 Memory 搜索结果复制检索模式和 rerank 摘要。
4. 在 Builder 返回后对 Memory 搜索已规范化的 query 和注入 `content` 做哈希。
5. Receipt 校验失败时，记录无正文错误，并返回没有 `receipt` 键的四字段 PreparedContext。
6. 官方宿主召回请求保持不变。只有在该宿主选择加入 Receipt 的同一改动里，才更新其校验器。

聚焦测试覆盖 empty、ready、truncated、deduplicated、reranked、fallback、`include_receipt=false`（没有 `receipt`
键）、`memory_not_retrieved` / `experience_not_retrieved` 标志，以及 Receipt 组装失败。每个 ready 用例断言
`content_digest` 与返回的 `content` 一致，且选中引用与 `origins` 一致。宿主召回夹具继续断言响应恰好四个键。

# Drawbacks

- 可选字段仍会扩大 OpenAPI 模型和所有生成客户端，即使多数宿主从不请求 Receipt。
- 省略计数是已接纳候选池的摘要，不是整个 Memory Artifact 的摘要，可能被误读成“考虑了项目的多大一部分”。
- 调用方仍可能不顾信任规则把 Receipt JSON 注入 prompt。
- `receipt_id` 看起来像耐久标识，但 v1 无法稍后获取。

# Rationale and alternatives

**在 `prepare` 上选择加入字段，而不是第二个操作。** 单独的 `POST /v1/context/explain` 要么重新跑选择并与注入字节
不一致，要么要求 Server 记住上一次 prepare。前者不正确，后者就是持久化。把 Receipt 附在同一响应上，保证一次选择、
一个 digest、无存储。

**不要默认把诊断放到 PreparedContext v1。** 每个宿主都会在热路径下载选择元数据。fail-open 注入器会开始依赖更大的
schema。

**不要把 OTel span 当作公开契约。** Span 会被采样、依赖导出器，并且必须保持无正文。它们不能作为受支持 API 携带
精确 Memory 引用。

**不要复用 Memory 搜索的 rerank trace。** 该 trace 包含 candidate hits，且仅在进程内。HTTP 搜索已经不返回它。
PreparedContext 选择发生在 Memory 搜索和 Experience 搜索之后，并使用不同预算。

**不要持久化每一次 prepare。** 请求时召回会变成用户 query 的无界历史。评测可以在 harness 中保留 HTTP 响应。

**不要发明渐进内容缓存。** 通过现有精确读取 API 展开 Receipt 条目，能保持 Artifact 权威。prepare 侧按短暂 id 缓存
正文会成为 Memory 文本的第二存储。

不做的影响：宿主和 benchmark 会继续从注入文本或私有日志反推召回，后续装箱实验也没有共享的省略词表。

# Prior art

PowerContext 已有四种相关但不同的记录：

- `PreparedContextBuild.origins` 是精确选中集合，在 HTTP 边界被丢弃。
- Runtime 的 `memory.search`、`experience.search`、`context.build` span 记录计数和模式，不记录引用。
- `MemoryRerankTrace` 解释进程内的 listwise Memory 搜索。
- Handoff Receipt 在 Work Continuity 中确认精确 Handoff Revision。

RFC 0028 已经要求注入路径带引用和预算，但没有暴露选择记录。RFC 0046 禁止把 Memory 正文和 query 原文放进
trace。RFC 0080 把 rerank 诊断留在 HTTP 搜索契约之外。

在 PowerContext 之外，检索系统常常随答案返回 hit ID 和分数。本 RFC 返回精确 PowerContext 身份和封闭省略原因，
并拒绝分数与正文，避免诊断变成另一段 prompt。

# Unresolved questions

- 以后的评测配置是否应在显式 TTL 下持久化 Receipt，还是 harness 侧存储就够？
- Dashboard 查看属于第一个实现 Issue，还是只做 CLI/SDK？

v1 保持 `include_receipt` 仅由请求开启。把 Receipt 附到每一次 prepare 的 Server 默认值会破坏当前宿主校验器，不在
本 RFC 范围。

其余问题不阻止接受上面的 v1 契约。它们属于实现 Issue 或后续 RFC。

# Future possibilities

- 从带 Receipt 的 prepare 响应渲染选中引用和省略计数的 Context Inspector UI。
- 把 Receipt 省略原因与任务分数拼接的评测报告。
- 多分辨率装箱（[#1426](https://github.com/oceanbase/powercontext/issues/1426)）通过同一套 Receipt `omitted` 词表
  报告选中层级。
- 面向审计的可选耐久 Receipt 存储，只保留 query digest 和短 TTL。
- 召回为空或被截断时，宿主记录 `receipt_id` 和 `content_digest` 的无正文诊断。
