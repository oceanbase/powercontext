---
title: "RFC 2570：基于 rag Artifact 的文档知识库"
description: "复用 Source journal、后台 Worker 与 Source 生命周期元数据，实现 OpenDAL 接入、文档 chunk 版本和知识库检索。"
---

- Proposal Name: `rag_knowledge_base`
- Start Date: 2026-11-10
- RFC PR: [#2570](https://github.com/oceanbase/powercontext/pull/2570)
- Tracking Issue: [#2440](https://github.com/oceanbase/powercontext/issues/2440)
- Related Issues: [#2441](https://github.com/oceanbase/powercontext/issues/2441), [#2442](https://github.com/oceanbase/powercontext/issues/2442), [#2443](https://github.com/oceanbase/powercontext/issues/2443), [#2444](https://github.com/oceanbase/powercontext/issues/2444)

# 概述

通过 Source 插件和 document、rag 两个 Artifact family 提供文档知识库。一个 Scope 可有多个用户创建的 rag，
每个文档 Source 和整篇 document Artifact 唯一归属一个 rag。OpenDAL 采集不可变原文，Markdown 与图片 OCR 共用简单切块。
异步操作请求保存为不可变 Source，通过现有 journal、cursor 和 Worker 消费；document revision、chunks、lineage、索引与消费游标原子提交。

新增一张文档 chunk 表，并给 pc_sources 增加通用软删除字段。复用 Artifact 存储和后台处理表，不另建导入／任务／状态平台。
展示内容是否可用，不提供持久逐文件执行状态。本文是建议契约，不表示功能已发布。

# 动机

业务描述和 SOP 需要整篇文档的片段检索、精确出处、显式更新和可预测的删除。记忆提取无法保留每个片段。
首期应依托现有 Source／Artifact 基础设施覆盖该流程，同时明确失败语义。

原文快照、已接受的操作请求和成功 Artifact revision 各有职责，不能把可变任务状态写入 Source payload。
检索从已提交 chunks 派生，与 Memory 索引及配置隔离。复用仍需实际适配 family、读取、投影和 Worker。

# 使用层说明

1. 在现有 Scope 中通过通用 Artifact 创建空 rag。
2. 使用配置好的 OpenDAL Source 插件，选择 rag 与本地目录／文件。
3. 查看“已生成／有更新待生成／尚未生成”；没有结果不代表已经失败。
4. 检索明确选择且已授权的 rag，更新失败时继续使用原提交内容。
5. 导入变更文件或显式重新解析，从固定原文整篇生成新 revision。
6. 异步编辑／移除 chunk；先接受请求，成功时提交新 revision，保留历史。

用户无需提供文件 hash、namespace 或 document ID。Scope 保持授权边界，同文件导入两个 rag 产生不同逻辑 Source／document，
可选复用物理快照字节。首期支持 Markdown 和独立导入的文字图片，OCR 使用 RapidOCR + ONNX Runtime。
Markdown 内图片只保留引用，不抓取或 OCR。SQLite、SeekDB、OceanBase 均支持完整流程，一个部署选择一种后端。

首期不包括 PDF／Office、独立 TXT 导入、语义／LLM 切块、复杂布局恢复、定时目录同步、逐 rag embedding 空间、
无停机索引切换、任务管理 UI、逐文件持久失败详情、恢复／永久擦除或大规模性能验收。

# 参考层说明

## 1. 归属与插件配置

```text
Scope
  rag R1
    原文 Source S1 -> 操作 Source Q1（import）-> document D@1 -> chunks
    操作 Source Q2（edit D@1）-> document D@2 + 成功编辑证据 E2
    原文 Source S2 -> 操作 Source Q3（reparse）-> document D@3
  rag R2
    独立的原文 Source、请求和 document

Source 插件：OpenDAL 采集、快照访问、原文与受信操作 Source 类型
Document 插件：Markdown／OCR、chunks、revision 写入、操作消费和溯源
rag 插件：集合配置、检索策略和 chunk 索引适配
共享设施：Source／Artifact API、Supervisor／Worker、cursor、事务、数据库／模型适配器
```

三个逻辑插件可放在同一集成包，应用服务负责协调，不新增第四个插件。Rag 可先于 Source 存在，配置使用 Artifact revision，
删除使用 head 治理，不设 rag 运行状态表。归属显式保存在不可变 Source payload 和 document manifest，不从 tags 或证据 lineage 推断。
每个原文、请求和 document 只属于一个 rag。

插件独立持有 embedding／索引配置。显式配置优先；安装时可从主路径初始化兼容的缺省项，并固定解析后的 profile 和凭据引用。
不把密钥写入 Artifact，也不自动跟随之后的主路径配置变化。Parser／OCR／chunker 可按 rag 配置；首期 embedding／索引 profile
为插件级，在同一 Scope 的 document family 共用。按 family、索引定义和向量空间隔离，模型／维度变化走现有 family 重建机制。

文档原文只允许 document 管线直接消费，操作请求只允许其处理器执行。在选择、执行和提交环节校验资格；仅注册新 Source 类型
并不能阻止已有通用消费者使用它。请求不作为普通生成输入或检索文本。

## 2. OpenDAL 导入、身份与去重

OpenDAL／opendalfs 负责本地列举／读取和受管目录中的不可变快照。插件实例必须能访问选定路径。固定根目录、相对路径和
符号链接规范化策略，拒绝越界。先保存快照，再接受 Source，对保存字节计算 hash 并解析该快照，不能解析随后变化的文件。
mtime／size 仅是提示，不证明内容相同。复用已有 Source 时保留首次 canonical 采集信息。

```text
file_key       = (scope_id, rag_id, collection_id, normalized_relative_path)
document_id    = stable_hash(identity_version, file_key)
content_digest = SHA256(captured raw bytes)
source_key     = stable_hash(source_schema_version, file_key, content_digest)
processing_key = hash(parser/OCR/model/normalizer/chunker versions and settings)
index_key      = hash(complete embedding inputs, plugin profile and projection versions)
```

Collection 由已注册存储实例／绑定和规范化根目录派生，不使用 scan token。Hash 命中还需核对完整身份，不截断路径或仅按摘要合并。
原文 payload 包含 owner rag、文件身份、原始路径、document ID、media type、摘要和快照引用。Artifact 尚未存在时即可得到相同的
确定性 document ID，无需预留表行。

| 情况 | 行为 |
| --- | --- |
| 同文件／内容／处理 profile，已有可用成功结果 | 复用 Source／document，跳过解析，单独检查索引就绪情况 |
| 存在尚未消费的等价请求 | 扫描未消费 journal 时可合并；并发重复 import 在执行时再次检查成功结果 |
| 同输入但无成功结果 | 显式重试可创建新请求；采集 checkpoint 不等于处理成功 |
| 同路径字节变化 | 新原文 Source，相同 document ID，成功后整篇新 revision |
| Parser／OCR／chunker 变化 | 复用原文 Source，显式请求重处理 |
| 仅 embedding／索引变化 | 从已提交 chunks 重建，不重复 OCR |
| 同内容不同路径／rag | 不同逻辑 Source／document，可选复用物理快照 |
| 人工编辑／移除后原文未变 | 保留人工版本，不把人工正文 hash 当原文 hash 比较 |
| 重新解析人工修改过的文档 | 必须明确 manual_edits_policy=discard，否则保留当前内容并拒绝冲突 |

文件回到旧字节可能复用较早的 Source，因此期望输入依据最新接受的 import／reprocess 请求，而不是最大原文 Source journal position。
找到 Source 不证明当前 document 已匹配。首期改名视为新身份，目录文件消失不自动删除。已删除 Source 身份与 retired document
不能被静默恢复。

复用 Source collect 的发现／add／status／control。客户端生成的 scan_token 放在插件 input 中标识一次显式扫描；重试相同输入
复用采集目标。再次扫描使用新 token，但文件身份不变。每 token 扫描一次，不暗示定时同步或重复 add 会启动 stopped target。
采集状态仅描述接入；扫描还需检查成功 document 结果，不能只看 checkpoint。

## 3. Markdown 规范化与切块

```text
Markdown 字节 -> markdown-it-py 结构／字节映射 -----+
                                                  +-> 规则切块
图片 -> RapidOCR 文字／区域 -> 规范化 Markdown -----+
```

OCR 保留实际文字、顺序、置信度和原图区域，包括预处理坐标变换。规范化只转义 Markdown 语法、组织行／段落，不虚构标题、
不做 LLM 校正或复杂表格恢复。规范化文本跨度映射回 OCR 行和原图区域，并声明 locator 精度。派生 Markdown 不替换图片 Source。

按标题、段落、列表和代码块组织，短块合并至 max_chunk_chars，过长块按句子／长度拆分，可用 overlap_chars 保留上下文。
保留标题路径、语言和续块标记。Overlap 必须小于最大长度，1200／100 仅为待验证示例；调用 embedding 前校验包含标题／前缀的
完整输入 token 预算，不能静默截断。

明确校验格式、UTF-8、图片解码／像素上限、OCR 模型、超时和无文字结果，固定并验证真实 RapidOCR、ONNX 和模型版本。
OCR 无文字不算成功空文档；显式移除最后一个 chunk 则允许。无需独立章节树、解析节点表或完整 Markdown 缓存。
Chunk metadata 保存有界出处映射，canonical chunks 不存向量，Artifact manifest 不存全文。

## 4. 持久化：一张明细表与 Source 生命周期字段

不新增 pc_document_imports、pc_document_source_states。Source payload 保存不可变身份／请求，Artifact 保存成功 revision，
现有处理表协调消费。不能用 rag content、Scope settings、checkpoint 或 cursor 中的无界字典替代这些表。

| 复用设施 | 职责及结构变化 |
| --- | --- |
| pc_sources | 原文、操作请求、成功编辑证据；增加下述通用生命周期字段 |
| Source journal／heads、connector checkpoints | 接受顺序和采集进度，结构不变 |
| pc_source_cursors | document binding 的连续扫描位置和 CAS generation，结构不变 |
| Processing intents、leases、binding state | Scope／binding 唤醒、调用代次和执行栅栏；结构不变，不是逐文件任务表 |
| pc_artifacts、pc_artifact_heads | rag 配置、document manifest、当前 revision 和 retirement／治理，结构不变 |
| Source／Artifact lineage、locators、tags | 原始输入、请求、base revision 和成功编辑证据，沿用结构 |
| Artifact search states／coverage／changes／text／vectors | 当前版本片段索引和构建协调，复用设施并增加 chunk 投影适配 |

给 pc_sources 增加：

| 字段 | 类型／默认值 | 语义 |
| --- | --- | --- |
| lifecycle_state | 短字符串、NOT NULL、默认 active | active／deleted，首期单向转换 |
| deleted_at | 可空 UTC 时间 | 首次删除时间，重复删除不刷新 |

已有 Source 回填 active，payload、身份和 journal position 保持不可变，生命周期元数据不参与内容 hash。
通过行锁／条件更新和统一锁定顺序与 document 提交互斥，首期不额外增加 Source 删除代次列。
适配 add/get/list、证据／生成访问和 family 读取；重复 add 命中已删身份时不能返回可用内容或恢复。
内部迁移／级联扫描可包含已删行，公共读取不能绕过。三个后端验证加法迁移，不能把 Artifact retirement 当作已有 Source 软删除能力。

唯一新增领域表为 pc_document_chunks：

| 约束／字段 | 设计 |
| --- | --- |
| 主键 | (scope_id, document_id, revision, chunk_id) |
| 唯一顺序 | (scope_id, document_id, revision, ordinal) |
| 内容 | text、content_digest |
| 结构 | 标题路径、续块／重叠元数据 |
| 溯源 | 规范化跨度、原文字节／图片定位、origin/verbatim、base/edit 引用 |

使用后端安全键长与有界元数据。每个 revision 是完整不可变 chunk 快照：100 块移除一块生成新 manifest 和 99 行，保留旧的 100 行。
Manifest 保存 owner rag、文件身份、root SourceRef、schema／处理指纹、chunk 数量／摘要、人工修改标记和 applied_request_ref。
历史读取使用精确 revision，而非当前 head。

首期成员和可用性查询可以按 Scope／类型分页扫描解码 payload，再按确定性 document ID 精确读 Artifact。不能假设 canonical payload
已具备可查询的 JSON owner/path 索引。过滤分页按扫描位置继续，不能丢失后续匹配项；写入／读取验证归属和完整身份。
扫描有成本，本方案不承诺大规模性能。额外投影需有测量依据，不能隐式重新引入任务表。

## 5. 操作 Source、Worker 与事务边界

受信应用服务创建专用操作 Source 类型，示意名 document_operation，原文示意名 document_file；它们是插件注册，不是新存储表。
请求保存 owner rag、document ID、operation、root SourceRef、固定处理配置／指纹、request ID、base revision，以及有界编辑／discard 参数。
首次导入可无 base，编辑必须固定已有 base。Payload 不包含可变 state、attempt、error 或未来输出 revision，普通用户 metadata 不能伪造命令。

Import／reprocess 从固定原文整篇生成；edit 支持 replace_text、remove。现有 ContentSourceInternal 表示成功 artifact_create／artifact_replace，
且绑定精确 revision，不能表示未执行命令。保留其成功语义，待执行请求使用专用类型。

原文接受／复用、请求插入和 document binding 的 processing_intents.request() 同事务完成。只有新接受的请求发布新工作。
需显式适配 Source／binding 通知，当前 SourceRepository 不会自动发现本方案的 processor。

请求身份在 Scope／rag／document／operation 和客户端 request_id 范围确定性生成。Canonical payload 相同的重放返回已接受请求，
相同身份但不同内容冲突。重放已消费请求不再次执行；显式重试使用新 request ID，可复用同一原文 Source。
只增加调用计数不能重新执行已被 cursor 越过的输入。

```text
请求 Source + processing intent 原子接受
                 |
现有 Worker 顺序扫描 document binding 的 journal 窗口
                 |
事务外准备 chunks／embedding
                 |
document + chunks + lineage + 索引 + cursor 原子提交
```

同 Scope／document binding 内顺序处理，采用有界窗口和既有栅栏／cursor CAS。不相关 Source 只推进连续扫描边界。
不提供逐文件队列或“最新请求自动取消旧请求”的保证。请求固定 base；前序操作改变 head 时，后续请求可能冲突，不能静默换 base。
用户刷新后提交新请求。执行期间接受的新 Source 不修改运行中的请求。Scope invocation 依照既有 ScopeInvocation 契约确认，
处理部分工作不能把整个窗口标成 clean。

Cursor 表示已消费，包含跳过，不表示成功。成功 revision 通过 applied_request_ref 和 lineage 关联请求；没有持久逐请求失败回执。

| 结果 | Cursor 与恢复 |
| --- | --- |
| 单输入解析／OCR／配置错误或 base 冲突 | 记录脱敏标识／错误，在执行栅栏保护下越过该请求，保留旧内容，继续其他输入 |
| 请求局部短暂错误 | 可在进程内有限重试，耗尽后同样记录并跳过，不持久保存 attempt |
| 数据库故障、索引重建或系统性模型不可用 | 不推进该请求，复用 Supervisor 退避／恢复，报告 binding 级不可用 |
| 提交前进程退出 | Cursor 未推进，恢复后重执行固定请求 |
| 成功提交后进程退出 | 内容与 cursor 已提交，不重复创建 revision |
| 输出失败日志后、跳过事务前退出 | 可能重新执行，允许重复诊断 |

日志遵循现有可观测性契约，不包含正文、凭据或敏感路径，也不是恢复依赖。已消费失败请求需显式新请求重试。
运行时任务状态或请求超时不能证明永久失败。

| 边界 | 工作 |
| --- | --- |
| 事务外 | 快照采集、解析／OCR、完整 chunk 快照、embedding 和索引投影准备 |
| 接受事务 | 权限、归属／生命周期／base 校验，Source／请求接受，processing intent |
| 成功内容事务 | 重验 Worker／cursor／base／owner／root／索引栅栏；提交 manifest、chunks、lineage、必要成功编辑证据、head、索引及 cursor |
| 失败跳过事务 | 重验执行／cursor 栅栏并推进连续边界，不写 document 或成功编辑证据 |
| 删除事务 | 标记 Source 生命周期或 retire Artifact head，由父／root 校验阻止并发发布 |

模型调用和 DDL 在内容事务外。Chunk 投影从 chunks 准备，不从 manifest 中假设存在全文；提交验证快照和投影指纹。
新内容与索引一起可见，更新失败保留旧 head／索引。Family 重建时延后 document 提交，不走通用“仅提交内容”路径。
重建读取已提交 chunks，不重复 OCR；明确不可用，不回退 Memory／纯全文，也不保证零停机。
三个后端需验证实际事务，包括 SQLite 写锁；ANN 过滤限制需通过限定集合精确检索或明确能力错误处理。

## 6. 编辑、chunk 移除与删除

编辑 If-Match 在接受和提交时分别校验，成功生成完整新快照。未变 chunk 保留逻辑 ID，重切块分配新 ID；引用始终带 revision。
移除最后一块后保留可读零块 manifest，当前索引没有片段；任何移除都不物理删除历史 chunk 行。

成功时创建系统 Content Source，role=lineage_only、operation=artifact_replace，绑定真实新 revision，记录请求／base／root 修改依据。
显式保留原始 Source 依赖。编辑文字标记 origin=manual_edit、verbatim=false，原图坐标只表示修改依据，不证明新文字逐字存在。
Remove 同样算人工修改，重新解析需明确丢弃授权。完整 embedding 输入未变可复用计算，但索引映射仍需指向新当前 revision。

| 删除操作 | 行为 |
| --- | --- |
| 原文 Source | Source lifecycle 标记 deleted，retire 当前仍以它为 root 的 document，并撤销相关历史读取；其他 root 完整重解析的版本保留 |
| Document | Retire head，阻断所有 revision／chunks 和后续命令，原文 Source 保持 active |
| rag | Retire head，立即阻断所属内容，再可恢复地标记全部所属 Source（含请求／编辑证据）和 document head |
| Chunk | 提交不包含该块的新 revision，保留历史 |

复用 Artifact lifecycle_state=retired 和治理 CAS。原文 payload／快照及历史业务内容保留，不动用户文件，首期无恢复／永久擦除。
尚无 head 的导入文件通过原文 Source 删除；document DELETE 返回不存在，不虚构占位 Artifact。
删除待处理替代 Source 不 retire 仍依赖另一 root 的旧提交版本。

检索和通用精确／历史／chunk 读取检查 owner rag、document head 和精确 revision 的 root。历史 base 链接不让完整重解析版本传递依赖旧 root。
原文读取校验自身状态／owner，单删 document 不隐藏原文；请求／编辑证据读取还需校验目标 document，避免保留的人工正文绕过删除。
删除请求会阻止尚未消费的执行，不撤销已提交编辑；内容有效性由 root／document／rag 决定，而非事后取消请求。

通用 retirement 可能仍允许精确读取，需增加 family guard。大规模 rag 级联从 retired 父对象和不可变归属重新发现剩余工作，
内部包含已删行，无需 cascade job 表。即使传播未完成，也必须在排名前排除无效对象。
派生索引沿用替换／移除／重建；保留业务历史不等于保留每个历史索引条目。

## 7. 可用性与统一接口

| 展示 | 推导依据 |
| --- | --- |
| 已生成 | 有可用提交 revision，匹配选定原文／配置，保留有效人工修改 |
| 有更新待生成 | 有可用旧 revision，但期望原文／配置不同；旧内容仍可检索 |
| 尚未生成 | Source 已接受但无可用成功 revision，可能未开始、运行中或失败 |
| 已删除 | Source 或所属 Artifact 生命周期无效，不返回保留正文，普通列表默认排除 |

期望输入取最新接受的 import／reprocess 请求，无请求时取已接受原文。人工编辑不是原文件不匹配。
Rag 配置变化影响后续请求，可提示需要重处理，但不自动入队。不展示逐文件阶段、百分比、attempt 或持久错误，
不从采集完成／cursor 推进推断成功；binding 诊断和索引可用性单独处理。

复用权限、错误、引用、分页、ETag 与 SDK／MCP 操作。必要契约改动写入 openapi/powercontext.yaml 并生成绑定。
当前通用 create-Source 只接受 built-in content，文档文件使用 Source 插件协议。
约定 `R=/v1/scopes/{s}/artifacts/rag/{r}`，`D=/v1/scopes/{s}/artifacts/document/{d}`。

| 操作 | 现有路由或必要扩展 |
| --- | --- |
| 创建／配置 rag | 现有 POST `/v1/scopes/{s}/artifacts`、GET/PUT R、revisions/tags，完整替换与既有前置条件 |
| 导入／控制 | 现有 POST `/v1/source-management/add`、collect capability／instance 和 status／target／instance controls |
| 文件与可用结果列表 | 现有 Source list/get、Artifact list/get/history，验证 owner-rag filter，helper 按 document ID 合并；不设 R/processing 任务列表 |
| 显式重解析 | POST D/processing，source_ref、action=reprocess、request_id、可选配置／discard；202 表示请求接受 |
| 编辑／移除 chunk | POST D/edits，当前 If-Match、有界类型化操作和 request ID；202 表示请求接受 |
| 读取 chunks | GET `D/revisions/{rev}/chunks`，精确版本、items/next_cursor |
| 检索 | 现有 POST `/v1/scopes/{s}/artifacts/document/search`，query/mode/limit/fusion，经过验证的 filters.rag_ids |
| 软删除 | 在既有精确 Source／Artifact 路由增加 DELETE；Artifact 治理 expected_generation，Source 单向生命周期 |

已有 head 的重解析需 If-Match，无 head 时使用确定性 document ID 和 If-None-Match: *。验证 Source 归属／文件身份确实解析到该 ID，
执行时重验 base。不把 Scope invocation generation 作为 document target generation 返回。
Document 操作复用 scope.contribute，rag／Source 删除复用 scope.admin，读取沿用既有授权。

创建 rag：

```json
{"family":"rag","content":{"title":"Operations SOPs","parser":"markdown-v1","ocr":"rapidocr-local-v1","chunker":{"max_chunk_chars":1200,"overlap_chars":100}}}
```

目录采集，capability／instance 为部署发现值示意：

```json
{"scope_id":"project-p","capability":"knowledge-documents.collect","capability_version":"1","instance_id":"local-documents","input":{"rag":{"family":"rag","artifact_id":"ops","revision":1},"root":"/work/docs","scan_token":"scan-01"}}
```

POST D/edits 移除 chunk，携带当前 document ETag：

```json
{"request_id":"edit-03","operation":{"kind":"remove","chunk_id":"chunk-3"},"reason":"Remove an obsolete instruction"}
```

202 回执说明持久接受，不表示 pending 状态或未来 revision：

```json
{"accepted":true,"document_id":"doc-1","request_source":{"source_type":"document_operation","source_id":"request-03"},"current_revision_at_accept":2}
```

current_revision_at_accept 是观察值，不是输出。通过 revision 的 applied_request_ref 确认成功，没有匹配 revision 不证明失败。
重新执行需刷新前置条件并使用新 request ID，相同请求重放返回原接受事实，不是新的执行结果。

显式重新解析，已有 document 使用 If-Match，并授权丢弃人工修改：

```json
{"action":"reprocess","request_id":"reparse-04","source_ref":{"source_type":"document_file","source_id":"source-1"},"manual_edits_policy":"discard"}
```

同 Scope 检索选定 rag：

```json
{"query":"How should I restart a follower?","mode":"hybrid","limit":10,"filters":{"rag_ids":["ops"]},"include_scores":true}
```

搜索结果保留完整有界 document manifest 和 lineage，增加可选类型化 matches 表示有界精确 chunk 命中，携带 revision／chunk ID、
text、locator、origin/verbatim 和得分。这是响应投影，不写入 Artifact content。Limit 计算文档数，每篇片段另有限额。
不支持的 filter／rerank 明确拒绝，缺少 rag 选择不能扩大范围。跨 Scope helper 逐个授权明确的 (scope, rag)，调用相同分 Scope 搜索，
按排名融合，保留目标错误／身份；不比较不兼容原始分数，不另建 /rag/query 协议。

接受阶段沿用 428／412 前置条件、409 payload／discard 冲突、授权后 410 删除、422 校验（传输层可用 413）和 503 服务不可用，
保持 error.code/message/details 错误封装。202 之后的请求局部失败／冲突按日志与消费规则处理，不提供持久错误状态端点。

## 8. 实施与验收

复用仍需 Source 生命周期迁移／读取 guard、受信请求接受、消费者限制、document binding 通知、journal 选择／cursor 事务、
chunk 投影和 top-k 前归属过滤。不代表框架无需适配，也不代表当前路由已经全部支持。

SQLite、SeekDB、OceanBase 使用相同公开流程验证：

- 多 rag／Scope、文件归属独占、授权选择与不串库。
- 固定 oceanbase/oceanbase commit 的 Markdown 和真实 OCR fixture，保留准确出处。
- Source 迁移回填、payload 不变、已删重放拒绝、精确／历史／证据读取校验。
- 成功去重、失败后新请求重试、配置重解析、仅 embedding 变化重建。
- 请求／intent 原子接受，重启恢复，内容／cursor 原子提交，相同请求不重执行。
- 单输入失败推进与系统性故障保留，cursor 推进绝不表示文件成功。
- 异步 base 冲突／迟到 Worker、失败不生成成功编辑证据、人工修改保护。
- Chunk 移除包括最后一块，历史完整，当前索引正确。
- Source／document／rag 删除竞争，可恢复级联，替代 root 独立性。
- 插件配置独立、索引隔离、重建不可用、分页、契约生成和 SDK／MCP 一致性。

Mock 不证明真实后端／OCR 支持。#2441 负责接入／归属／Source 生命周期，#2442 负责处理、请求／chunks／原子写入，
#2443 负责检索／接口，#2444 负责诊断／验收。一个实现分支／PR 可分阶段交付；RFC 不关闭实施 Issue，也不证明大规模容量。

# 缺点

操作 Source 增加 journal 行，完整 chunk 快照和软删除保留消耗存储。顺序消费可能等待前序文件；可用性展示没有逐请求持久失败详情，
首期 owner／请求过滤可能扫描 payload。Family 重建可中断多个 rag。这些是首期明确取舍，Source 生命周期、消费者／投影适配仍有实际工作量。

# 设计理由与替代方案

- 多 rag Artifact 保留项目级 Scope，不把集合组织与授权绑定。
- Source journal 持久保存已接受操作，无需新增可变任务表；仅内存命令在重启时会丢失。
- 请求 Source 保存意图，精确成功编辑 Source 保存结果；提前使用后者会错误表达溯源。
- Source 生命周期字段表达通用删除，payload 仍不可变；文档专用 sidecar 重复承担该职责。
- 整篇 manifest 配合 chunk 明细避免巨大正文 JSON 和逐 chunk Artifact 身份。
- 不可变 revision 替换保留旧数量／摘要／引用，直接删 chunk 做不到。
- 独立任务／发布系统可提供更丰富的状态／切换能力，但不在接受范围内。

# 既有设计

基于 [Source 插件](2012-source-plugin-system.md)、[Artifact 插件](2175-artifact-plugin-system.md)、
[Source 身份](1400_source_definition_and_observation_model.md)、[处理监督](1515_artifact_processing_supervisor.md)
和 [Source／Artifact API](1437_source_artifact_rest_api.md)。现有 OpenDAL hash、Source cursor CAS、Artifact head 治理和片段索引
提供基础，并非完整知识库实现。Atomic Memory retirement 也不等于已存在 Source 到 document 的级联契约。

# 待解决问题

实现时固定 Source／capability 签名、图片／模型版本、资源与切块默认值、有限重试错误分类、后端迁移／索引事务行为。
新增查询投影必须有测量依据。逐请求持久结果、恢复／擦除／GC、改名身份、逐 rag 向量空间和完整编辑 Markdown 导出另定契约，
不能直接拼接重叠 chunks 重构完整编辑文档。

# 后续可能性

通过组件边界扩展 OpenDAL provider、解析器、切块器、rerank 与受限 agentic 检索。测量后再考虑共享不可变 chunk 正文、
更丰富任务状态或面向集合规模的查询投影，这些都不是首期归属、请求恢复、版本内容和精确证据的前置条件。
