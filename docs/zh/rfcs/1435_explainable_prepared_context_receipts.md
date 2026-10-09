---
title: "RFC 1435：可解释的 PreparedContext Receipt"
---

- Proposal Name: `explainable_prepared_context_receipts`
- Start Date: 2026-09-02
- RFC PR: [oceanbase/powercontext#1435](https://github.com/oceanbase/powercontext/pull/1435)
- Tracking Issue: [oceanbase/powercontext#1356](https://github.com/oceanbase/powercontext/issues/1356)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、[RFC 0028](0028_context_pack.md)、
  [RFC 0046](0046_observability_foundations.md)、[RFC 0080](0080_memory_search_reranking.md)

# Summary

本 RFC 为请求时召回增加可选、有界的 PreparedContext Receipt。现有 `powercontext.prepared-context.v1` 注入值仍只有
`status`、`content` 和 `content_bytes`。选择加入的调用方会得到一份伴随 Receipt，说明 Runtime 选中了什么、省略了什么、
使用了哪条检索路径、消耗了多少 byte 预算，且不保留 query 原文或被选中内容的正文。

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
2. 按带 Scope 的身份精确读取 Memory entry、Topic Memory、Experience 或 Profile；代码则按 fingerprint、
   仓库相对路径、文件哈希和行范围读取。
3. 在调用方有权读取时，查看该 Artifact 上已有的精确 Source 证据。

Receipt 不为以后展开而缓存条目正文。需要正文时，通过普通 Artifact/code API 加载当前精确身份。若该身份已被
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
  query_digest: 规范化后的原始 query 的 sha256 hex
  content_digest: 注入 UTF-8 内容的 sha256 hex；status=empty 时为 null
  requested_max_bytes: integer
  used_bytes: integer，等于 content_bytes
  truncated: 任一选中条目被按大小截断时为 true
  non_deterministic: 调用了模型 rerank 或 query 扩展时为 true
  retrieval:
    rounds: [RecallRound]          # 最多 3 轮：round 0 加至多 2 轮扩展
    searches: [SearchGroup]        # 所有轮次合计最多 32 组
    rerank_configs: [RerankConfig] # 最多 8 个不同配置
    reranks: [RerankGroup]         # 所有轮次合计最多 32 组
  selected: [SelectedItem]         # 最多 32 条，包括代码证据
  omitted: [OmittedGroup]           # 最多 16 组
  stages: [StageTiming]             # 最多 8 个阶段耗时聚合
```

`receipt_id` 用于在日志中与 HTTP `X-PowerContext-Request-ID` 关联。它不是 Artifact ID，v1 不得将它当作耐久获取键。
`non_deterministic` 描述实际执行，而非仅仅配置：模型 rerank 即使 temperature 为零仍是非确定性的；配置了但未运行的
reranker 不会将该值设为 true。

### 精确选中身份

| `SelectedItem.kind` | 必需身份 |
| --- | --- |
| `memory` | `memory_entry_address`：带 Scope 的 Memory Artifact 地址、entry ID、entry version ID |
| `topic-memory` | `artifact_address`：Scope ID 与精确 Topic Memory Artifact ref |
| `experience` | `artifact_address`：Scope ID 与精确 Experience Artifact ref |
| `profile` | `artifact_address`：Scope ID 与精确已提交 Profile Artifact ref |
| `code` | `code_evidence`：Scope ID、workspace fingerprint、仓库相对路径、文件 SHA-256、包含端点的起止行、片段 SHA-256 |

Artifact 地址包含 `scope_id` 和 `artifact`（`family`、`artifact_id`、`revision`）。每个选中条目还包含
`rendered_bytes`（渲染片段的 UTF-8 大小，不含条目之间的分隔符）和 `truncated`。身份字段互斥。即使是当前 Scope 的
Memory citation 或 Artifact ref，也要扩展成带 Scope 的地址；两个 Scope 中相同的 Artifact 或 entry ID 必须是两个身份。
`memory_entry_address` 使用现有 `MemoryEntryAddress` 形状（`memory`、`entry_id`、`entry_version_id`），
其中 `memory` 是 Artifact 地址。`code_evidence` 使用现有 `CodeEvidenceRef` 字段（`scope_id`、`fingerprint`、
`path`、`file_sha256`、`start_line`、`end_line`、`snippet_sha256`）；它不是 Artifact ref。

选中条目按注入顺序排列。将简短 ref 规范化为地址后，其有序身份必须等于 `PreparedContextBuild.origins` 接上
`PreparedContextBuild.code_origins`，包括截断代码条目最终的行范围和片段哈希。只有代码的 ready 结果会有空 `origins`
和非空 `code_origins`。集合比较或只比较 `origins` 都不满足此不变量。缺少、多出、重排或歧义身份使整份 Receipt 无效，
并进入 Receipt 组装失败路径。

32 条上限覆盖当前显式 assembly 最多 26 条历史内容（Memory、Topic Memory、Profile 各八条，Experience 两条），
再加最多四条代码。默认 Builder 的历史条目合计上限是八，且也支持 Topic Memory；这些默认值不是所有请求的选中上限。
Runtime 条目限制和 byte 预算仍控制实际选择。Receipt 不得为适配自身 schema 而改变这些限制。

### 跨 Scope、跨轮次的检索证据

`RecallRound` 包含 `round`（0、1、2）、`outcome`（`completed` 或 `expansion_failed`）和 `retained`（该轮候选池
是否参与最终构建）。它复用召回扩展已追踪的执行结果。扩展失败且 Runtime 返回 round-zero 候选时，round zero 被保留，
每个被放弃的扩展轮次都标记为未保留。尝试过但失败的扩展不得报告为成功搜索。扩展 query 原文和模型响应均不进入 Receipt。

`SearchGroup` 聚合 `round`、`family`、实际 `mode`、`outcome`、`fallback_reason` 相同的调用，包含正整数 `count`。
family 为 `memory`、`topic-memory`、`experience`、`profile`、`code`；mode 为 `fts`、`vector`、`hybrid`、
`snapshot`、`code` 或 `none`。`snapshot` 描述 Profile 读取，`code` 描述现有代码 query 操作。
`outcome` 为 `completed`、`not_run` 或 `failed`；`none` 表示没有执行检索模式。Experience adapter 必须提供实际模式，
Runtime 不得根据 adapter 是否存在来推断。未启用的 family 不产生组；请求了但没有配置 reader 或可搜索 head 的 family
产生 `not_run` 组。已完成但零命中的搜索仍为 `completed`，保留实际模式。
completed/failed 组的 `count` 统计实际调用；not-run 组统计跳过的检索机会。扩展循环外的 Profile 和 code 操作归入
round zero，每次实际调用只计一次。

`fallback_reason` 为 `none`、`inference_unavailable`、`inference_timeout` 或 `reused_fts_fallback`。
`auto` 正常选择 FTS 使用 `none`；推理错误后放弃 vector 通道则使用实际错误类别。后续轮次复用该失败留下的 FTS-only
结果时使用 `reused_fts_fallback`。扩展失败属于 `RecallRound`，不属于搜索回退枚举。`auto` 是请求策略，不是 Receipt
中的实际执行模式。

例如，同一轮对两个已授权 Scope 的 Memory 搜索可以产生以下组：

```json
[
  {"round": 0, "family": "memory", "mode": "hybrid", "outcome": "completed", "fallback_reason": "none", "count": 1},
  {"round": 0, "family": "memory", "mode": "fts", "outcome": "completed", "fallback_reason": "inference_timeout", "count": 1}
]
```

聚合有意不列每次搜索的 Scope ID：ContextReferences 没有固定数量上限。选中身份始终保留 Scope；聚合计数描述所有实际
执行的调用，包括最终放弃的轮次。不得把 Topic Memory、Profile 或 code 的调用数乘以引用 Scope 数；只记录实际执行的
调用。按分组字段元组确定性排序，且不得合并不同模式或回退原因。

### Rerank 证据

`RerankConfig` 包含 `config_id`、`policy_id`、`model`、`effective_settings`、`timeout_seconds`、`max_requests`、
`config_digest`、`prompt` 和 `non_deterministic`。`policy_id` 标识 rerank 指令策略，不足以标识模型或配置。
`model` 是不带凭据的 provider/model 身份（明确声明为确定性、非模型 reranker 时为 null）。`effective_settings` 包含继承、覆盖和规范化后实际传入的非正文模型参数，
包括 temperature 为零等显式默认值；不是部署配置的完整转储。实现必须定义带版本的安全参数名和类型白名单，并校验值；
header、凭据、URL、任意 provider payload 和承载正文的参数均排除。

`config_digest` 是带版本记录经 RFC 8785 canonical JSON 编码后的 SHA-256；该记录包含策略、模型、有效非正文参数、
timeout、request limit 和下述 Prompt 身份。影响执行的安全参数不得被悄悄省略。adapter 若无法完整、安全地表达有效
配置，必须省略 Receipt 并记录无正文诊断，不能用部分配置 digest 声称精确证据。该 digest 是配置证据，不保证 provider
重现相同结果。

`prompt` 来自实际调用已绑定的 `ResolvedPrompt`，不能在调用结束后读取最新 Prompt head 代替。它包含 `scope_id`、
`key`、`definition_version`、`builtin_version`、`selection`（`built_in` 或 `artifact`）、`selected_version`、
`compiled_digest` 和 `artifact_address`（内置 Prompt 为 null；否则是带 Scope 的精确 Prompt Artifact 地址）。
编译后的指令和 demonstrations 排除。两个 Scope 的 Prompt 选择不同，不能仅因 rerank 指令策略 ID 相同就共用配置条目。

`RerankGroup` 包含 `round`、`config_id`、`outcome`（`selected`、`fallback` 或 `failed`）、`fallback_reason`
（`none`、`empty_selection`、`inference_unavailable`、`inference_timeout` 或 `invalid_output`）和正整数 `count`。
这些字段汇总实际 rerank 调用；相同配置和结果进行聚合并共用一个配置条目。回退仍记录调用过的配置。没有调用就没有组或
配置。模型配置的 `non_deterministic=true`，即使调用失败或回退也一样。注入的 reranker 必须提供等价证据，并声明是否
使用模型；证据不可得就是 Receipt 失败，不能虚构内置 Prompt 或把调用标成确定性。明确声明的确定性非模型
reranker 使用 `prompt=null`、`non_deterministic=false`，并在配置记录中标识实际算法/版本及有效安全参数。
仅仅缺少模型元数据不代表确定性。

`config_id` 就是 `config_digest`；配置条目按 digest 排序，每个 rerank 组必须恰好引用一个条目。rerank 按四个
分组字段聚合，并按该元组排序。

### 省略与耗时摘要

`OmittedGroup` 包含 `family`、`reason` 和正整数 `count`；相同 family/reason 聚合。family 与选中条目共用五值枚举。
reason 为封闭枚举：

| 原因 | 含义 |
| --- | --- |
| `duplicate` | 去重排除了重复的、带 Scope 的精确候选身份 |
| `blank` | 空身份或无可渲染文本 |
| `family_limit` | 候选超过该 family 或 assembly section 的接纳上限 |
| `entry_limit` | 候选超过合计注入条目上限 |
| `below_min_bytes` | 候选太短，无法截断进剩余预算 |
| `no_fitting_truncation` | 没有允许的截断方式能放入剩余 byte 预算 |
| `rerank_not_selected` | 粗排 Memory 池中的候选在进入 Builder 前被 listwise rerank 排除 |
| `not_retrieved` | 请求的 family 在保留轮次中没有产生候选 |

`below_min_bytes` 和 `no_fitting_truncation` 保留 Builder 已有的 `dropped_below_min_bytes` 与
`dropped_no_fitting_truncation` 区别。两者相加等于 `dropped_items`；不得再将总数计为另一种省略。
成功截断记录在选中条目与 Receipt 上，不计为省略候选。

`not_retrieved` 是每个请求 family 的空集标志，`count=1`，包括 reader/head 缺失。该 family 为最终构建产生过候选时，
即使全部候选后来被丢掉，也不出现该组。其他原因统计保留轮次和最终构建处理的有界候选列表中的排除事件，而非存储中的
不同 Artifact 数。重复身份被排除时增加 `duplicate`。放弃的扩展候选池不增加省略计数。现有 omission 和 recall-effort
计数表达同一事件时直接复用；其余排除在遍历同一有界候选池时计数，不增加数据库搜索或整份 Artifact 扫描。

`StageTiming` 包含 `stage`、`duration_ms` 和 `count`。对同一现有 Runtime stage 的多次执行累加耗时并记录次数；
其总和不必等于 prepare 的 wall-clock latency。只包含实际执行的阶段，包括放弃的轮次。不复制 span payload，
也不增加每个 Scope 的耗时列表。

## 边界

| 限制 | 取值 |
| ---: | ---: |
| `selected` | 32 条 |
| `retrieval.rounds` | 3 |
| `retrieval.searches` | 合计 32 组 |
| `retrieval.rerank_configs` | 8 个不同配置 |
| `retrieval.reranks` | 合计 32 组 |
| `omitted` | 16 组 |
| `stages` | 8 个阶段耗时聚合 |
| `receipt` JSON UTF-8 大小 | 8192 bytes |
| 条目正文、原始或扩展 query 原文、prompt 正文、模型响应、向量、密钥、token、绝对路径 | 禁止 |

在实际返回给调用方的 Receipt 序列化字节上检查预算。数量上限不保证 32 个完整身份加配置证据能放入 8192 bytes。
任何数量超限、byte 超限、身份不完整或执行证据缺失都省略整份 Receipt，记录无正文诊断，保持注入不变。
不得截断身份、用通用 policy ID 代替配置证据，或忽略额外调用。实现验收必须用实际身份长度，测量有代表性的混合 family、
跨 Scope 和 rerank 配置载荷。普通工作负载若频繁超出预算，应在发布前依据测量结果调整预算，不能悄悄发布持续缺失的诊断。

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

按 Tracking Issue 的要求，仅在设计被接受后开始实现。

1. 在 OpenAPI 中扩展 `PrepareContextRequest` 和 `PreparedContext`，再生成绑定。
2. 从 `build_scopes_result()` 和代码组装保留有序选中身份及逐条渲染元数据。在同一有界候选池统计排除事件，复用已有
   budget-omission 与 recall-effort 计数。
3. 在 `_recall_scope` 和 `_recall_round` 丢弃元数据之前，从每次搜索和推理调用保留请求内的执行证据。按轮次聚合实际
   模式和回退原因，保留失败扩展结果。rerank 配置证据绑定到调用时的有效模型参数与实际解析 Prompt。
4. Builder 返回后对规范化原始 query 和最终注入 `content` 做哈希。校验有序完整 origins、rerank 组与配置的引用关系、
   所有数量上限及实际序列化 byte 大小。
5. Receipt 任一校验失败时，记录无正文诊断，返回未改变的 PreparedContext，并省略 `receipt`。
6. 官方宿主召回请求保持不变。只有在宿主选择加入 Receipt 的同一改动里才更新校验器。不增加 Receipt 表或渐进内容缓存。

实现验收覆盖：

- empty、ready、truncated、deduplicated、reranked、fallback 和 Receipt 构造失败；默认、false、null 标志仍返回
  恰好四个响应键。
- 默认召回的 Topic Memory；选中 18 和 26 条历史内容的 Memory/Topic Memory/Experience/Profile 混合 assembly；
  纯代码与混合代码结果，包括代码截断后的最终行范围和哈希。
- 两个 origins 集合的有序完整选中身份、不同 Scope 中相同简短 ID、精确读取复用，以及 `content_digest` 等于返回
  `content` 的 UTF-8 哈希。
- 同一轮不同 Scope 中的 hybrid 与 FTS、已完成零命中与未运行的区别、正常 FTS 与推理回退、FTS 回退复用，以及扩展
  失败时仅返回 round-zero 候选池。
- 继承与独立 rerank 模型/参数、不同 Scope Prompt 与 revision、参数或编译 Prompt 变化导致配置 digest 变化、重复
  配置去重、temperature 为零仍非确定性，以及注入 reranker 缺证据时 fail-open 省略 Receipt。
- 两种预算丢弃子计数、五类来源的空集标志、放弃轮次不重复计数、每个数量上限、有代表性的序列化大小，以及 byte 超限
  时整份省略而不丢失身份。
- SQLite 与 OceanBase 的 Receipt 语义一致，无 schema migration，`include_receipt` 不增加召回/模型调用，成功 Receipt
  与失败诊断均无禁止内容。

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

- `PreparedContextBuild.origins` 与 `code_origins` 保留精确选中身份，在 HTTP 边界被丢弃。
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
