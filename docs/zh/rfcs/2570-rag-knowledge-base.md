---
title: "RFC 2570：基于 rag Artifact 的文档知识库"
description: "通过插件实现文档知识库，保留不可变 Source、分块版本、混合检索与可恢复的后台处理。"
---

- Proposal Name: `rag_knowledge_base`
- Start Date: 2026-11-10
- RFC PR: [#2570](https://github.com/oceanbase/powercontext/pull/2570)
- Tracking Issue: [#2440](https://github.com/oceanbase/powercontext/issues/2440)
- Related Issues: [#2441](https://github.com/oceanbase/powercontext/issues/2441), [#2442](https://github.com/oceanbase/powercontext/issues/2442), [#2443](https://github.com/oceanbase/powercontext/issues/2443), [#2444](https://github.com/oceanbase/powercontext/issues/2444)

# 概述

通过一个文档 Source 插件和 `document`、`rag` 两种 Artifact family 提供文档知识库。一个 Scope 内允许用户创建多个
`rag` Artifact。用户向指定 `rag` 导入文件，后台解析不可变输入，生成代表整篇文档、分行保存 chunks 的 document Artifact，
自动关联知识库，再发布全文／向量索引。本文描述设计目标，不表示接口已经发布。

# 动机

业务描述和任务 SOP 需要带精确证据的片段检索，以及显式更新和删除能力。提取的记忆可以补充这一场景，但不能保留全部文档内容。
知识库必须区分原始输入、处理后的内容和可检索版本，避免处理失败破坏已有结果，也避免把人工纠正伪装成原始证据。

复用 PowerContext 的 Source 定义、Artifact 管理、插件注册、Scope 授权、处理监督和持久化设施，补充其中尚缺的文档专用契约，
不另建平行的知识库对象体系或通用工作流引擎。

# 使用层说明

Scope 继续代表项目或工作空间，并作为首期授权边界。知识库是 Scope 内的 `rag` Artifact，拥有独立名称和处理／检索配置。
知识库改名或删除不会改变 Scope。一个 Scope 内的不同知识库不构成独立的保密边界。

例如，团队在同一 Scope 中创建“产品文档”和“运维 SOP”：

1. 通过 Artifact 管理创建两个空 `rag`，无需先导入文档。
2. 向“产品文档”导入 Markdown，向“运维 SOP”导入独立的文字图片。
3. 获取持久导入回执，查看解析、索引和失败状态。
4. 检索单个知识库，或显式选择两个库联合检索。处理后文档自动关联知识库，索引发布后才能检索，无需手动挂载 document。
5. 替换原文件或编辑已有 chunk，生成新 document revision；索引就绪后新内容参与检索。更新失败时保留旧发布版本，除非它已被撤销或移除。

首期支持 Markdown 和独立导入的图片，OCR 使用 RapidOCR + ONNX Runtime。Markdown 图片仅保留引用，不抓取或识别引用目标。
SQLite、SeekDB、OceanBase 均须支持完整功能链路；一个部署使用其中一种后端。

普通 TXT、PDF、Office、自动同步、生产级 rerank、agentic 检索、完整管理界面和几万篇文档的规模评测不作为首期验收条件。
但任务状态和恢复 API 必须能支撑后续界面。根据检索结果回答问题仍由应用负责。

# 技术说明

## 1. 身份、归属与溯源

```text
Scope
  +-- rag K1 -- 成员关系 --> document D1 -- revisions --> chunks
  +-- rag K2 -- 成员关系 --> document D2 -- revisions --> chunks
                                ^
不可变文档 Source ---------------+  内容溯源
```

| 对象 | 身份与职责 |
| --- | --- |
| `rag` Artifact | 用户创建的配置与集合身份；成员变化和处理进度不生成配置 revision |
| 文档 Source | 不可变原始输入描述：原路径、受管理快照引用、摘要、媒体类型和采集元数据 |
| `document` Artifact | 一篇完整逻辑文档，ID 稳定，内容 revision 递增；首期唯一归属一个 `rag` |
| Chunk | document revision 内部的内容和检索单元，不是 Source 或独立 Artifact |
| 索引 generation | 以 `rag` 为范围的索引配置／构建，与内容 revision、插件注册代次分离 |

逻辑文档身份为 `(scope, rag, source_namespace, external_key)`。同一身份再次导入更新原 document；同路径导入另一 `rag`
产生独立 document，不能按内容摘要合并逻辑身份。一个不可变 Source 可显式复用于同 Scope 的多个知识库，但不因此建立跨 Scope
Source 身份或共享 document 归属。

Source lineage、`rag` 成员关系和可检索 revision 选择是三种不同关系，不能用标签代替归属。原路径用于溯源，读取使用采集时的
不可变快照，而非路径现在指向的文件。原始 Markdown 字节和图片保存在 OpenDAL 管理的本地目录，适当复用现有 OpenDAL／`opendalfs` 集成。

定义专用文档 Source 类型，暂称 `knowledge-document-input`，仅允许 document 处理链路直接消费。在输入选择、执行和提交校验中
落实限制，不仅靠命名约定；不能自动将其暴露到其他 Artifact family 消费的通用证据流。解析结果、OCR 文字、chunks、索引指针和
任务进度不得回写 Source payload。

## 2. 插件职责与框架适配

| 逻辑插件 | 负责的行为 |
| --- | --- |
| 文档 Source | 文件校验、不可变快照、Source 接受、授权后的原文读取 |
| `document` Artifact | 解析、OCR、切块、内容版本、chunk 读取、人工编辑和证据映射 |
| `rag` Artifact | 配置、成员关系、索引 generation、发布、混合检索和显式多库查询 |

这是三个逻辑插件，不要求三个进程或独立安装包。Parser／OCR／chunker 是 document 内的可替换组件；索引和检索适配归 rag，
使用共享数据库和模型设施。rag 可以引用默认配置，组件 schema 由其所属能力校验。

复用 Source、Artifact 插件注册及现有管理／读取约定。现有生成模型仍需扩展显式 rag 创建、领域明细存储和定向任务能力：
空 rag 是管理对象，不是消费 Source 的生成结果。不能伪造输入 Source 来触发建库，也不能声称仅配置通用插件声明就已满足全部能力。
仍可记录现有系统生成的管理溯源，但不触发文档处理。

知识库应用服务通过注册的 family 能力协调这些操作，不是第四个领域插件。各 writer 只管理本领域记录，并参与调用方共享数据库事务；
插件不能写其他插件的私有表或独立提交。首期使用同一部署数据库，不做插件服务间的分布式事务。document parser 无权决定目标 rag。

## 3. 解析与 chunk 存储

Markdown 使用 `markdown-it-py` 处理，保留标题、段落、列表、代码、表格和链接。将解析位置映射回原始 UTF-8 字节，不能用块级行号
伪造行内精确位置。RapidOCR + ONNX Runtime 处理独立图片，固定模型身份、限制图片大小，并将预处理后的坐标映射回原图。
保留真实 OCR 文字、置信度和区域；区分不支持的输入、非法编码、坏图、无识别文字、模型缺失和超时，不用生成模型补写或改写 OCR 输出。

两条路径都输出经过校验的 blocks、sections 和原文定位，交给可配置的标题／段落／长度切块策略。保留顺序、重叠／续块关系、邻接信息，
以及可能包含多个原文跨度或图片区域的定位。定位必须对应真实证据；OCR 置信度是引擎分数，不是准确率承诺。

| 存储 | 建议表示 |
| --- | --- |
| 现有 Artifact 记录与 heads | 每个 document revision 一条有界 manifest：标题／摘要、原始 Source 引用、配置标识、schema 版本、chunk 数量和集合摘要 |
| 文档明细 | 每 chunk 一行，以 `(scope, document_id, revision, chunk_id)` 为键，配套章节／block 结构和证据／编辑元数据 |
| 成员与目标状态 | rag 归属、逻辑文档键、目标输入／配置，以及生命周期和并发代次 |
| 检索投影 | chunk 级全文／向量条目及精确 revision／profile／generation 映射 |
| 处理记录 | 持久目标请求、阶段／结果、重试、租约及可恢复的索引工作与删除回执 |

在语义匹配时复用 Artifact、head、lineage、tag 和处理设施。不新增重复的知识库／document／revision 主表，也不将文档数据混入
Topic Memory 专属 chunk 表。下述建议结构确定归属、键和最小字段；后端 DDL 和辅助索引通过迁移落实，不在运行时临时建表。

首期每个 revision 保存**完整 chunk 快照**。100 块的文档保存一条 manifest 和 100 行明细；编辑一个块后再保存一条 manifest
和完整 100 行快照。校验数量、唯一顺序、结构引用，以及覆盖有效文本、顺序和证据映射的摘要。分批暂存数据在完整 revision 提交前不可见。
这一选择接受存储放大，以简化版本读取和恢复；跨 revision 共享 chunk 正文留待后续。

Artifact 主体不能保存无界的全文／chunk／向量数组。检索直接访问 chunk 索引，再批量读取精确命中和同版本邻接块。通用 Artifact
读取返回 manifest；明细分页游标固定 revision、视图和顺序。原始解析证据与编辑后的有效 chunks 分开读取，不能直接拼接重叠 chunks
来导出编辑后的整篇文档。

### 3.1. 复用的现有表

| 现有表／设施 | 用途与必要适配 |
| --- | --- |
| `pc_sources`、`pc_source_journal_heads` | 保持 canonical payload 和 journal 顺序不可变；引用原始快照，不保存派生 chunks |
| `pc_artifacts`、`pc_artifact_heads` | 保存 rag 配置、document manifest 和版本；所有 Artifact 键保留 family；head 不作为检索发布指针 |
| `pc_artifact_lineage_sources`、`pc_artifact_source_locators` | 保留原始输入和编辑 Source；增加／核实 `(scope_id, source_type, source_id)` 反向查询索引 |
| `pc_artifact_lineage_artifacts` 与 tags | 精确 base revision 证据和标签，不代表 rag 成员关系 |
| `pc_artifact_processing_intents`、租约与 binding 状态 | 复用唤醒、执行资格和注册栅栏，逐文档工作单独记录 |
| `pc_scope_settings` | 保留系统管理的 Source 可见代次，和 Source 删除原子递增；不允许用户直接修改 |

不复用 `pc_artifact_publications` 表示可检索版本，它负责跨 Scope 发布。通用搜索状态按 Scope／family 标识，未经扩展不能支持
两个 rag 各自的 generation。可复用向量空间定义和适配代码，但 rag 发布选择必须独立；通用 head 的 searchable_text 不是片段索引。

### 3.2. 建议关系表结构

以下是拟定表名，不表示现有数据库已经包含这些表。基线为七张领域表、一张共享 Source 生命周期表和一张 chunk 索引投影表。
不重复新增 Source／Artifact／revision 主表，不另建编辑审计表和请求回执表。只有保持下述键、原子性和读取行为时，才能合并到
语义等价的既有设施中。后端搜索结构可能额外需要物理表。

所有键和关联包含 scope_id。ID 按字节精确比较，revision 为正整数，代次为非负大整数，时间使用 UTC。
PK 表示主键，UQ 表示唯一键；省略公共时间字段。JSON 仅存有界元数据／配置，不保存整篇文档的 chunk 数组。

| 建议表／所属能力 | 键 | 最小字段与用途 |
| --- | --- | --- |
| `pc_rag_runtime`／rag | PK `(scope_id, rag_id)` | `state`（active/disabled/deleted）、`version`、`effective_config_revision`、`active_generation_id`、`building_generation_id`、`change_seq`、`visibility_epoch`、`deleted_at`；运行状态与内容分离 |
| `pc_rag_documents`／rag | PK `(scope_id, document_id)`；UQ `(scope_id, rag_id, namespace, external_key_hash)` | `rag_id`、完整 `external_key`、`state`（active/deleted）、`target_source_type/id`、`target_content_generation`、`target_config_revision`、`last_change_seq`、`version`、`deleted_at`；身份预留与成员关系 |
| `pc_document_nodes`／document | PK `(scope_id, document_id, revision, node_id)` | `kind`（section/block）、`parent_node_id`、`ordinal`、`title/text`、`source_locators`、`parser_metadata`；不可变原始解析证据与层次 |
| `pc_document_chunks`／document | PK `(scope_id, document_id, revision, chunk_id)`；UQ `(scope_id, document_id, revision, ordinal)` | `section_id`、`text`、`content_digest`、`source_locators`、`origin`、`base_revision`、`edit_source_type/id`、`metadata`；完整有效内容快照 |
| `pc_rag_index_generations`／rag | PK `(scope_id, rag_id, generation_id)` | `state`（building/ready/active/retired/failed）、`config_revision`、不可变 `profile_snapshot`、`start_seq`、`applied_seq`、`cutover_seq`、`build_fence`、`error`；独立配置构建 |
| `pc_rag_document_publications`／rag | PK `(scope_id, rag_id, generation_id, document_id)` | `document_revision`、`target_content_generation`、`chunk_count`、`index_digest`、`version`；选择索引完整的内容，不代表成员或 head |
| `pc_rag_jobs`／应用服务 | PK `(scope_id, job_id)`；UQ `(scope_id, request_scope_key, operation, idempotency_key)` | 按操作填写 `rag_id/document_id`、`binding_name`、`parent_job_id`、精确 Source、`request_digest`、`profile_snapshot`、`target_content_generation`、`generation_id`、`change_seq`、`stage/state`、`attempt`、`lease_token/expires_at`、`next_attempt_at`、`receipt`、脱敏 `error`、`actor`；回执、逐项任务和索引变更工作 |
| `pc_source_lifecycle`／共享 Source 服务 | PK `(scope_id, source_type, source_id)` | `state`（active/deleted）、`delete_generation`、`deleted_at`、`deleted_by`、`reason`、`delete_operation_id`；首期用于文档输入及其编辑 Source |
| `pc_rag_chunk_index`／rag 后端适配 | PK `entry_id`；UQ `(scope_id, rag_id, generation_id, document_id, revision, chunk_id)` | 完整目标键、`searchable_text`、`embedding_input_digest`、`vector_space_id`、`embedding`、`source_digest`；精确 chunk 的派生索引条目 |

request_scope_key 是服务端生成的非空规范键：建库使用 Scope+操作，库内命令使用目标 rag，删除 Source 使用精确 Source 身份。
内部任务使用确定性键，不能因为 rag_id 可空而破坏幂等唯一性。请求摘要覆盖操作、目标、规范化输入描述和前置条件。
接受后的快照固定，即使原路径内容变化，重试也返回原回执；新文件版本必须使用新请求键。

完整 external_key 与定长身份 hash 一起保存；碰撞时比较完整值，不能静默合并。接受导入时预留 document ID 和归属行，内容处理成功后
才创建 Artifact，因此预留行不能提前要求 document head 外键存在。内容／node／chunk 引用必须定位到
`(scope, family=document, document_id, revision)`，rag 引用定位到 family=rag。同库关联使用组合约束或事务校验。公开删除不执行 SQL 级联。

manifest 包含 root SourceRef、parser／OCR／chunker 快照标识、chunk_count、chunks_digest 和 schema 版本。
章节／block 父子引用只能位于同一 revision，且不能成环；chunk 定位关联这些 blocks，在发布前校验 ID 和顺序。
编辑 Source payload 提供审计信息，不新增审计主表。

### 3.3. 索引与后端映射

| 访问路径 | 必要索引／约束 |
| --- | --- |
| 列出库内文档 | 成员表 `(scope_id, rag_id, state, document_id)`，不扫描 Scope 全部 Artifact |
| 读取 revision chunks | 唯一 ordinal 索引；keyset 游标固定 revision 和最后 ordinal/chunk ID |
| 查 Source 依赖 | lineage 反向索引；每次编辑继续保留 root 依赖 |
| 领取任务／恢复租约 | `(binding_name, state, next_attempt_at, job_id)` 和 `(state, lease_expires_at)` |
| 追平重建 | 持久变更任务 `(scope_id, rag_id, change_seq)`；构建仍需使用的回执不得丢弃 |
| 检索发布内容 | generation／publication 键与 chunk 索引 UQ；排名前约束精确 revision |

SQLite 使用 FTS5 和稳定的 entry／row-ID 映射，向量使用经过验证的 sqlite-vec 适配或其他明确支持的精确实现。
缺少向量能力不能静默降级混搜。SeekDB 和 OceanBase 分别验证全文／向量适配。embedding 为逻辑列，实际可能按维度分表或使用
不同向量结构，但都映射到精确 chunk 键。核实后端索引长度限制，不能截断身份。模型／profile 变化创建新 generation，不覆盖旧向量。

人工编辑事务写入系统 Source、新 manifest、完整 nodes／chunks 快照、lineage、目标代次和索引任务，保留成员关系。
索引 Worker 构建不可见条目、核验覆盖与摘要后，通过 CAS 切换 publication 行。全库重建捕获 change_seq 并追平到切换水位；
新增、更新、移除和目标变化均在所属事务中递增序号。Source 删除另用 Scope 可见代次校验，不等待逐库传播完成。

取消／过期构建不能变为可见。首期保留已删除记录和索引，因此存储会随版本和重建增长，需要暴露保留量／可见量及容量不足错误。
恢复、垃圾回收和永久擦除后续单独设计，不是 DELETE 的隐含副作用。

## 4. 定向异步任务与发布

导入请求固定 Scope、目标 rag、精确 SourceRef、逻辑文档身份、处理配置快照和输入／请求代次。接受或复用 Source 与记录目标工作
必须原子完成。仅依赖 Source journal 重放不够：已有 Source 导入另一 rag、重试和配置重建可能不新增 Source，必须显式请求处理。

复用 Scope／binding 的监督、租约、超时和执行栅栏。Worker 领取持久目标工作，不向所有 rag 广播 Scope 中的每个 Source。
阶段包括解析／OCR、切块与内容提交、索引构建、发布。工作项遵循以下状态转换，并使用有上限的重试策略：

```text
queued -> running -> succeeded
             +--> retry_wait -> queued
             +--> failed
queued / running / retry_wait -> cancelled 或 superseded
failed -> 经授权的显式重试 -> queued
```

暴露阶段、失败原因、attempt，以及最新内容和可检索 revision。首次失败时可以尚无 document Artifact。重试失败的持久阶段，
不重复生成内容版本或发布。同一幂等键携带不同请求摘要时返回冲突。

| 边界 | 原子记录／必要校验 |
| --- | --- |
| 快照准备 | 接受前保存字节；失败后的无引用快照保留，待后续 GC 策略处理 |
| 导入接受 | Source 接受／复用、已验证的目标请求和持久调度意图 |
| 内容提交 | 完整 manifest／明细、溯源、已验证的成员关系、内容回执和索引工作；人工编辑在此创建系统 Source |
| 索引发布 | 校验完整索引、精确内容目标、生效配置、执行资格和生命周期；切换可检索选择及可见代次 |
| 软删除 | 确认前持久化所属生命周期标记、推进可见代次并保存幂等回执；不物理清理 |

OCR、embedding、DDL 和批量索引构建都在上述短事务外执行。每次提交重新校验注册／租约栅栏、目标状态及输入／配置／删除代次。
新的编辑或导入使旧发布任务失效。持久工作记录是恢复依据，进程内通知仅加速唤醒。

内容 head 与可检索 revision 分离。索引失败时保留旧发布版本，同时展示新内容和错误。rag 重建使用新 generation，在切换前追平已接受
变更到经验证的水位，不能用部分构建替换当前 generation。重建一个知识库不得切换其他库的 generation。文件系统／数据库备份恢复后，
应验证被引用快照和索引一致性，再开放恢复后的检索。

现有 `pc_artifact_processing_pending.source_through` 要求正 Source 水位。纯目标任务通过 requested intent 代次和持久任务调度，
不得伪造 Source 水位或推进 Source cursor。领取任务固定自身 lease token 和 Supervisor／binding 栅栏，每次 checkpoint 都比较。
租约过期允许恢复，不允许旧 Worker 继续提交。

## 5. 人工编辑、重处理与删除

首期只允许替换一个已有 chunk 的文字，以 document head ETag 做并发控制；不开放 chunk 增删、合并或重排。超长编辑明确拒绝，
不静默重切块。编辑一个 chunk 不自动修改其他 chunk 中的重叠文字。

沿用 Artifact 管理的溯源模式：在内容事务中创建 `role=lineage_only`、`operation=artifact_replace` 的系统 Content Source，
再提交新 document revision 和索引工作。记录 base revision、修改内容、操作者／原因及精确目标。该 Source 不再次进入解析。
除编辑 Source 和 base revision 外，显式保留原始输入 Source 依赖，不能假设通用替换溯源会自动保留根输入。

人工替换保留逻辑 chunk ID，但引用始终包含 revision。重新解析／切块生成新 chunk ID，序号不代表身份。编辑后的结果标记
`origin=manual_edit` 和 `verbatim=false`；原始定位是修改依据，不表示修改后的文字逐字出现在原文中。

更新同步全文条目、向量映射、发布选择及受可见性约束的缓存。只有完整实际输入和模型／profile 身份一致时才可复用 embedding 计算，
即使复用也必须索引新 revision 地址。仅修改 embedding／索引配置保留人工编辑内容。重处理含人工修改的内容必须显式指定
`manual_edits_policy=discard`，否则冲突。Source 替换／重解析尚未提交时拒绝编辑；已提交内容仍在索引时允许继续编辑，并使旧索引
任务失效。这些条件必须和目标状态一起原子校验。

**首期仅实现软删除。** Source payload、原文快照、document revision、chunks、编辑证据和索引均保留，不提供恢复或永久擦除接口。
删除表示不能再通过受支持的应用接口获取内容，不代表数据库备份或管理员存储访问中的数据被擦除。保留 tombstone，防止身份复用和意外恢复。

| 删除对象 | 权威标记 | 立即生效范围与保留对象 |
| --- | --- | --- |
| 文档 Source | `pc_source_lifecycle.state=deleted` 与 Scope 可见代次 | 所有依赖它的 revision 不可用，包括同 Scope 多个 rag 内的人工编辑版本；rag 配置保留 |
| document | `pc_rag_documents.state=deleted` 与 rag 可见／变更计数 | 其全部 revision／chunks 在成员读取、通用读取和检索中不可用；Source 和其他文档保留 |
| rag | `pc_rag_runtime.state=deleted` 与 rag 可见代次 | 知识库及内部文档不可用；Scope、共享 Source 和其他 rag 保留 |

可见性是多个条件的交集，不需要给每行复制一个删除布尔值：普通内容读取要求 rag 未删除且启用、document 成员有效、根 Source 未删除。
检索额外检查 active generation 和精确发布 revision。历史读取可指定旧 revision，但仍检查其所属对象和 Source 状态。
根输入必须取自被读取的 revision，不能使用成员表中最新 target Source。历史 base revision 链接不代表传递性的内容依赖；
完整重解析的替代版本声明自己的根输入。
停用 rag 的配置／状态仍可供有权限的用户管理，内容检索关闭；重新启用停用 rag 不等于恢复已删除 rag。

删除 Source 不必同步更新每个 chunk／成员行，根标记和可见代次即可立即拒绝读取。管理状态根据这些标记计算实际不可用原因。
携带派生正文的系统编辑 Source 也必须通过精确 document 目标和不可变 root Source 引用校验，普通 Source、通用 Artifact、历史、
chunk、原图和缓存读取均不能绕过。
原始 Source 具有独立的 Scope／生命周期授权，单独删除 rag 或 document 不撤销该 Source 及其原文件直读。
编辑 Source 保存的是派生文字，必须检查 document／rag 目标。公开 Source DELETE 仅针对原始文档输入，编辑 Source 由系统管理。

保留 lineage 外键及 RESTRICT 约束，公开 DELETE 不物理删除 Source／Artifact 行。重复删除返回原回执，不再次推进代次。
向已删除身份导入返回 410；必须显式选择新身份。首期没有清除删除标记的操作。在途读取返回前重新校验，迟到任务不能提交或发布。
已经发送的响应无法收回。

删除旧 Source 不影响仅依赖替代 Source 的 revision。若当前发布的旧 revision 依赖已删 Source，即使替代版本仍在索引，也必须立即不可用。
不能用旧版兜底、静默发布未完成索引的替代版本，或让人工编辑内容跳过 Source 校验。

## 6. 检索与公开接口

查询显式选择完整 rag 引用，可位于同一 Scope 或多个已授权 Scope。不能仅凭 Scope 默认选第一个或全部知识库。
逐目标授权，在全文／向量通道内落实成员、元数据、生命周期、已发布 revision 和 generation 过滤，不能先取整个 Scope 的 top-k 再过滤。
后端 ANN 无法满足合格候选集合过滤时，使用经过验证的过滤集合内精确向量检索，不静默改变检索契约。

限制候选数量并采用确定性的排名融合。不同 embedding profile 使用兼容的查询向量，不能跨库比较不兼容的原始分数。
去重保留不同来源证据。结果返回正文、Scope／rag、document／revision／chunk、SourceRef、定位、编辑标记及实际索引 profile／generation。
逐目标失败与无命中分开报告，全部目标失败时返回失败。Parser、chunker、检索策略和可选 rerank 扩展均须保持预算与证据契约。

复用通用 Artifact 创建／读取／替换／revision／tag 的形状、完整引用、分页、错误和条件写入约定，新增定向导入、任务查看／重试、
固定 revision 的 chunk 读取、chunk 编辑、检索和 Source 撤销等领域能力。不开放能绕过溯源和归属校验的任意 document 替换。
HTTP、Python client 和必要 MCP 能力保持一致，知识库上下文显式选择，现有 Memory 检索默认行为不变。

下述路由和 payload 是建议契约，实现时写入 `openapi/powercontext.yaml`，生成客户端并运行契约检查，不表示接口已经可用。新 family 和明细表通过三个后端的版本化迁移引入；既有 Source 和 Artifact family 保持原契约，不自动转换或加入文档处理。

### 6.1. 接口清单

约定 `R = /v1/scopes/{scope_id}/artifacts/rag/{rag_id}`，
`D = /v1/scopes/{scope_id}/artifacts/document/{document_id}`。以下路由尚未发布。
复用现有 ArtifactReference、ArtifactAddress、SourceTypeReference、ETag、Location、分页、ErrorResponse 和 Scope 权限，
不另建通用 Artifact CRUD 协议。

| 操作 | 路由／契约 | 权限／返回 |
| --- | --- | --- |
| 创建 rag | 复用 POST `/v1/scopes/{s}/artifacts`，family=rag 与类型化 content；为此命令扩展可选 idempotency_key | scope.contribute；201 ArtifactCreated、ETag、Location |
| 列出／读取／配置 | 复用 family list、GET/PUT `R`、revisions/tags；配置 PUT 携带当前 Artifact If-Match，完整替换 content | 读 scope.read，配置 scope.contribute；200、ETag |
| 运行状态 | 新增 GET/PUT `R/runtime`；PUT 接受 enabled 和 expected_version，不生成配置 revision | 读 scope.read，控制 scope.admin；200 运行版本／状态 |
| 批量导入 | 新增 POST `R/imports`；idempotency_key、items，每项有唯一 item_key、namespace/key 和输入描述 | scope.contribute；202 父任务和逐项回执 |
| 浏览成员 | 新增 GET `R/documents`；cursor/state 过滤，包含预留目标、成员版本、实际可用状态及内容／发布版本 | scope.read；200 items/next_cursor |
| 文档／历史 | 复用 GET `D` 和 revisions；manifest／溯源均检查生命周期 | scope.read；200、ETag |
| Chunk／证据明细 | 新增 GET `D/revisions/{rev}/chunks` 和 `/nodes`；分别返回有效 chunks、原始证据，支持 cursor/limit | scope.read；200 items/next_cursor，固定 revision/view |
| 编辑 chunk | 新增 PATCH `D/chunks/{chunk_id}`；document If-Match、text/reason/idempotency_key | scope.contribute；202 新 revision 和索引任务 |
| 重处理 document | 新增 POST `D/reprocess`；document If-Match、幂等键，必要时显式 manual_edits_policy=discard | scope.contribute；202 任务 |
| 重建 rag | 新增 POST `R/rebuilds`；config_revision、mode=index_only/reprocess、幂等键和必要的编辑丢弃策略 | scope.admin；202 generation／任务 |
| 查看／重试任务 | 新增 GET `/v1/scopes/{s}/rag-jobs/{job_id}`，POST `.../{job_id}/retry` 使用新幂等键，保留原接受目标／输入 | 读 scope.read，重试 scope.contribute；200 状态／202 回执 |
| 多库检索 | 新增 POST `/v1/rag/query`；显式 targets、query、top_k、类型化 filters 和有界选项 | 每个目标 scope.read；200 命中和逐目标结果 |
| 原始输入 | 现有 Source GET 返回描述；新增 GET `/v1/scopes/{s}/sources/{type}/{id}/content` 返回快照流 | scope.read；200、媒体类型，不暴露存储凭据 |
| 软删除 document | 新增 DELETE `R/documents/{document_id}`，expected_version 取成员版本 | scope.contribute；200 回执 |
| 软删除 rag | 新增 DELETE `R`，expected_version 取运行状态版本 | scope.admin；200 回执 |
| 软删除 Source | 新增 DELETE `/v1/scopes/{s}/sources/{type}/{id}`，首期仅适用文档 Source 生命周期 | scope.admin；200 回执 |

DELETE 携带 Idempotency-Key，要求 expected_version 的操作将其作为查询参数。回执包含 operation_id、目标、state=deleted、
deleted_at 和 data_retained=true，不隐含清理任务。重复 DELETE 返回原回执。管理任务可报告删除状态，但不返回保留的正文；
普通内容 GET 在授权后返回 410，不提供 include_deleted 内容绕过选项。

创建空 rag 时原子创建初始运行／generation 元数据，不调度解析。配置 PUT 保存目标配置，导入使用生效配置，配置变化不静默重解析人工编辑。
显式 rebuild 在校验切换后激活目标快照，index_only 拒绝 parser／OCR／chunker 变化。重建期间的导入／编辑持久保存，切换前追平。
分别展示目标和生效 revision；重建失败保留旧生效配置与索引。
普通文档更新期间，有效发布 revision 无须等于最新 head 或目标代次，这是索引期间的预期差异；成功发布后才切换。
生命周期撤销始终优先于旧版可用的兜底规则。

文件输入使用已授权 Source binding 加相对路径，或同 Scope 精确 SourceTypeReference。客户端任意填写的服务器路径、URL 或快照位置
不构成访问授权。插件负责捕获字节和校验媒体／摘要，复用 Source 也须调度目标工作。接受前校验权限和批次形状；逐项采集失败可以与
成功项共存，但每个接受项必须原子保存 Source、身份预留、任务和 intent，明确报告部分接受。批次重试返回原逐项回执；
没有固定输入的采集失败使用新键重新提交，Worker 重试则继续使用已接受快照。

### 6.2. 典型请求与响应

**创建**沿用 family + content。以下 profile 是示例部署 binding，不是硬编码默认值。rag content schema 拒绝未知字段，不保存密钥或成员数组。

```json
{
  "family": "rag",
  "idempotency_key": "create-ops-01",
  "content": {
    "schema_version": "1",
    "title": "Operations SOPs",
    "summary": "Runbooks and screenshots",
    "profiles": {
      "parser": "markdown-v1",
      "ocr": "rapidocr-local-v1",
      "chunker": "heading-paragraph-v1",
      "embedding": "docs-embedding-v1",
      "retrieval": "hybrid-v1"
    }
  }
}
```

**导入**已有 rag，每项只选择一种输入形式：

```json
{
  "idempotency_key": "import-ops-01",
  "items": [
    {"item_key": "a", "namespace": "oceanbase-docs", "external_key": "README.md",
     "input": {"kind": "file", "binding_name": "local-documents", "path": "README.md"}},
    {"item_key": "b", "namespace": "ops-images", "external_key": "restart.png",
     "input": {"kind": "source", "source": {"source_type": "knowledge-document-input", "source_id": "image-42"}}}
  ]
}
```

202 响应包含稳定父任务、逐项 Source 引用和预留 document ID；内容尚未生成时 document=null。父任务计数区分接受和成功。
逐项回执暴露 stage=accepted/parse/chunk/index/publish、state、attempt、最新内容／可检索 revision 和 error。
父任务聚合可报告 partial_failure，不把它加入单项工作状态机。

**编辑**使用 PATCH `D/chunks/chunk-3`，If-Match 为 document GET 返回的 ETag：

```json
{"idempotency_key": "edit-03", "text": "Restart the follower before the leader.", "reason": "Correct OCR text"}
```

```json
{
  "document": {"family": "document", "artifact_id": "doc-1", "revision": 2},
  "index_job_id": "job-12",
  "latest_content_revision": 2,
  "searchable_revision": 1,
  "chunk": {"chunk_id": "chunk-3", "origin": "manual_edit", "verbatim": false}
}
```

**检索**的每个 rag 复用 ArtifactAddress。目标 revision 固定调用者所见的配置 head，过期时返回冲突。
响应单独标明生效索引／配置，它可能落后于目标配置。

```json
{
  "targets": [
    {"scope_id": "project-p", "artifact": {"family": "rag", "artifact_id": "ops", "revision": 1}},
    {"scope_id": "project-q", "artifact": {"family": "rag", "artifact_id": "product", "revision": 3}}
  ],
  "query": "How should I restart a follower?",
  "top_k": 10,
  "filters": {"media_types": ["text/markdown", "image/png"]},
  "retrieval": {"mode": "hybrid", "candidate_limit": 100}
}
```

每条命中包含 Scope、rag/document ArtifactReference、chunk ID／text／rank／score、SourceTypeReference、支持的定位、
origin/verbatim 和索引 generation。响应返回逐目标状态／错误及 partial。权限失败在检索前拒绝整个请求；已授权目标的运行失败
可返回明确部分结果，全部目标失败仍是错误。

### 6.3. 错误、并发与能力启用

| 情况 | 契约 |
| --- | --- |
| 相同键／摘要 | 校验当前授权／生命周期后重放稳定回执；删除重放其 tombstone 回执 |
| 同键不同摘要 | 409 idempotency_conflict，不创建新工作 |
| 缺失／过期内容或配置 ETag | 复用 428／412 前置条件响应 |
| 过期运行／成员版本、rag 配置或正在重解析 | 409 和机器可读原因，不部分提交 |
| 目标／证据已删除 | 授权后 410 resource_deleted，错误不包含正文 |
| 不支持输入、非法配置、过长编辑 | 422；接受前传输字节限制可返回 413 |
| 缺少后端／模型能力 | 503，不当作无命中 |
| 已接受任务异步失败 | 任务 failed/retry_wait；202 不表示处理完成 |

沿用现有错误封装：

```json
{"error": {"code": "resource_deleted", "message": "The requested document is unavailable.", "details": null}}
```

内容写入比较 document head，索引写入比较精确目标／publication 版本，都原子更新状态和持久工作。
重放查询先于旧写入前置条件的再次检查，但不能绕过当前权限／删除。失败命令回滚回执和业务写入，暂存内容完整前不可见，
重放响应也不能泄露随后被删除目标的文字。

迁移先创建 schema／索引／能力声明，再启用功能。新文档 Source 在接受时创建生命周期行，缺少必要生命周期／归属是完整性错误，
不能默认允许读取。无关 family 不变；缺少要求的后端／profile 能力时拒绝启用。通过公开 API 验证升级／重启、重复请求、
写后读、发布竞态和删除过滤，不只直接写表验证。

## 7. 验收与交付

使用 `oceanbase/oceanbase` 固定 commit 的 Markdown，以及文字／定位已核验的独立 OCR 图片。SQLite、SeekDB、OceanBase 均验证：

- 同 Scope 多个空知识库、自动文档归属、精确引用、单库过滤，以及显式授权的同 Scope／跨 Scope 查询。
- 已有 Source 复用、幂等重试、无 document 的首次失败、进程重启、内容／成员／工作原子提交，以及索引失败时旧版本可检索。
- 真实 RapidOCR、非法／不支持输入和无文字结果、Markdown 图片引用保留，以及不扫描整篇 JSON 的 chunk 全文／向量检索。
- 人工编辑溯源、完整快照、固定 revision 分页、全文／向量映射更新、重解析冲突、迟到 Worker 拒绝，以及并发更新下的 generation 切换。
- Source／document／rag 软删除，历史／缓存／原文／编辑 Source 读取校验、物理数据保留、幂等删除，以及无关文档、替代 Source 版本和 rag 配置的保留。

沿用现有 Issue：#2441 负责基础与导入，#2442 负责处理／内容／索引生命周期，#2443 负责检索和接口，#2444 负责运行可见性与三后端验收。
实现可采用一个集成分支和 PR，按这四个阶段评审。本 RFC PR 不完成这些实施 Issue。随实现发布支持格式、模型版本、资源限制和恢复说明，
不从功能样本推断生产容量。

# 缺点

完整快照使单块编辑也增加存储和写入成本。区分内容版本与检索版本增加读取和诊断复杂度。专用消费限制、定向任务、family writer 和
撤销机制需要超出插件声明的框架适配。三后端增加迁移和索引测试成本，RapidOCR 模型分发及平台兼容性也需要明确维护。软删除保留全部数据和索引，不是擦除能力，需要监控存储容量。

# 设计理由与替代方案

- **每个知识库一个 Scope：** 可以复用现有隔离，但会把集合组织绑定到授权和项目身份。多 rag 将这些职责分离，需要隔离时仍使用不同 Scope。
- **将 chunks 存在可变 Source 中：** 混淆原始证据和派生内容，违反 Source 不可变原则。
- **每个 chunk 一个 Artifact：** 将整篇文档更新拆成多个独立 Artifact 生命周期，难以表达原子文档版本。
- **全部 chunks 放在 Artifact JSON 中：** 写入简单，但片段读取和索引依赖大 payload。
- **独立知识库主表：** 重复已有 Artifact 身份、版本和管理设施。
- **只靠 Source journal 生成：** 无法可靠表达空 rag 创建或已有 Source 复用引发的工作。

选定模型保留整篇文档身份，同时为 chunks 提供适合检索的物理存储和明确发布语义。不提供这一能力，会使各应用自行拼装不一致的导入、
溯源和删除流程。

# 既有设计

本提案基于仓库的 [Source 插件设计](2012-source-plugin-system.md)、[Artifact 插件设计](2175-artifact-plugin-system.md)、
[Source 模型](1400_source_definition_and_observation_model.md)、[Artifact 处理监督](1515_artifact_processing_supervisor.md)、
[Source／Artifact API](1437_source_artifact_rest_api.md) 和 [Topic Memory](1417_topic_memory.md)。复用其扩展、溯源、调度和读取边界，
增加显式集合管理和文档专属存储／发布。Topic Memory 的专属明细／索引 schema 不作为共享文档 schema。

# 待解决问题

以下内容须在对应实现中固定并测试，不延后身份、原子性、消费限制和撤销语义：

- 实现上述契约的 Python 能力／writer 签名、迁移 DDL 和后端索引布局。
- 快照、解码图片像素、manifest、chunk、分页、Worker 并发、超时和重试限制。
- 固定的 RapidOCR／ONNX／模型版本、支持的图片格式／平台，以及默认切块／embedding profiles。
- 后续保留／擦除策略及过期引用响应；首期保留历史和软删除数据。

完整编辑文档导出需要明确重叠和编辑合并契约。独立 rag ACL、跨部署联合查询和共享 document 归属需要单独设计，不隐式提供。恢复和永久擦除还需明确依赖、授权与审计规则后才能开放。

# 后续可能性

更多解析器、存储后端、自动采集、切块策略、rerank 和受限 agentic 检索可以复用组件边界。测量到实际存储压力后，可考虑跨 revision
共享不可变 chunk 正文。这些扩展必须保留精确证据、目标隔离、发布与删除语义，不作为首期知识库流程的前置条件。
