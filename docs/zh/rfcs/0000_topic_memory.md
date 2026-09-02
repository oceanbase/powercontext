- Proposal Name: `topic_memory`
- Start Date: 2026-09-01
- RFC PR: [oceanbase/powercontext#1417](https://github.com/oceanbase/powercontext/pull/1417)
- Related RFCs: [RFC 0014](0014_memory_layer_design.md)、[RFC 0019](0019_local_source_memory_runtime.md)、
  [RFC 0051](0051_experience_skill_artifact_families.md)、[RFC 0080](0080_memory_search_reranking.md) 和
  [RFC 0081](0081_end_to_end_evaluation_architecture.md)

# Summary

本 RFC 为 PowerContext 增加 `topic-memory` Artifact Family。一个 Topic Memory Artifact 表示一个长期主题，
包含标题、概要和详细正文。标题、概要与正文都参与检索；自动召回只返回标题、概要、可选正文片段和精确
ArtifactRef，Agent 判断有必要时再读取完整正文，从而实现渐进式披露。

Topic Memory 从每个 Scope 的不可变 Source Journal 中增量生成。处理器先从有界 Source Window 生成轻量
Probe，检索当前 Topic Heads，再根据上下文大小选择全局直接演进、按 Work Item 演进或临时 Topic 降级路径。
新建结果会在发布前进行第二次历史检索和相关组协调。最终只执行 CREATE、UPDATE 或 NOOP；模型只提供
`title`、`summary`、`detail` 和 `evidence_ids`，目标 Revision、Artifact identity、操作类型与发布状态全部
由服务端控制。

生成、检索、协调、chunking 和 Embedding 在后台 Worker 中完成。通用的
`ArtifactProcessingSupervisor` 使用持久化 Pending dirty set 发现待处理 Scope，使用独立 Source Cursor 保存
完成进度，并通过 Supervisor fencing、Cursor CAS 和 Artifact Head CAS 防止多副本、重复 Worker 或迟到 Worker
提交过期结果。新 Revision 只有在四个检索通道全部准备完成后，才在一个短事务中替换旧的可检索 Revision。

# Motivation

## 长期主题的内容组织

现有 Memory 适合保存可独立检索的事实、偏好、决定、约束和工作笔记。一个长期主题却需要把跨会话、跨任务的
多条证据整理成一个持续演进的整体，例如：

~~~text
标题：PowerContext 后台制品处理架构
概要：Source 驱动的制品使用独立 Cursor；global Supervisor 负责队列、Worker 和多副本选主。
详细内容：
  - Source Journal、Window、Cursor 与 Pending
  - SQLite 与 OceanBase 的 Supervisor 模式
  - Worker fencing、原子发布与故障恢复
~~~

把这些内容拆成许多 Memory Entry 会丢失主题结构；每次都加载完整主题正文又会浪费 Agent 上下文。Topic
Memory 提供独立主题 identity，并把发现、判断与完整读取分开。

## 主题需要随新证据演进

新 Source 可能补充、纠正或改变一个历史主题。只追加新记录会积累重复和冲突；只按相似度覆盖又可能把相关但
不同的主题错误合并。系统需要先检索历史 Topic，再让受约束的演进流程结合精确 Source 与精确历史 Revision
决定新建、更新或不处理。

## 长任务不能阻塞交互请求

一个 Topic Window 可能需要多次生成、四路检索、正文切片、批量 Embedding 和原子索引切换。Source 写入事务和
显式 flush 都不应等待整条链路完成。后台执行需要在 SQLite 单机与 OceanBase 多副本部署中保持相同的业务语义，
并能从进程崩溃、超时、重复派发和主备切换中恢复。

# Guide-level explanation

## 一个 Artifact 表示一个主题

Topic Memory 不在一个 Artifact 内保存多个 Topic Entry。每个主题拥有稳定、不透明的 `artifact_id`；标题可以
随内容演进，不是唯一键。Topic 内容为：

- `title`：简短且可区分的主题标题；
- `summary`：帮助 Agent 快速判断相关性的概要；
- `detail`：完整主题正文。

同一主题更新时保留 `artifact_id`，并产生新的不可变 Revision。`ArtifactRef` 精确引用
`family + artifact_id + revision`；完整定位还需要调用方提供 `scope_id`。搜索后展开必须继续使用搜索返回的
精确 ArtifactRef，不能改用 `artifact_id` 读取最新 Head，否则搜索与读取之间的并发更新可能改变内容。

Topic Memory 与 Memory、Experience、Skill 和 Handoff 共存。它不替代其他 Family，也不改变 Handoff 必须由
用户主动选择和继续的行为。

## Source、Window、Cursor 与 Pending

每次 Source add 都创建一条新的 Source，并获得稳定 SourceRef。一个 Source 可以封装一条消息、一轮对话、
多轮对话或一段文档，因此 Source 数量不等于原始消息数量。

每个 Scope 拥有一条按 journal position 单调递增的 Source Journal。不同处理 binding 为同一 Journal 保存独立
Cursor：

~~~text
Scope A Source Journal: 1 2 3 ... 20

Memory Cursor:       18  -> 待处理 19..20
Topic Memory Cursor: 15  -> 待处理 16..20
Experience Cursor:   10  -> 待处理 11..20
~~~

Source Window 是运行时选择的连续区间 `(after, through]`，不是持久化实体，没有 ID，也不会创建新的 Source。
Pending 说明哪些 `(binding_name, scope_id)` 可能落后；Cursor 说明该 binding 已经原子发布到哪里；Window
说明本轮实际处理哪些连续 Source。

## Topic 与 Source 的证据关联

Source Window 只是允许模型读取的输入边界，不等于每个 Topic 的证据。服务端为 Window 中的 Source 建立
operation-local evidence ID。模型为每个 CREATE 或 UPDATE 返回实际使用的 `evidence_ids`，服务端将其映射回
精确 SourceRef，并拒绝不存在或 Window 之外的引用。

例如：

~~~text
Window = Source 1..3

Topic A evidence_ids = [s1, s2]
Topic B evidence_ids = [s3]
~~~

最终 Topic A 的直接 lineage 只保存 Source 1、2，Topic B 只保存 Source 3。UPDATE 的新 Revision 还引用服务端
持有的精确旧 ArtifactRef。Window 本身不会被保存成证据，也不会生成 Source 归档。

## 渐进式检索与展开

Topic Memory 自动进入当前 Scope 的检索候选。标题、概要和全部 Detail chunks 从第一次检索开始就参与召回，
但自动返回的内容保持紧凑：

1. 搜索返回精确 ArtifactRef、标题、概要和可选正文 snippet。
2. Agent 根据这些信息判断 Topic 是否值得展开。
3. Agent 使用精确 ArtifactRef 读取完整 Detail 和 Source lineage。

渐进式披露控制的是“向 Agent 返回多少内容”，不是“哪些字段参与检索”。

## 自动处理与显式 flush

Source 写入只更新 Pending，不同步生成 Topic。Topic Memory 的自动处理默认关闭；部署者可以配置自动处理间隔。
用户也可以调用 `POST /v1/topic-memory/flush`，要求后台尽快基于最新 Source 快照启动处理波次。

flush 在处理意图持久化后立即返回 HTTP 200：

~~~json
{"status": "accepted"}
~~~

如果 Topic Memory Cursor 已覆盖调用时的 Source Head，则返回：

~~~json
{"status": "idle"}
~~~

`accepted` 不表示生成已经完成，也不创建可查询的一次性任务。并发 flush 会合并到同一 Pending 记录；运行中的
波次之后收到的 flush 最多触发一个后继波次。

# Reference-level explanation

## Family、binding 与处理范围

Artifact Family 描述持久化内容类型；processing binding 描述如何消费 Source 并产生或演进 Artifact。本 RFC
注册 `topic-memory` Family 和 `topic-memory-source-window` binding。

首版检索、生成和精确读取都限定在一个 `scope_id`。不接受 `scope_ids`，不执行跨 Scope 检索。Topic Memory
自动发布，不进入 Experience/Skill 使用的 Review Inbox。首版也不提供用户手动 create、update、delete、retire
Topic 的 API。

本 RFC 提取一层最小 Source-driven Artifact processing substrate，负责：

~~~text
发现 Pending
  -> 读取独立 Cursor
  -> 选择有界 Window
  -> 建立并验证 evidence ID
  -> 在事务外执行 Processor
  -> 使用 fencing、Cursor CAS 与 Head CAS 发布
  -> 失败后从相同 Cursor 重试
~~~

Topic Memory 是首个使用者。本 RFC 不迁移现有 Memory、Experience 或 Skill。

## Window 选择与上下文预算

Topic Window Policy 按 Journal 顺序选择 Cursor 后满足以下限制的最大连续前缀：

- Source 数量不超过 `runtime.topic_memory_source_window_limit`，默认 10；
- Source 估算 token 总量不超过生成模型上下文窗口的 80%。

`inference.generation_model_context_window_tokens` 默认 125,000，因此默认 Source Window 上限为 100,000
tokens。一次实际 generation 请求仍必须满足：

~~~text
系统提示词
+ 阶段指令
+ Source
+ 历史 Topic
+ 结构化输出 schema
+ 输出预留
<= generation_model_context_window_tokens
~~~

如果加入下一条 Source 会超预算，它留到下一 Window。如果 Cursor 后的第一条 Source 单独就超过 Window token
预算，为保证 Cursor 能前进，仍选择它形成单 Source Window，并按原文尝试处理。首版不对单个超长 Source 做
内部切片、截断或拒绝；如果它超过模型实际能力，处理失败并保留 Cursor，等待后续专门设计。

## Probe 与历史 Topic 选择

Worker 首先读取当前 Source Window，生成零到多个轻量 Probe。Probe 是语义查询句或关键词及其 evidence IDs，
不包含 Topic 正文，也不决定 CREATE、UPDATE 或 NOOP。

每个 Probe 在当前 Scope 的四个 Topic 检索通道中召回当前可检索 Revisions。通道结果先按 Topic 合并，再通过
RRF 融合。历史候选选择使用三个部署配置：

~~~text
history_max_candidates = 20
history_rrf_threshold = 70
history_min_candidates = 5
~~~

RRF 分数归一化到 0..100。先选择达到阈值的候选，最多 20 个；如果不足 5 个，再按融合排名从剩余候选补足；
如果总候选少于 5 个，则使用实际存在的全部候选。

## 分层演进流程

所有中间结果只存在于 Worker 内存，不是 Artifact、Source、Job 或可检索记录。

### 全局直接演进

服务端先估算：

~~~text
全部 Source Window 原文
+ 全部入选历史 Topic 正文
+ 提示词、schema 和输出预留
~~~

如果总量不超过上下文预算，并且服务端能够确定性地把输出绑定到操作目标，则跳过 Work Planner，由 Global
Topic Evolver 直接形成最终内容。

Global Evolver 是优化路径，不承担正确性责任。如果一次输出可能对应多个 UPDATE、不能唯一绑定历史 Topic，
或结构校验后仍存在目标歧义，服务端丢弃尚未发布的内存结果并降级到 Work Planner。模型超时、网络错误或
数据库错误属于处理失败，进入正常重试，不属于语义降级。

### Work Planner 与直接 Work Item

只有全局材料超过预算或全局目标无法确定性绑定时，才运行 Work Planner。Planner 只读取：

~~~text
Probe
+ Probe 对应的 SourceRef
+ 历史 Topic 的 title、summary、snippet
~~~

Planner 不读取完整 Source 或全部历史 Detail。它把材料分成 Work Items，并遵守：

- 每个 Probe 只属于一个 Work Item；
- 命中同一历史 ArtifactRef 的 Probe 必须进入同一 Work Item；
- 一个历史 Topic Head 本轮只由一个 Work Item 负责；
- 同一 SourceRef 可以属于多个不同主题的 Work Item；
- 一个 UpdateWorkItem 只绑定一个由服务端持有的目标 ArtifactRef；
- CreateWorkItem 不绑定历史目标。

每个 Work Item 再加载自己的 Source 原文与至多一个历史 Topic 完整正文。如果材料不超过上下文预算，Item
Evolver 直接生成最终 Topic 内容。

### 超长 Work Item 与临时 Topic

如果单个 Work Item 的 Source 原文加历史 Topic 仍超过预算，只对该 Work Item 启用临时 Topic 路径：

~~~text
Work Item Source
  -> 按 Source 边界拆成多个 Source Batch
  -> 每个 Batch 在不加载历史 Topic 的情况下生成临时 Topics
  -> 全部相关临时 Topics + 一个历史 Topic或空白目标
  -> 最终 CREATE / UPDATE / NOOP
~~~

临时 Topic 只包含本批 Source 对当前主题贡献的新信息，并保留自己的 evidence IDs。最终 lineage 是实际进入
结果的临时 Topics 所引用 SourceRef 的并集。临时 Topic 没有 identity，不写数据库，不参与检索，Worker 结束
后即丢弃。

如果“全部临时 Topic + 单个历史 Topic”仍超过模型上下文，首版不做递归压缩、历史 Topic 分片或自动拆分。这是
已知但明确排除的极端输入，与单个 Source 超过模型能力的处理边界一致。

## 二次检索与相关组协调

所有路径产出的 CREATE 内容在发布前使用完整的 `title + summary + detail` 再检索一次历史 Topics。第二次
检索用于弥补 Probe 表达不足造成的漏召回。

服务端根据第二次检索形成有界相关组，例如：

~~~text
本轮 CREATE B + 本轮 CREATE C + 历史 Topic D
  -> 一个 UPDATE Topic D
~~~

Planner 应先消除明显重复；相关组协调只是兜底。它可以把多个本轮 CREATE 合成一个 CREATE，或把多个本轮
CREATE 合入同一个历史 Topic 的一次 UPDATE，但不得把两个已经存在的历史 Artifact identities 合并。没有
CREATE 时跳过第二次检索和协调。

## 演进操作与模型输出

后台演进只产生三种内部操作：

- CREATE：服务端分配新 `artifact_id` 并创建 Revision 1；
- UPDATE：保留 `artifact_id`，完整替换 `title + summary + detail` 并创建下一 Revision；
- NOOP：不创建 Artifact 或索引，但 Window 仍成功，Cursor 正常推进。

UPDATE 是完整内容重写，不是 patch，也不以检索 chunk 为更新单位。补充、纠正、时间变化或范围收窄都通过
UPDATE 表达。旧 Revision 保留审计，但不参与默认检索。

首版不定义 MERGE、SPLIT、RETIRE 或 DELETE。新证据中出现独立主题时可以 CREATE；如果原 Topic 同时需要
纠正或收窄，则另做一次 UPDATE。两个历史 Topic 不自动合并。

Topic 内容生成阶段允许模型提供的业务字段只有：

~~~text
title
summary
detail
evidence_ids
~~~

UpdateWorkItem 的目标 ArtifactRef 由服务端持有；CreateWorkItem 的新 Artifact ID 由服务端生成。操作类型、
Revision、lineage 与发布状态也由服务端决定。NOOP 不保存实体或 reason，日志最多记录代码已知的
`no_change` 及处理上下文。

## Detail chunk 与四路检索

公开 TopicMemoryContent 只保存 `title`、`summary` 和 `detail`。Detail chunk 是可重建的内部检索投影，
没有业务 identity，不是公开数据模型，也不能作为 UPDATE 单位。

首版维护四个逻辑通道：

~~~text
title + summary 全文
embed(title + summary)
detail chunk 全文
embed(detail chunk)
~~~

Detail embedding 不拼接全局 Topic title，避免短 chunk 被标题主导。Detail 自身的局部 Markdown 标题可以作为
正文的一部分保留。

Chunk policy：

1. 先按 Markdown 标题、段落、列表和句子边界形成不可再分的语义块；
2. 将相邻语义块装配到内部目标大小；
3. 过短尾块优先并入前一个 chunk；
4. 只有单个语义块超过最大长度、必须固定窗口切分时才使用有限 overlap；
5. 搜索命中后围绕最佳位置动态扩展 snippet；
6. exact get 始终返回完整 Detail。

具体 chunk 长度、尾块阈值和 overlap 比例是带 policy version 的内部常量，不是公共配置。Chunk 策略变化需要
重建对应检索投影。

每个检索通道先按 Topic collapse，同一 Topic 的多个 detail 命中只保留最佳位置用于 snippet。四个通道通过
RRF 排名融合，不比较全文分数和向量距离的原始数值。最终一个 Topic 最多占一个结果位置，并保留
`matched_by` 说明命中通道。

## 存储、Head 与原子激活

Topic 内容、identity、Revision、Head 和 lineage 复用共享 Artifact 存储：

- `pc_artifacts` 保存不可变 TopicMemoryContent Revision；
- `pc_artifact_heads` 保存当前 Topic Head；
- `pc_artifact_lineage_sources` 保存直接 Source evidence；
- `pc_artifact_lineage_artifacts` 保存 UPDATE 所依据的精确旧 Revision。

Topic 专属检索存储维护两类 active projection records：

- Topic-level active record：精确 ArtifactRef、title、summary、全文字段和 title/summary vector；
- Detail-chunk active records：精确 ArtifactRef、chunk ordinal、正文位置、snippet 文本、全文字段和 detail vector。

数据库适配器可以按 SQLite FTS/vector virtual table 或 OceanBase full-text/vector index 的现有模式实现这些
逻辑记录，但搜索只能查询当前完整可检索的 active records，不能先读取所有 Revision 后构造大型 `IN` 查询。

Topic Content、chunks、全文字段和所有 Embeddings 都在事务外准备。更新已有 Topic 时，旧 active Revision
继续服务；新建 Topic 在首个 Revision 完整前不可检索。只有四个通道全部就绪后，Worker 才在一个短事务中：

~~~text
验证 Supervisor 任期
-> Cursor CAS
-> 对每个 UPDATE 执行 Artifact Head CAS
-> 写入全部 Topic Revisions 与 lineage
-> 写入并切换全部 active projections
-> 推进 Cursor
-> 更新 Pending
-> commit
~~~

一个 Window 的全部 CREATE 和 UPDATE 与 Cursor 原子提交。任一验证或 CAS 失败则整批回滚，并基于最新状态
重新处理。系统不存在只有全文、没有向量，或只有部分 Detail chunks 可检索的 Revision。

## Pending dirty set

`pc_artifact_processing_pending` 不是 Job 队列，也不保存
`queued/running/retry_wait/failed/completed`。表结构为：

~~~text
binding_name                NOT NULL
scope_id                    NOT NULL
source_through              BIGINT NOT NULL
flush_generation            BIGINT NOT NULL DEFAULT 0
handled_flush_generation    BIGINT NOT NULL DEFAULT 0

PRIMARY KEY (binding_name, scope_id)

CHECK source_through >= 1
CHECK flush_generation >= 0
CHECK handled_flush_generation >= 0
CHECK handled_flush_generation <= flush_generation
~~~

Source 写入事务执行：

~~~text
source_through = max(existing, new_source_position)
~~~

两个 flush generation 不变。显式 flush 在短事务中执行：

~~~text
source_through = max(existing, current_source_head)
flush_generation = flush_generation + 1
~~~

Supervisor 启动一个波次时冻结 `wave_target = source_through` 和
`claimed_flush_generation = flush_generation`。一个波次可以包含多个受 Window 限制的 Worker 作业。只有最后
一个 Window 成功覆盖 wave target 后，才执行：

~~~text
handled_flush_generation =
    max(current, claimed_flush_generation)
~~~

如果运行期间又发生 flush，`flush_generation` 会继续递增；当前波次完成后立即启动最多一个使用新快照的后继
波次。普通 Source 新增只提高 `source_through`，留给下一次自动或显式波次。

只有同时满足以下条件才能删除 Pending：

~~~text
cursor >= source_through
and handled_flush_generation == flush_generation
~~~

Worker 失败不推进 handled generation。Supervisor 重启后可以从 Cursor 和 Pending 恢复，不需要 Job history 或
阶段 checkpoint。

## Artifact Processing Supervisor

首版提供通用 `ArtifactProcessingSupervisor`，Supervisor group 固定为 `global`。同一个 group 使用一套：

- `pc_artifact_processing_pending`；
- `pc_source_cursors`；
- `pc_artifact_processing_leases`；
- 内存公平队列和全局 Worker pool。

本 RFC 不增加 `pc_artifact_processing_routes`。只有未来需要把 binding 在线、无停机地从 `global` 迁移到
独立 group 时，才需要持久化路由和 routing generation。

### 进程角色

`runtime.artifact_processing_role` 支持：

- `all`：API + global Supervisor；
- `api`：只运行 API；
- `background`：只运行 global Supervisor。

OceanBase 支持三种角色与多副本候选 Supervisor。SQLite 首版只支持单进程 `all`，不支持 API 与 Supervisor
分进程部署。

### Leader、任期与 fencing

`pc_artifact_processing_leases` 以 `supervisor_group` 为主键，当前只有 `global`，至少保存：

~~~text
supervisor_group
holder_id
supervisor_generation
lease_expires_at
~~~

每个 Supervisor 进程启动时生成 UUIDv4 `holder_id`。`supervisor_generation` 是单调递增的领导任期，
用于防止同一 holder 失去并重新获得领导权时出现 ABA。

OceanBase 允许多个候选者，但只有一个有效 `global` Leader。获得过期 Lease 时递增 generation；续租不递增。
Worker 最终事务必须验证 `holder_id + supervisor_generation` 且 Lease 未过期。

SQLite 不做选主或续租。Supervisor 启动时覆盖 holder、递增 generation，并令
`lease_expires_at = NULL`；这个任期持续到下一次合法启动。Worker 使用与 OceanBase 相同的业务流程，但只验证
holder 与 generation。即使旧 Supervisor 崩溃后留下孤儿 Worker，新启动产生的 generation 也会拒绝旧提交。

### 内存队列与 Worker

Leader 内存中按 `(binding_name, scope_id)` 维护公平队列。同一键同时最多一个 Worker；不同键可以在
`artifact_processing_max_workers` 限制内并行。每个 Worker 一次只处理一个 Window；仍有本波次积压的键回到
队尾，避免热点 Scope 饥饿其他 Scope。

Worker 是 Supervisor 管理的子进程，不发送进度心跳。Supervisor 只判断它是否在
`artifact_processing_worker_timeout_seconds` 内结束；超时则终止并重新派发。Worker 可以访问数据库，但只能
执行一次带 fencing、Cursor CAS 和 Head CAS 的最终短事务。`deadline_at` 不是数据库正确性条件，也不出现在
WorkAssignment 中。

### 自动调度

自动处理间隔按 binding 计算，而不是按 Supervisor 全局活动时间计算。一个自动波次启动时冻结当前
`source_through`，用多个 Window 处理到该目标；波次期间新增 Source 留给下一波。下一次自动间隔从本波结束后
重新计算。

SQLite 使用进程内 flush signal 和自动 timer 唤醒同进程 Supervisor。OceanBase Leader 使用短周期事件循环完成
续租、发现持久化 flush generation 和自动 deadline；同进程信号只用于降低延迟，正确性由数据库状态保证。

自动调度关闭时，普通 Pending 等待显式 flush。未完成的 flush generation 在重启后仍需恢复；自动调度开启时，
Supervisor 重启后对已有 Pending 立即启动恢复波次。

### 重试与可观测性

处理失败时不推进 Cursor、不删除 Pending，并阻塞同一 binding/scope 的后续 Source，但不阻塞其他键。Supervisor
在内存中维护：

~~~text
retry_states[(binding_name, scope_id)] = {
  consecutive_failures,
  next_retry_at
}
~~~

采用带抖动的指数退避：约 30 秒、1 分钟、2 分钟，直至约 30 分钟上限；不设置最大重试次数，也不自动跳过
Source。主备切换或进程重启会丢失退避状态，并允许立即额外重试一次。

模型调用、输出校验、检索、Embedding、数据库提交、Worker crash 和 timeout 等实际错误采用同一退避策略，
但必须按 `stage` 和 `error_code` 写结构化日志。Cursor/Head CAS 冲突与 leadership lost 是控制信号，不增加
普通失败次数。

日志至少包含 binding、scope、Window 范围、stage、error code、异常类型、失败次数、重试延迟、Supervisor
generation、worker ID 和 traceback；不得记录 Source 原文、Prompt、模型完整输出或密钥。

## HTTP、MCP 与 Prepared Context

### HTTP API

首版公开三个 HTTP operation。

`POST /v1/topic-memory/flush`：

~~~json
{
  "scope_id": "project:powercontext"
}
~~~

返回 `{"status":"accepted"}` 或 `{"status":"idle"}`，均为 HTTP 200。它只持久化 Pending 与 flush generation，
不等待后台完成。鉴权、请求校验、依赖不可用和内部错误沿用现有 401、422、503 和 500 语义。

`POST /v1/topic-memory/search`：

~~~json
{
  "scope_id": "project:powercontext",
  "query": "Supervisor 如何故障恢复？",
  "limit": 10
}
~~~

每个命中返回精确 ArtifactRef、title、summary、nullable snippet、融合 score 和 `matched_by`。同一 Topic 的
多个 chunk 命中只返回一个结果。

`POST /v1/topic-memory/get`：

~~~json
{
  "scope_id": "project:powercontext",
  "artifact": {
    "family": "topic-memory",
    "artifact_id": "topic-a",
    "revision": 3
  }
}
~~~

返回该精确 Revision 的 title、summary、完整 detail 和 SourceRefs，不暴露内部 chunks。

### MCP

MCP 只投影面向 Agent 的只读操作：

- `search_topic_memory`；
- `get_topic_memory`。

`flush_topic_memory` 不投影为 MCP tool，与现有 Memory flush 和 Experience/Skill generation 的 HTTP-only
边界保持一致。

### Prepared Context

`POST /v1/context/prepare` 自动检索 Topic Memory，并只加入：

~~~text
title + summary + optional snippet + exact ArtifactRef
~~~

不自动加入完整 Detail，也不在历史内容中注入“调用工具”的控制指令。MCP/API 工具描述负责告诉 Agent 可以按
精确 ArtifactRef 展开。

每个 Family 的候选上限为：

~~~text
Memory:       8
Topic Memory: 8
Experience:   2
~~~

不再使用“所有 Family 合计最多 8 条”的固定上限。Runtime 在各 Family 内部保持各自排序，跨 Family 不比较原始
score，并交错填充结果。请求的 `max_bytes` 是最终输出的统一约束；Topic-only 场景可以返回完整 8 条紧凑命中。

## Configuration

第一版新增或使用以下部署级配置：

| 配置 | 默认值 | 语义 |
|---|---:|---|
| `runtime.topic_memory_schedule_seconds` | `None` | 未配置时关闭自动波次；大于 0 时为波次间隔；小于等于 0 为配置错误 |
| `runtime.topic_memory_source_window_limit` | `10` | 一个 Window 最多包含的 Source 数 |
| `runtime.topic_memory_history_max_candidates` | `20` | 历史 Topic 候选上限 |
| `runtime.topic_memory_history_rrf_threshold` | `70` | 0..100 归一化 RRF 接纳阈值 |
| `runtime.topic_memory_history_min_candidates` | `5` | 阈值后不足时的最小召回数 |
| `runtime.artifact_processing_max_workers` | `10` | global Worker pool 总并发 |
| `runtime.artifact_processing_worker_timeout_seconds` | `600` | 一个 Window Worker 的本地超时秒数 |
| `runtime.artifact_processing_role` | `all` | `all / api / background` |
| `inference.generation_model_context_window_tokens` | `125000` | 生成模型单次请求的总上下文窗口，包含输入和输出预留 |

Source Window token 上限固定派生为 generation context window 的 80%；Topic 总请求预算为 100%。两个比例首版
不是公共配置。

Topic Memory 复用现有 generation model、generation timeout、generation max requests、Embedding model、
Embedding profile、dimension、normalization、timeout 和 batch size。Probe、Planner、Evolver 与 Reconciler
使用同一个 generation model；分阶段模型选择留给后续 RFC。

配置在进程启动时读取。OceanBase 多副本的 `all/background` 候选应使用一致配置，并在启动日志中记录有效值。

# Drawbacks

- Topic Memory 增加多次生成、四个逻辑检索通道、后台进程和索引存储，成本明显高于现有 Memory。
- 自动演进可能错误新建或错误更新主题；不可变 Revision 与 lineage 提供审计能力，但不能自动保证语义质量。
- 只有完整索引才能激活 Revision，会增加写入延迟。
- Pending dirty set 增加 Source 写事务的写放大。
- 不持久化 Job、checkpoint 和 retry state 简化了系统，但失败后需要重算整个 Window，且无法查询单次任务进度。
- `global` Supervisor 统一资源控制，但未来多个重型 Family 共用 Worker pool 时可能成为瓶颈。
- 单个超长 Source 和“临时 Topic + 历史 Topic”仍超上下文是首版明确接受的未覆盖极端输入。

# Rationale and alternatives

## 独立 Family，而不是扩展 Memory Entry

一个 Topic 拥有独立 identity、完整 Detail 和渐进展开语义。把它塞进 Memory Entry 会混合两种粒度，并让版本、
检索和评估难以区分。因此 Topic Memory 与 Memory 共存。

## Probe-first，而不是固定 candidate-first

每个 Window 先生成完整候选 Topic 会增加所有处理的第一轮成本。Probe 足以低成本召回历史；完整新 Topic 的
第二次检索再弥补漏召回。临时 Topic 只用于超长 Work Item，不成为每个 Window 的固定步骤。

## 分层降级，而不是硬截断

全局材料能放下时直接演进；放不下时按 Work Item 分组；单个 Work Item 仍过长时才生成临时 Topic。这样正常
路径保留完整原文，超长路径才承担压缩成本。普通多 Source Window 不会因为预算而静默丢弃尾部 Source。

## Pending + Cursor，而不是持久 Job 状态机

Cursor 已经权威表达完成位置，Pending 只需要指出哪些键可能落后。每 Window 一条 Job 会重复表达进度，并引入
状态清理、阶段恢复和任务历史。本 RFC 不需要公开任务查询、取消或 checkpoint，因此采用可合并 dirty set。

## 一个 global Supervisor，而不是每个 Family 一套后台系统

首版只有 Topic Memory 使用新骨架。一个 global Leader 可以统一 Worker 和模型并发，而不提前建立路由系统。
未来拆组仍可复用相同 Pending、Cursor、Lease 和 Worker 协议。

## 完整索引后激活，而不是融合不完整 Revision

允许无向量的新 Revision先参加全文检索，会让不同 Revision 拥有不同通道数，融合排名不可比较。保留旧 active
Revision，直到新 Revision 的四个通道全部就绪，可以避免大型 Revision `IN` 过滤和临时评分补偿。

# Prior art

- [OpenViking](https://github.com/volcengine/OpenViking) 使用分层上下文、Session commit、持久队列和异步记忆提取；
  它证明了紧凑发现信息与完整内容读取分离的价值。
- [Hindsight](https://github.com/vectorize-io/hindsight) 的 Mental Models 将持续积累的 Memory 汇总成可刷新主题视图，
  并支持事件或计划刷新。
- [TencentDB Agent Memory](https://github.com/TencentCloud/TencentDB-Agent-Memory) 使用异步队列、分布式协调和分层
  Memory processing，说明显式 flush 不应等待后续长链路完成。
- [Infini Memory](https://github.com/infinigence/Infini-Memory) 使用结构化正文边界、局部检索与命中后扩展，支持本 RFC
  的结构优先 chunk 和动态 snippet 设计。
- PowerContext RFC 0051 已定义 Experience/Skill 的精确 evidence、target Revision、Review 与 Head CAS；现有
  Experience incubation 也提供独立 Cursor、有界 Window、operation-local evidence IDs 和事务外生成。本 RFC
  复用这些原则，但增加自动历史检索、自动发布、完整索引屏障和多副本 Supervisor。

# Unresolved questions

当前范围没有阻塞 RFC 接受的未决设计。

以下边界已明确排除，不作为实现者自行选择的开放问题：

- 单个 Source 自身超过生成模型实际上下文时的切片、截断或拒绝策略；
- 临时 Topic 总内容加单个历史 Topic 仍超过上下文时的递归压缩或拆分策略；
- 两个已有 Topic identities 的自动合并；
- 跨 Scope Topic 检索；
- 用户手动管理 Topic 的 create/update/delete/retire API；
- 可查询的后台任务、取消、checkpoint 或持久化 retry state；
- 独立 Topic Supervisor group 与在线路由迁移。

# Future possibilities

- 独立 Artifact 配置 RFC，允许部署或用户选择启用哪些 Family，并按 Scope 覆盖调度和预算。
- 多模型 RFC，允许 Probe、Planner、Evolver、Reconciler、Embedding 和 rerank 使用不同模型。
- 将 Experience incubation 与 Skill usage evolution 迁入 Artifact Processing Supervisor。
- global 出现瓶颈后增加 `topic`、`experience` 或 `skill` group；只有要求在线无停机迁移时再增加持久路由。
- 为超长单 Source、超长历史 Topic 和递归协调设计专门的有损或无损降级策略。
- 增加 Topic 人工纠正、回滚、retire、历史可视化和评估标注能力。
- Topic Memory 开发完成并通过功能验收后，再设计并执行 LoCoMo 对比评测与调参，不把该评测作为本 RFC 实现验收条件。
