---
title: "RFC 0000：基于 rag Artifact 的文档知识库"
description: "通过插件实现文档知识库，保留不可变 Source、分块版本、混合检索与可恢复的后台处理。"
---

- Proposal Name: `rag_knowledge_base`
- Start Date: 2026-11-10
- RFC PR: [#0000](https://github.com/oceanbase/powercontext/pull/0000)
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
| 处理记录 | 持久目标请求、阶段／结果、重试、租约及可恢复的索引／清理工作 |

在语义匹配时复用 Artifact、head、lineage、tag 和处理设施。不新增重复的知识库／document／revision 主表，也不将文档数据混入
Topic Memory 专属 chunk 表。物理表名和各后端辅助索引在实现时确定。

首期每个 revision 保存**完整 chunk 快照**。100 块的文档保存一条 manifest 和 100 行明细；编辑一个块后再保存一条 manifest
和完整 100 行快照。校验数量、唯一顺序、结构引用，以及覆盖有效文本、顺序和证据映射的摘要。分批暂存数据在完整 revision 提交前不可见。
这一选择接受存储放大，以简化版本读取和恢复；跨 revision 共享 chunk 正文留待后续。

Artifact 主体不能保存无界的全文／chunk／向量数组。检索直接访问 chunk 索引，再批量读取精确命中和同版本邻接块。通用 Artifact
读取返回 manifest；明细分页游标固定 revision、视图和顺序。原始解析证据与编辑后的有效 chunks 分开读取，不能直接拼接重叠 chunks
来导出编辑后的整篇文档。

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
| 快照准备 | 接受前保存字节；接受失败可能留下待清理的无引用快照 |
| 导入接受 | Source 接受／复用、已验证的目标请求和持久调度意图 |
| 内容提交 | 完整 manifest／明细、溯源、已验证的成员关系、内容回执和索引工作；人工编辑在此创建系统 Source |
| 索引发布 | 校验完整索引、精确内容目标、生效配置、执行资格和生命周期；切换可检索选择及可见代次 |
| Source 撤销 | 确认删除前持久化生命周期 tombstone、推进可见代次并记录级联清理 |

OCR、embedding、DDL 和批量索引构建都在上述短事务外执行。每次提交重新校验注册／租约栅栏、目标状态及输入／配置／删除代次。
新的编辑或导入使旧发布任务失效。持久工作记录是恢复依据，进程内通知仅加速唤醒。

内容 head 与可检索 revision 分离。索引失败时保留旧发布版本，同时展示新内容和错误。rag 重建使用新 generation，在切换前追平已接受
变更到经验证的水位，不能用部分构建替换当前 generation。重建一个知识库不得切换其他库的 generation。文件系统／数据库备份恢复后，
应验证被引用快照和索引一致性，再开放恢复后的检索。

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

删除原始 Source 是 Scope 授权下的操作，撤销所有依赖 document revision，包括人工编辑版本及同 Scope 其他 rag 中的依赖。
成员引用不阻止删除，rag 配置保留。撤销立即约束检索、缓存、原文读取及普通／历史 Artifact 和 chunk 读取，物理清理异步执行并可重试。
在途结果返回前重新校验可见性；已经发送的响应无法收回。

清理范围包含依赖正文、OCR／解析缓存、索引，以及携带正文的编辑 Source／审计 payload，可保留最小的无正文回执。
普通编辑不改写原始 Source payload，显式擦除使用独立生命周期。移除一个知识库不删除共享 Source；删除旧 Source 不撤销仅依赖其替代
Source 的 revision，也不能保留被撤销旧版作为检索兜底。不删除用户原文件，受管理快照仅在没有存活引用时回收。
迟到 Worker 必须受与撤销相同的删除栅栏约束。

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

最终 URL 和 schema 在实现时写入 `openapi/powercontext.yaml`，随后生成客户端并运行契约检查。本文确定操作语义，不给出尚未实现的
curl 示例。新 family 和明细表通过三个后端的版本化迁移引入；既有 Source 和 Artifact family 保持原契约，不自动转换或加入文档处理。

## 7. 验收与交付

使用 `oceanbase/oceanbase` 固定 commit 的 Markdown，以及文字／定位已核验的独立 OCR 图片。SQLite、SeekDB、OceanBase 均验证：

- 同 Scope 多个空知识库、自动文档归属、精确引用、单库过滤，以及显式授权的同 Scope／跨 Scope 查询。
- 已有 Source 复用、幂等重试、无 document 的首次失败、进程重启、内容／成员／工作原子提交，以及索引失败时旧版本可检索。
- 真实 RapidOCR、非法／不支持输入和无文字结果、Markdown 图片引用保留，以及不扫描整篇 JSON 的 chunk 全文／向量检索。
- 人工编辑溯源、完整快照、固定 revision 分页、全文／向量映射更新、重解析冲突、迟到 Worker 拒绝，以及并发更新下的 generation 切换。
- 共享依赖上的 Source 撤销，历史／缓存／原文读取校验，可重试清理，以及无关文档、替代 Source 版本和 rag 配置的保留。

沿用现有 Issue：#2441 负责基础与导入，#2442 负责处理／内容／索引生命周期，#2443 负责检索和接口，#2444 负责运行可见性与三后端验收。
实现可采用一个集成分支和 PR，按这四个阶段评审。本 RFC PR 不完成这些实施 Issue。随实现发布支持格式、模型版本、资源限制和恢复说明，
不从功能样本推断生产容量。

# 缺点

完整快照使单块编辑也增加存储和写入成本。区分内容版本与检索版本增加读取和诊断复杂度。专用消费限制、定向任务、family writer 和
撤销机制需要超出插件声明的框架适配。三后端增加迁移和索引测试成本，RapidOCR 模型分发及平台兼容性也需要明确维护。

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

- 精确的 family 能力／writer 扩展、API schema、迁移名称和后端索引布局。
- 快照、解码图片像素、manifest、chunk、分页、Worker 并发、超时和重试限制。
- 固定的 RapidOCR／ONNX／模型版本、支持的图片格式／平台，以及默认切块／embedding profiles。
- 历史 revision 保留期、暂存／快照垃圾回收及过期引用响应。

完整编辑文档导出需要明确重叠和编辑合并契约。独立 rag ACL、跨部署联合查询和共享 document 归属需要单独设计，不隐式提供。

# 后续可能性

更多解析器、存储后端、自动采集、切块策略、rerank 和受限 agentic 检索可以复用组件边界。测量到实际存储压力后，可考虑跨 revision
共享不可变 chunk 正文。这些扩展必须保留精确证据、目标隔离、发布与删除语义，不作为首期知识库流程的前置条件。
