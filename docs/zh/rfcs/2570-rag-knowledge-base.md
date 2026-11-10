---
title: "RFC 2570：基于 rag Artifact 的文档知识库"
description: "复用 Source、Artifact 和后台处理设施，通过 OpenDAL 接入文档、简单 Markdown 切块和插件独立检索配置构建知识库。"
---

- Proposal Name: `rag_knowledge_base`
- Start Date: 2026-11-10
- RFC PR: [#2570](https://github.com/oceanbase/powercontext/pull/2570)
- Tracking Issue: [#2440](https://github.com/oceanbase/powercontext/issues/2440)
- Related Issues: [#2441](https://github.com/oceanbase/powercontext/issues/2441), [#2442](https://github.com/oceanbase/powercontext/issues/2442), [#2443](https://github.com/oceanbase/powercontext/issues/2443), [#2444](https://github.com/oceanbase/powercontext/issues/2444)

# 概述

通过文档 Source 插件和 document、rag 两个 Artifact family 提供知识库。一个 Scope 可有多个用户创建的 rag，
每个文档 Source 和 document 唯一归属一个 rag。OpenDAL 采集本地文件，Markdown 与图片 OCR 共用简单 Markdown 切块策略，
复用 Artifact Worker，在内容和索引准备好后一起提交 document revision。复用核心元数据表结构、CRUD、监督和通用索引存储，
仅增加文档明细与目标记录。本文是建议契约，不表示功能已发布。

# 动机

业务描述和 SOP 需要完整文档的片段检索、精确出处、显式更新和可预测的删除行为。记忆提取无法保留每个片段。
首期应覆盖这条流程，不另建任务平台、逐 rag 索引代次系统或重复 Artifact 主表。

Source 快照和历史 document revision 是证据，知识库检索是派生投影，与 Memory 检索数据和默认配置隔离。
复用基础设施，但不能把现有插件声明视为已具备定向导入、分行 chunk 存储和删除校验的全部能力。

# 使用层说明

1. 在已有 Scope 中通过通用 Artifact 创建 rag。
2. 选择 rag 和本地目录／文件，客户端使用配置好的本地 OpenDAL Source 插件；用户无需提供 Source ID、namespace、hash 或 document ID。
3. 查看逐文件最新处理状态，输入已接受不代表内容可检索。
4. 检索指定 rag 的文档，或显式选择多个已授权 rag。
5. 修改文件后再次导入：相同且已处理成功的输入跳过，内容变化生成新 Source 和同一 document 的新 revision。
6. 编辑或删除 chunk 都生成新 document revision，旧 revision 及其 chunk 内容保持完整。

Scope 仍是授权边界。Source／document 独占归属 rag，不跨库共享逻辑身份。同文件导入两个 rag 会产生不同 Source／document，
底层快照可按实际需要去重。首期支持 Markdown 和独立导入的文字图片，OCR 使用 RapidOCR + ONNX Runtime。
Markdown 内图片仅保留引用，不抓取、不自动 OCR。SQLite、SeekDB、OceanBase 均须支持完整流程，一个部署选择一种持久化后端。

PDF、Office、普通 TXT 导入、语义／LLM 切块、复杂版面／表格重建、定时目录同步、每 rag 独立 embedding 空间、
单库独立无停机索引切换、完整管理界面和大规模语料性能门槛不属于首期。后续界面使用逐文件状态契约。

# 技术说明

## 1. 归属与插件配置

```text
Scope
  +-- rag K1 -- 拥有 --> 原始 Sources -- 派生 --> document revisions -- chunks
  +-- rag K2 -- 拥有 --> 独立 Sources/documents，即使本地文件相同

Source 插件：OpenDAL 采集与不可变快照读取
Document 插件：Markdown / OCR -> Markdown -> chunks、内容版本和溯源
rag 插件：集合管理、检索策略和索引适配
共享设施：Source/Artifact API、Supervisor/Worker、事务、数据库／搜索适配
```

rag 是配置和集合身份，不是消费 Source 生成的结果，可以为空。名称／配置使用 Artifact revision，删除使用现有 head 治理状态。
不另设 rag 运行状态表或运行状态 API。解析进度属于导入文件，不生成 rag 配置 revision。

document 代表整篇文档，稳定身份范围为 `(scope, rag, collection, relative_path)`，chunks 是 revision 内部明细。
collection 由系统根据注册的存储 binding／instance 和规范化根目录生成，不使用每次变化的扫描 ID。
Source payload 保存不可变的所属 rag、原路径、相对路径、媒体类型、内容摘要和受管理快照引用。任务固定处理配置快照，
可变状态和解析结果不写回 Source。专用 Source 仅供 document 链路直接消费，在选择、执行、提交时都校验。

三个逻辑插件可在一个安装包中交付。知识库插件包独立管理 embedding／索引配置，与 Memory 等主路径分离。
显式插件配置优先；未指定时，可在安装时用主路径的兼容值初始化并保存解析后的配置快照。保存凭据引用，不把密钥复制进 Artifact。
之后主路径配置改变，不静默改变插件 profile。配置缺失或不兼容则拒绝启用。
Parser／chunker 可按 rag 配置，首期 embedding／索引为插件级配置，在各 Scope 的 document family 内共用。

可以复用模型调用和 SQL 适配代码，但按 family、索引定义、向量空间／profile 身份隔离，查询不隐式混入 Memory 数据。
插件模型／维度变化沿用现有 family 重建机制及其可用性限制，不新增逐 rag 发布协议。

## 2. OpenDAL 导入与重复判断

通过现有 OpenDAL／opendalfs 集成列举和读取本地文件。插件 instance 必须运行在所选路径实际存在的机器上，
远端 Server 不能将客户端本地路径当作自己的文件。相对路径在配置根目录下解析，拒绝越界，路径规范化和符号链接策略保持稳定。
接受 Source 前将原始 Markdown 字节／图片保存成受管理不可变快照。对实际保存的字节计算 hash，不能对第一次读取算 hash、
再处理已经变化的第二次读取。读取失败或检测到采集不稳定时允许重试。

```text
file_key       = (scope_id, rag_id, collection_id, normalized_relative_path)
content_digest = SHA256(采集到的原始字节)
source_key     = hash(Source schema 版本, file_key, content_digest)
processing_key = hash(content_digest, parser/OCR/model 版本及配置,
                      Markdown 规范化版本, chunker 版本及参数)
index_key      = hash(完整 embedding 输入, 插件 profile 和投影版本)
```

mtime／大小可辅助选择工作，但不能证明内容相同。hash 碰撞时比较完整身份，不能截断路径或仅按摘要合并文档。
用户可见 Source ID 由框架结合插件的确定性身份规则生成。复用已有不可变 Source 时保留第一次 canonical 采集元数据，
不覆写 mtime 或注释。

| 观察结果 | 处理规则 |
| --- | --- |
| 同文件／内容／处理指纹，已有成功结果 | 复用 Source 和 document，不生成新 revision；另查索引是否就绪 |
| 相同目标正在等待／运行 | 合并到现有目标，不重复解析 |
| 输入相同，但上次处理失败 | 保留固定 Source，允许有界重试；采集 checkpoint 不等于处理成功 |
| 同路径，字节变化 | 新增 Source，生成同一 document 的新 revision |
| 字节相同，parser／OCR／chunker 变化 | 复用 Source，显式重处理并生成新 revision |
| 仅 embedding／索引 profile 变化 | 使用已提交 chunks 重建，不再次 OCR |
| 路径或 rag 不同，字节相同 | 不同逻辑文档和 Source，仅可复用物理快照 |
| 遇到已删除身份 | 不自动恢复；首期无恢复接口，需显式采用新身份 |

文件恢复到曾经出现过的字节时，也要比较当前目标／结果；找到旧 Source 不代表当前 document 已是该内容。
首期改名按新路径处理，文件消失不隐含删除。相同文件重扫不能覆盖人工编辑／移除。
含人工修改的文档遇到输入变化或显式重处理时，要求 manual_edits_policy=discard，否则保留旧提交版本并报告可操作的冲突。
原始输入 Source 可以已经采集，但内容处理仍保持阻塞。

目录导入复用 Source collect 能力发现和现有 add／status／control 操作。客户端在插件 input 中生成 scan_token，
标识一次显式扫描目标。同 input 重试 add 复用目标；用户再次导入使用新 token，但 collection／file 身份不变。
插件每 token 只扫描一次，并继续等待后续目标，不定时重扫。现有 Source status 表示采集状态，不是完整文档处理或批次历史。
新扫描必须同时检查文档处理状态和采集 checkpoint，避免“采集未变”掩盖下游失败。

## 3. Markdown 规范化与简单切块

两种输入形成统一 Markdown 视图：

```text
Markdown 字节 -> markdown-it-py 结构／原文字节映射 ---+
                                                    +-> 规则切块
图片 -> RapidOCR -> 文字行／区域 -> 规范化 Markdown --+
```

OCR 保留真实文字、顺序、置信度和原图区域，考虑预处理坐标变换。Markdown 转换只做语法转义、文字行／段落组织，
不猜标题、不用 LLM 修正文案、不重建复杂表格。保留规范化文字跨度到 OCR 行和原图区域的映射。
规范化 Markdown 是派生内容，不能替代图片 Source；定位必须说明真实精度。

按标题、段落、列表和代码块处理，合并短块直到接近 max_chunk_chars，超长块再按句子／长度拆开，
必要时在拆分边界保留 overlap_chars，并校验 overlap 小于最大长度。保留标题路径、顺序和续块标记，不建设独立章节树系统。
Embedding 前还要检查真实模型 token 预算，包括标题／前缀上下文，不静默截断。改变边界的切块配置变化需要版本化。

媒体校验、非法 UTF-8、损坏／不支持图片、无识别文字、模型缺失、像素超限和超时分别处理，固定并验证真实 RapidOCR、ONNX 和模型版本。
规范化 Markdown 可以在处理期间计算，首期不要求独立解析缓存。Chunks 保存有效文字和有界来源映射，不保存向量。
Artifact manifest 有界，不嵌入全部 chunks。

## 4. 持久化：核心表结构不变，三张支撑表

本方案不修改现有核心元数据表定义。新 family 记录、声明和索引沿用既有 schema，这是迁移约束，不代表无需修改处理代码。

| 复用设施 | 用途与必要代码适配 |
| --- | --- |
| `pc_sources`、Source journal、connector checkpoints | 不可变输入、采集身份和采集进度 |
| `pc_artifacts`、`pc_artifact_heads` | rag 配置、document revision manifest、active/retired 状态和治理 CAS |
| `pc_artifact_lineage_sources`、locators、Artifact lineage、tags | 原始输入、系统编辑 Source、base revision 和标签，不表示可变成员关系 |
| `pc_artifact_processing_intents`、租约、binding 状态 | 持久唤醒、显式请求代次、执行资格和注册栅栏 |
| `pc_artifact_search_states`、coverage、changes | 现有 Scope/family 构建协调及当前版本索引维护 |
| `pc_artifact_search_text`、vectors、向量空间／索引定义 | 片段级检索投影，family=document、fragment_id=chunk_id |

不新增 pc_rag_runtime、逐 rag generation／publication、通用 jobs／receipts 系统、独立 nodes／目录主表。
不把状态塞进无关 Scope settings，也不改 Source payload 模拟可变元数据。文档归属必须显式，不能从标签或证据 lineage 推测。

以下三张支撑表是建议实现基线。使用 UTC 时间、字节精确身份比较、有界 JSON 和满足各后端限制的键长度。
迁移必须核实三个后端的实际布局；只有唯一性和恢复语义相同时才能用既有存储替代。

| 表 | 主键／唯一键 | 最小字段与职责 |
| --- | --- | --- |
| `pc_document_imports` | PK `(scope_id, document_id)`；UQ `(scope_id, rag_id, collection_id, path_hash)` | 完整相对路径、binding name、当前／目标 SourceRef 与摘要、处理／配置快照和指纹、目标代次、operation（import/reprocess/edit）、有界待处理编辑、request ID/digest、state/stage、attempt、重试时间、lease token/expiry、结果 revision 和 error；合并身份预留、成员关系和逐文件最新工作 |
| `pc_document_chunks` | PK `(scope_id, document_id, revision, chunk_id)`；UQ `(scope_id, document_id, revision, ordinal)` | text、内容摘要、标题路径、续块／重叠元数据、规范化跨度和原文字节／图片定位、origin、编辑／base 引用；完整不可变 revision 快照 |
| `pc_document_source_states` | PK `(scope_id, source_type, source_id)` | 不可变 owner rag_id、document／文件身份、state=active/deleted、删除代次／时间；原始 Source 及其系统编辑 Source 的独立可变生命周期 |

解析前预留 document ID，但不要求 Artifact head 已存在。提交后明细引用同 Scope 的 family=document，每个 Source owner
匹配预留记录和不可变 payload。Manifest 保存所属 rag、root SourceRef、schema／处理指纹、chunk 数量／摘要和人工修改标记。
100 块的 revision 保存一条 manifest 和 100 行明细；移除一块生成新 manifest 和 99 行，不改原来的 100 行。
未变行可高效复制，但不完整快照不能可见。历史版本按精确引用读取，不能用当前 head 解释历史。

按 `(scope, rag, document_id)` 索引导入记录，按 binding／状态／重试时间查待办，按 revision／ordinal 读 chunks，
按 `(scope, rag, document_id)` 索引 Source 状态支持删除传播。核实既有反向 lineage 查询路径以支持原始 Source 撤销。
Hash 键同时保留完整原始身份用于比较。Source 行归属不随状态／进度改变。
导入表保留最新目标和结果，不记录无界批次或命令历史。Source 接受前的错误归采集诊断；完整持久批次历史需另行明确扩展范围，
不能用元数据行里的无界 JSON 数组实现。

## 5. 复用 Worker，内容与索引原子提交

应用服务协调三个插件，不是新增插件或服务。Source 接受／复用、生命周期初始化、document 目标预留／更新和
processing_intents.request() 同事务提交，唤醒通知丢失后也能从持久目标恢复。
仅提交 Source 的入口也必须调用同一接受能力，不能绕过目标验证。

Supervisor 保持 Scope／binding 调度，document processor 选择导入表中的待办，不向所有 rag 广播 Source。
已有 Source 的重试／重处理使用显式请求，不伪造正 source_through，也不为纯目标工作推进 Source cursor。

```text
pending -> running -> succeeded
              +--> retry_wait -> pending
              +--> failed / blocked
新目标 -> 目标代次递增，旧执行结果过期
rag/document/Source 删除 -> 受影响目标 cancelled，禁止后续提交
```

每个逻辑文档只处理一个当前目标。相同待办合并，新的文件版本可以取代旧解析；人工编辑要求不存在冲突的待处理变更。
持久保存操作、固定输入／配置、base revision 和最新请求 token，使重启后能重试相同工作。
当前 token／digest 重放返回当前状态，同 token 不同内容冲突；过期 token 版本不能重新创建工作，不承诺无限期的历史回执重放。

| 边界 | 工作 |
| --- | --- |
| 事务外 | OpenDAL 快照采集、解析／OCR、Markdown／chunk 构造、embedding 和索引投影准备 |
| 接受事务 | 校验归属／生命周期和前置条件，接受／复用 Source 与目标状态，提交现有 processing intent |
| 内容事务 | 重验目标代次、base head、所属 rag／Source 生命周期、插件 profile/build 和 Worker 栅栏；一起提交 manifest、完整 chunks、lineage、必要系统编辑 Source、head、索引和目标成功状态 |
| 删除事务 | retire 父 Artifact 或标记 Source 已删，使目标失效；利用现有 invocation 和目标行持久化可恢复的传播工作 |

模型调用和 DDL 不在内容事务里执行。Chunk 投影适配器从完整 chunk 快照准备索引，不能假设有界 manifest 包含全文。
提交时同时校验快照和投影指纹，现有通用 JSON 字段投影不经适配并不满足要求。

正常更新失败保留原提交 head 和索引，不生成“已可读但尚未索引”的新 revision。历史内容可精确读取，检索使用当前索引 revision。
共享 document family 索引重建或不可用时，延后新文档提交并明确报告不可用；不能静默走通用框架“重建时仅提交内容”的路径，
不能回退 Memory 检索，也不承诺无停机切换。重建读取已提交 chunks，不重新 OCR。
SQLite、SeekDB、OceanBase 都需通过真实适配验证事务／读取；原生 ANN 无法保证过滤语义时，使用限定集合精确检索或明确报能力不足。

## 6. 编辑、chunk 移除与软删除

编辑命令支持已有 chunk 的 replace_text 和 remove，使用 document If-Match。Worker 构造完整新 revision，重新校验数量／顺序／摘要，
准备索引后原子提交。允许删除最后一块：有效内容为空、索引片段数为零，但保留可读 manifest 和溯源。
未变 chunks 保留逻辑 ID，重切块生成新 ID；引用始终带 revision。Remove 只影响新快照，不物理删除历史 chunk 行。

成功提交时创建 role=lineage_only、operation=artifact_replace 的系统 Content Source，记录精确新 revision、base revision 和编辑／移除命令。
该 Source 用于修改溯源，不进入普通解析。显式保留原始文档 Source 依赖，不能假设通用替换已自动继承。
修改文字标记 origin=manual_edit、verbatim=false，原图位置只是修改依据，不表示新文字逐字存在于图片。
移除 chunk 也算人工修改，重解析不能静默覆盖。完整实际 embedding 输入／profile 未变时可复用计算，但仍更新新 revision 的索引映射。

| 删除操作 | 状态与影响 |
| --- | --- |
| Source | sidecar 标记 deleted，不改 payload；当前输入仍为它的 document retire，相关历史 revision 和编辑 Source 不可读；仅依赖替代 Source 的当前版本保留 |
| Document | Artifact head retire，阻断全部 revision／chunks 和待办；不删除原始 Source |
| rag | head retire，立即阻断全部所属内容，再持久完成所属全部 Source 版本和 document head 的删除／retire 标记，包括尚未处理成功的目标 |
| Chunk | 生成不包含该块的新 document revision，保留历史内容 |

Retire 复用 lifecycle_state=retired 和 governance CAS，不新增第二套 rag／document 删除标记。
Source payload 不可变，因此删除放在 sidecar。重复删除幂等。
仅预留、尚无 head 的 document 在导入行以 document_deleted 原因终止，不为删除而创建空 Artifact；后续扫描不能静默恢复该身份。
删除待处理替代 Source 只取消其目标，不 retire 依赖另一 Source 的已提交版本。
较大级联可分批完成：已 retire rag 和持久归属使未完成工作可重新发现，父状态立即阻断读取／提交。
Source 不跨 rag 共享逻辑归属，因此删库不误删其他库的 Source。保留 OpenDAL 快照和 canonical／历史内容，不删除用户原文件；
首期不提供恢复或永久擦除。

检索及通用精确／历史／chunk 读取校验所属 rag、document head 和该 revision 的根 Source。
历史 base revision 链接不让完整重解析的替代版本传递依赖旧 Source。系统编辑 Source 也检查其 document／rag 目标。
原始 Source 读取检查自身状态和 owner rag；单删 document 后原始 Source 仍可读，删其 rag 后不可读。
必须增加 family 读取校验，不能只依赖通用 retirement，因为既有行为可能仍允许精确读取。

搜索索引是派生结构，不是历史证据，沿用现有替换／移除／重建维护。软删除保留业务数据，不承诺原样保留每个历史索引条目。
即使旧索引暂存，排名前也必须排除，包括删除传播恢复期间。不论物理索引是否清理，都必须检查父对象／Source 和提交栅栏。
已经发送的响应无法收回。

## 7. 统一接口契约

以下路由为建议复用或扩展，当前 schema 尚未支持全部 family／filter。实现时修改 openapi/powercontext.yaml 并重新生成绑定。
复用权限、错误封装、Artifact／Source 引用、分页、ETag 和 SDK／MCP 映射。
不把文档文件塞进目前只接受 built-in content 的通用 create-Source 命令，使用 Source 插件协议。

约定 `R=/v1/scopes/{s}/artifacts/rag/{r}`，`D=/v1/scopes/{s}/artifacts/document/{d}`。

| 操作 | 复用或必要扩展 | 语义 |
| --- | --- | --- |
| 创建／配置 rag | 现有 POST `/v1/scopes/{s}/artifacts`、GET/PUT `R`、revisions/tags | family=rag，完整配置替换和既有 If-Match；创建空库不解析 |
| 导入目录／文件 | 现有 POST `/v1/source-management/add`，使用发现的 collect capability 和 instance | 插件 input 携带 owner rag 和本地根目录／文件；Source 回执仍表示采集，不新建 rag import/job 封装 |
| 采集状态／控制 | 现有 source-management status 和 target／instance controls | 不声称 collect add 会重新调度 stopped target，也不把 status 当完整批次历史 |
| Source／document | 现有 Source list/get、Artifact list/get/history | 缺少时增加经过验证的 owner-rag 过滤；document GET 返回有界 manifest，不返回所有 chunks |
| 最新逐文件进度 | 新增 GET `R/processing`，cursor／state 过滤 | 最新目标、阶段、attempt／error、请求代次和已提交 revision，包含无 Artifact 的预留记录 |
| 重试／重处理 | 新增 POST `D/processing`，首次失败无 head 时也可使用预留 document ID | action、expected_target_generation、request_id、可选丢弃策略；202 当前目标回执 |
| 读取 chunks | 新增 GET `D/revisions/{rev}/chunks` | 复用 items/next_cursor，精确 revision 的有效 chunks 和出处 |
| 编辑／移除 chunk | 新增 POST `D/edits` | document If-Match、有界类型化操作和 request_id；202 仅表示待办，原子成功后才生成新 revision |
| 检索 | 现有 POST `/v1/scopes/{s}/artifacts/document/search` | 沿用 query/mode/limit/fusion，要求经过验证的 filters.rag_ids；可选匹配片段不替代完整 Artifact 结果 |
| 软删除 Artifact／Source | 给现有精确 Artifact／Source 资源路径增加 DELETE | family writer／Source 生命周期服务校验归属；Artifact 使用 expected_generation（治理 CAS）；document 为 scope.contribute，rag／Source 为 scope.admin |

rag content 保存标题／摘要和 parser／OCR／chunker 配置或引用，不保存运行进度、成员或主路径 embedding 配置。
修改配置影响后续接受的目标；已有 document 通过显式重处理更新，并继续保护人工修改。

创建示例，沿用通用 Artifact 封装：

```json
{"family":"rag","content":{"title":"Operations SOPs","summary":"Runbooks and screenshots","parser":"markdown-v1","ocr":"rapidocr-local-v1","chunker":{"max_chunk_chars":1200,"overlap_chars":100}}}
```

目录采集示例，capability／instance 名称和参数是部署发现值的示意：

```json
{"scope_id":"project-p","capability":"knowledge-documents.collect","capability_version":"1","instance_id":"local-documents","input":{"rag":{"family":"rag","artifact_id":"ops","revision":1},"root":"/work/docs","scan_token":"scan-01"}}
```

接受时验证目标 rag 引用；归属固定为 rag ID，处理固定已接受配置。scan_token 不是文档身份，不改变 Source 协议顶层 schema。
客户端重试扫描沿用 token，明确再次扫描使用新 token。

移除 chunk 示例，POST `D/edits` 携带当前 document ETag：

```json
{"request_id":"edit-03","operation":{"kind":"remove","chunk_id":"chunk-3"},"reason":"Remove an obsolete instruction"}
```

```json
{"document_id":"doc-1","request_id":"edit-03","target_generation":4,"state":"pending","committed_revision":2}
```

回执不能提前虚构 revision 3。稍后 GET processing 返回成功和新提交版本，或失败且 revision 2 不变。
文字替换使用 kind=replace_text 并提供 text，不把局部操作伪装成通用 PUT 的完整 content。
当前 request ID/digest 重放复用目标，冲突或过期请求返回冲突／前置条件错误，不静默再次执行。

同 Scope 检索沿用通用请求：

```json
{"query":"How should I restart a follower?","mode":"hybrid","limit":10,"filters":{"rag_ids":["ops"]},"include_scores":true}
```

results 继续返回完整 document Artifact manifest 和 lineage。在搜索项上增加可选类型化 matches，携带有界精确 chunk 命中：
chunk ID、text、locator、origin/verbatim 和片段得分。这是响应投影，不写入 Artifact content。
limit 仍限制返回 Artifact 数量，每 document 的片段数量另有能力上限。只接受已声明 document filters/options，
不支持的 rerank 明确报能力错误；未知 filter 或未指定 rag 均不能扩大查询范围。

跨 Scope 查询由客户端／helper 接受明确的 `(scope, rag)` 目标，先逐个授权，再分组调用同一个现有搜索操作并按排名融合。
保留 document／chunk 身份和逐目标错误，不比较不兼容原始分数，不把失败当无命中。
首期不新增独立 /rag/query 协议；HTTP、Python、MCP 使用同一 family 操作，必要时执行有界编排。

| 情况 | 响应 |
| --- | --- |
| 缺少／过期 document／配置 ETag | 既有 428／412 前置条件错误 |
| 目标代次／请求冲突、存在待处理编辑或缺少人工修改丢弃策略 | 409 与可操作错误码 |
| 对象或证据已删除 | 授权后 410，错误中不返回保留正文 |
| 格式／配置非法或输入超限 | 422；传输层可用 413 拒绝大请求 |
| 索引／模型不可用或重建中 | 503，不静默降级纯全文或检索其他 family |
| 已接受处理失败 | 最新目标 failed/blocked/retry_wait，Source 接受和处理成功分开 |

错误继续使用现有结构，例如：

```json
{"error":{"code":"resource_deleted","message":"The requested document is unavailable.","details":null}}
```

## 8. 实现与验收

复用框架仍需文档 Source 接受 hook、Worker 逐文件选择、chunk 明细投影适配、top-k 前归属过滤、所有读取的生命周期校验，以及
同事务 family writers。不能因为复用表而把全文塞回 manifest，或声称通用 JSON 投影已经足够。
不计划修改核心元数据列；若实现发现真实缺口，加法领域迁移和 OpenAPI 扩展仍需评审。

SQLite、SeekDB、OceanBase 各自通过公开操作验证：

- 同 Scope 两个 rag 和另一 Scope 的 rag；同文件导入归属独占，查询不串库。
- 固定 oceanbase/oceanbase commit 的 Markdown 和真实 OCR fixture，保留图片引用及准确出处。
- 相同成功跳过、相同失败重试、字节变化生成版本、配置变化重处理、仅 embedding 变化不 OCR。
- 迟到 Worker／冲突编辑拒绝，持久当前目标支持重启；内容或索引失败不暴露半发布版本。
- 通过新 revision 移除 chunk，包括最后一块；旧快照完整，当前索引不命中已移除块。
- Source／document／rag 软删除，重启后继续传播，历史／编辑 Source 读取校验，删除旧 Source 不误删替代版本。
- 插件由兼容主路径配置初始化但不持续耦合，索引隔离，重建期间明确报告不可用。
- 核心 schema 兼容、过滤混搜、分页和契约生成；mock 不代替真实后端／OCR 验收。

沿用 #2441 负责归属／导入，#2442 负责处理／内容／索引适配，#2443 负责检索／接口，#2444 负责诊断和三后端验收。
一个实现分支／PR 可按这四个阶段评审。本 RFC PR 不关闭实施 Issue，不新增 Issue，也不从功能 fixture 推断大规模容量。

# 缺点

完整 chunk 快照增加存储与写入，软删除原文／历史保留需要容量监控。共享 document-family 重建可能影响同 Scope 多个 rag 的检索。
逐文件最新状态比任务中心简单，但不提供完整批次／命令历史。即使核心 schema 不变，插件专用读取／投影／接受适配仍有实际开发量。
OCR 分发和平台支持需测试。

# 设计理由与替代方案

- 一个知识库一个 Scope 会将集合组织绑定到授权；多个 rag Artifact 保留项目分组。
- 跨 rag 共享逻辑 Source 会让删库影响其他库，仅在需要时共享物理快照字节。
- 在 Source 存 chunks 破坏不可变证据，每 chunk 一个 Artifact 则难以表达整篇文档版本。
- 全文 JSON 数组简化序列化，但会放大检索／明细读取。
- 独立 rag runtime、发布代次和通用任务表可支撑更复杂能力，但内容／索引原子提交的首期无需这些结构。
- 直接 SQL 删除 chunk 破坏旧 revision 数量、摘要和引用，用 revision 替换保留证据。
- 运行时持续回退主路径 embedding 配置可能静默改变向量空间，插件持有解析后的配置可避免此问题。

# 既有设计

基于 [Source 插件](2012-source-plugin-system.md)、[Artifact 插件](2175-artifact-plugin-system.md)、
[Source 身份](1400_source_definition_and_observation_model.md)、[处理监督](1515_artifact_processing_supervisor.md)
和 [Source／Artifact API](1437_source_artifact_rest_api.md)。现有 OpenDAL 已比较路径／内容 hash，Artifact head 治理和通用片段索引
提供复用基础，但并不表示知识库已经实现。Atomic Memory retirement 可参考，但不等于已有 Source 到 document 的级联契约。

# 待解决问题

实现需要固定能力签名、支持图片格式／模型版本、资源限制、后端迁移／事务行为，以及依据实际 embedding token 限制验证的默认参数。
验证三表基线，不预先扩大。任何核心 schema 改动必须有真实缺口和评审依据。

永久擦除、恢复、保留／GC、改名身份、完整批次历史、逐 rag 独立 embedding／重建和完整编辑 Markdown 导出需要另定契约。
不能直接拼接重叠 chunks 声称得到准确的编辑全文。

# 后续可能性

更多 OpenDAL provider、解析器、切块策略、rerank 和受限 agentic 检索可扩展组件边界。
测量到实际存储压力后可考虑共享不可变 chunk 正文，这些都不是首期归属、去重、原子提交或精确证据的前置条件。
