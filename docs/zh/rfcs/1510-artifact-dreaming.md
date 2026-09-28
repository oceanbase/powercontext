- Proposal Name: `artifact_dreaming`
- Start Date: 2026-09-07
- RFC PR: [#1510](https://github.com/oceanbase/powercontext/pull/1510)
- Tracking Issue: [#1509](https://github.com/oceanbase/powercontext/issues/1509)
- Related RFCs: [Candidate 与 Review Inbox](0050_artifact_candidate_review_inbox.md)、
  [Experience 与 Skill](0051_experience_skill_artifact_families.md)、
  [标准 Skill 生命周期](1351_standard_skill_package_lifecycle.md)、
  [访问控制](1396_handoff_access_control.md)、
  [Source 与 Artifact 基础 API](1437_source_artifact_rest_api.md)、
  [Topic Memory 与后台处理](1417_topic_memory.md)、
  [Artifact Processing Supervisor](1515_artifact_processing_supervisor.md)

# Summary

本 RFC 定义 Artifact Dreaming：在后台回看一组已有制品及其证据，对照不同任务中的结果，提炼可复用判断、
补充适用边界或提出能力升级。Dreaming 产生需要审核的 Candidate，批准后才形成正式 Artifact Revision。

首版提供两种操作：

| operation | 输入 | 输出 |
| --- | --- | --- |
| `refine_experience` | 精确 Memory 条目版本、正式 Experience Revision，或二者的组合；可补充精确 Source | 一个新的 Experience Candidate，或针对一个 Experience 的 replacement Candidate |
| `derive_skill` | 一组正式 Experience Revision，可补充精确 Source | 一个新的 managed Skill Candidate，使用现有 `experience` generation origin |

一次运行处理一个用户选定的问题，最多生成一个 Candidate；没有值得保存的变化是合法结果。运行记录 DreamRun
保存精确输入、策略版本、处理结果和成本。首版由用户或 integration 显式触发，执行在后台完成，并复用现有
Review Inbox。周期性发现、Memory 条目修改、Skill 使用反馈驱动修订和多制品合并治理列入后续范围。

本 RFC 定义运行与审核契约。实现复用正式制品、Candidate 和 Family 生命周期，不将 DreamRun 定义成新的 Artifact
Family，不更改基础 Artifact 管理写入的授权语义。

# Motivation

已有制品分别记录了有价值的信息，但跨任务的规律和反例往往散落在不同 Revision 中。用户还需要手动收集相关
Memory 条目和 Experience、辨别是否指向同一问题、找出旧结论的适用边界，再调用 generation 提出修改。

Memory 的正文保存在独立 Entry Version 中，顶层 Artifact 仅保存 manifest 和变更记录。现有通用 generation
对 Artifact 的 JSON 投影没有展开这些条目；Dreaming 提供精确条目解析，使记忆中的任务记录能够沉淀为经验。

例如，三次任务分别证明了“读取重试有帮助”“超时后的写入重试产生了重复记录”和“幂等键可以防止重放重复”。
逐条保留它们有用；对照三者还可以修正“所有失败都重试”的过宽经验，并形成检查重试策略的 Skill。

现有 Source-window Experience incubation 从新增 Task Outcome 中提炼候选。Dreaming 的处理单位是用户明确
选择的既有 Memory 条目版本、Experience Revision 及相关证据，任务可以横跨多个 session 和 Source window。
它需要自己的运行结果，不能复用或推进 Memory、Experience incubation、Topic Memory 的 Source Cursor。

预期收益是减少错误概括、降低后续任务中重复分析的成本，并形成可验证的工作方法。生成 Candidate 的数量本身
不代表质量或任务收益。

# Guide-level explanation

## 从 Memory 沉淀经验

用户在同一 Scope 中选中三条连接池排查记忆，每条都固定到所属 Memory Revision 和 Entry Version：

| 条目 | 内容 | 原始任务结果 |
| --- | --- | --- |
| `entry_pool_diagnosis@ev_2` | 排查发现异常路径未归还连接 | outcome_pool_a |
| `entry_pool_fix@ev_1` | 补齐归还逻辑后连接池恢复 | outcome_pool_a，与上一条属于同一次任务 |
| `entry_pool_counterexample@ev_3` | 另一次连接池耗尽由慢查询引起，调优查询后恢复 | outcome_pool_b |

用户选择“提炼 Experience”。后台读取三个条目的真实正文和来源，把前两条归入同一个根证据组，保留第三条
作为条件不同的反例，提出“连接池耗尽时区分连接泄漏与慢查询占用”的 Experience 候选。

Reviewer 可以从候选逐级打开“具体条目版本 → 原始任务结果”，看到三条 Memory 来自两次任务，而非三次独立
验证。批准后创建新的 Experience；原 Memory 条目保留。用户偏好、计划或缺少行动结果的描述不会自动变成经验。

## 一次提炼

用户在同一 Scope 内选中三个 Experience：

| 制品 | 其证据记录的结果 |
| --- | --- |
| `exp_read_retry@2` | 外部读取请求遇到暂时错误，重试后成功 |
| `exp_write_timeout@1` | 写请求超时后自动重试，产生重复记录 |
| `exp_idempotent_write@1` | 带稳定幂等键的写请求被重放，没有产生重复记录 |

用户选择“提炼 Experience”，并将 `exp_read_retry@2` 作为要修订的目标。系统返回 run_id，后台核对这三份制品
和可用原始证据，提出有适用条件的修订：

> 重试策略需要区分读取、幂等写入和结果不明的写入。对结果不明且没有去重保证的写入，先确认提交状态；
> 只有在相应接口提供幂等保证时，才按已验证的策略重放。

Reviewer 可以查看目标 Revision、完整 proposal、被使用的证据、变化理由和证据局限，再按精确 Candidate
version 批准或拒绝。批准后，同一 Artifact identity 产生下一 Revision。

如果目标已被其他写入者推进，批准返回冲突。系统保留 pending Candidate；Reviewer 读取新的 head 后重新提案
或提炼。既有 Candidate 的 target 不能跨 version 改变，Dreaming 不把旧 proposal 自动套用到新的 head 上。

## 从经验形成 Skill

新 Experience 获批后，用户可以在另一运行中选择 `derive_skill`，生成“重试策略检查”Skill 候选。候选包含
适用说明、操作步骤和验证要求，随后进入现有标准 Skill 校验、审核和显式发布流程。

本次 Dream 产生的 Candidate 不能作为本次或另一 Dream 的 Artifact 输入。Skill 获批不等于已经安装、加载、
运行或向 Receiver 分发。

## 运行完成与审核完成

~~~text
选择精确 Memory 条目版本或 Experience
  -> 创建 DreamRun
  -> 后台解析证据、生成、校验
  -> Candidate + DreamRun 结果原子提交
  -> Reviewer 检查 Candidate version
  -> 批准后产生 Artifact Revision
~~~

运行成功可以得到三种结果：

- `proposed`：已经保存一个 pending Candidate；
- `no_change`：已有内容足够，没有实质变化；
- `needs_evidence`：问题有价值，但现有材料不足以支持完整提案。

`succeeded` 仅描述运行，不表示候选已被批准。执行错误返回 `failed`，不能伪装成 `no_change`。

## 首版边界

首版解析同 Scope 的 Memory 条目、Experience 和普通生成证据 Source。正式 Experience 包括通过 Review 发布的内容，以及
通过基础管理 API 显式写入的内容；正式身份不使其每句话自动成为已验证事实。所有由 Dreaming 产生的内容都必须
先进入 Candidate。

Memory → Experience 复用 refine_experience；Memory 正文按 manifest 中的精确 Entry Version 展开。Memory
修改、Skill 包输入及 Handoff 输入不在首版范围。derive_skill 仍只直接接受 Experience；Memory 先经审核形成
Experience，再由另一运行派生 Skill。

# Reference-level explanation

## 领域对象与运行输入

DreamRun 的公开身份是 `(scope_id, run_id)`，不是 ArtifactRef。它既不进入 Artifact search，也不进入
PreparedContext 或下一轮的证据集合。模型不能分配 run_id、candidate_id、artifact_id 或目标 Revision。

创建请求使用以下字段；未知字段返回 422：

| 字段 | 约束 |
| --- | --- |
| `operation` | 必填，`refine_experience` 或 `derive_skill` |
| `artifacts` | 可省略，默认空数组；精确 Experience ArtifactRef，不接受整份 Memory Ref |
| `memory_citations` | 可省略，默认空数组；复用 `{memory_ref, entry_id, entry_version_id}`，仅 refine_experience 支持 |
| `sources` | 可省略，默认空数组；元素为 `{source_type, source_id}` |
| `target` | 可省略或 null；仅 refine_experience 支持，必须是 artifacts 中的精确 Experience Ref |
| `idempotency_key` | 必填，1–128 字符的非空、无首尾空白字符串 |

`scope_id` 只从 Path 获取。所有引用都在该 Scope 中解析；不接受 latest、跨 Scope 地址、Candidate 引用、
自定义提示词、模型名称或任意工具调用。规范化去重后，artifacts 与 memory_citations 合计 1–20 项；加上
sources 后合计不超过 32。derive_skill 要求 memory_citations 为空、artifacts 非空；不接受仅 Source 的 Dream。

每个 memory_ref 的 family 必须为 memory，revision 必须精确。引用中的 entry_id 和 entry_version_id 必须
同时属于该 Revision 的 manifest。条目引用属于 Memory 内部身份，不分配新的 Artifact identity。

`target` 在受理时必须是当前 head。其他输入可以是历史 Revision，模型与 Reviewer 必须看到精确版本和历史
性质，不能将其表述为当前状态。derive_skill 的 target 固定为 null；首版不自动寻找或替换已有 Skill。

## 证据解析、快照与来源准入

受理时检查直接引用、条目锚点、Family、Source 用途和目标基准。后台首次执行时，解析所选条目与制品、来源链和显式
补充 Source，持久化不可变 input manifest，再调用模型。后续重试只使用这份 manifest 指定的版本和投影。

manifest 至少包含：

- 请求选择的精确 MemoryCitation、ArtifactRef、SourceRef 和可选 target；
- 每个实际读取的内容引用、内容摘要及其 operation-local evidence ID；
- 实际输入模型的证据投影摘要、完整性说明和来源角色；
- `transform_version`、提示词版本、模型配置标识及有效预算。

Memory 项还记录 entry_content_hash、引用时的条目状态、解析时观察到的当前版本，以及到根 Source 的引用路径
和去重分组；这些均由 Runtime 构建。模型只选择已分配的 evidence ID，不能补写条目身份或来源关系。

原始正文仍由 MemoryEntry、Source 和 Artifact 存储持有；manifest 不创建新的 Source，不复制一套长期记忆。恢复时重新读取相同
引用，并核对摘要和可用性；无法恢复相同输入时失败，不切换到 latest。来源遍历必须去重、检测循环并设上限；
模型可见的完整证据集合最多 32 项，总投影不超过预算。一次运行最多遍历 128 个不同节点、256 条引用边和
8 层来源关系；超过任一界限即 evidence_limit_exceeded。首版不静默截断后声称完整溯源。

### Memory 条目解析与准入

解析器复用 Memory 的 exact citation 校验，并补充 Dream 生成准入：

1. 按 memory_ref 读取权威 Revision，校验 manifest 中条目 ID、版本 ID 和 hash，不从搜索摘要或当前投影补正文。
2. 读取完整 MemoryEntryVersion，复核所属 Memory、正文及来源的内容 hash；复用 validate_citation/expand 的
   身份和 hash 校验，而非仅序列化顶层 Memory。
3. 引用 Revision 中该条目必须 active，当前权威 manifest 中该逻辑条目也必须存在且 active。历史版本可以
   使用，但必须标记为历史；当前版本前进不替换选中的正文。搜索命中或缓存不代替状态检查。
4. 投影仅包含选中条目的 kind、text、版本和来源角色，继续沿该 Entry Version 的 sources/artifacts 解析。
   不展开同一 Memory 内未选中的其他条目，不把整份 Memory 的 lineage 当成每条记忆的证据。
5. 上游 Memory 关系只有精确条目引用时才继续解析；旧数据仅有整份 Memory Ref 时记录为未解析的关系，不遍历
   其全部条目，也不计作根证据。Experience 的精确 lineage 可以继续展开，缺少解析器的 Family 同样标明局限。

逐项检查条目、上游制品和 Source 的读取权限。已声明的精确来源不存在、hash 不匹配或权限撤回会失败，不当成
无变化。没有记录原始来源、仅有 lineage_only 或未解析关系时保留证据缺口；Memory 参与的 Experience 提案必须
具有可用的合规根 Source，并能支撑相关行动和结果，否则返回 needs_evidence。与经验无关的偏好等材料可返回
no_change。条目正文、kind 标签或重复出现次数本身不构成执行成功证明。

条目在排队、生成或审核期间被停用，阻止后续生成提交或批准；恢复 active 后可重新校验，不能用旧快照绕过停用。

来源分为两种用途：

| 用途 | 可供模型读取 | 可作为独立任务证据 |
| --- | --- | --- |
| 普通、已授权且符合生成准入的 Source | 是 | 仅在来源身份和任务语义支持时 |
| `lineage_only` 系统 Source | 否 | 否 |

基础 Artifact Create/Replace 保存的 `lineage_only` Source 只解释该 Revision 的管理写入来源。解析器记录其
溯源角色后停止展开，不将其 payload、嵌套正文或新的 evidence ID 交给模型。它也不能因为已经被 Artifact
引用就绕过 `is_generation_eligible`。显式提交这类 Source 返回 `source_not_eligible`。

Memory 或 Experience lineage 中的精确 Prompt Revision 只记录生成配置，标记为 `lineage_only`。
其正文不进入模型证据投影，不增加根证据数量，也不单独构成证据缺口。仅有 Prompt 引用不能创建 Experience Candidate。

Artifact 正文可以作为正式派生知识参与加工，但其正式身份、Review 状态、被检索次数与原始观察的独立性
是不同概念。缺少独立原始证据时，可以整理正式说明；不能凭空补出已验证结果、成功次数或普遍适用的结论。

## 根证据去重与冲突

同一任务结果被多个 Memory 条目、多个条目版本或 Experience 转述，仍然只有一份原始证据。解析器先按
`(scope_id, memory_artifact_id, entry_id, entry_version_id)` 合并重复正文，保留所有合法 MemoryCitation 锚点；
再沿条目级 lineage 归并根 Source。相同条目版本出现在不同 Memory Revision 中不增加观察次数。

SourceRef 相同的来源必须去重。存在受信任的任务尝试身份或明确重放关系时，还要合并对应证据组。
不同 SourceRef、相似正文或不同 Artifact identity 本身不足以证明独立观察；无法判断时保留未知状态。
不能把梦境生成的后代、旧 Revision 的转述或模型 confidence 计入新的独立支持次数。

Run manifest 保存“引用锚点 → 条目／Experience → 根 Source 组”的关系，以及模型使用的组号和
独立性说明，供审核者通过 Run Get 核对。同一 SourceRef 的正文只投影一次，保留多条引用路径；同一任务组内不同 Source 的正文仍保留，
分组只合并独立支持计数。未知独立性不显示成已验证任务数。

提示词要求区分新增、佐证、细化和纠正，核对版本、时间、环境及适用条件：

- 佐证需要新增独立证据或有意义的来源关系，重复表达不是佐证；
- 显式纠正可以由一次有效新证据支持，不统一规定“三次才有效”；
- 归纳规律需要说明其支持材料与反例，不能把单次结果扩大为普遍事实；
- 相关但条件不同的结论应保留边界；无法裁决的冲突返回 needs_evidence；
- 材料导入时间不覆盖事件发生时间，计划、建议和声明不改写成 observed outcome。

程序检查引用、类型和提交不变量；事实是否得到充分支持仍由 Reviewer 检查。RFC 不将这些语义要求描述为
确定性程序可以证明的准确率保证。

## 生成计划与 Candidate 映射

Builtin Runtime 提供固定、内部的 Dream pipeline，复用 Family-owned typed generation 和 Review writer。
首版不增加公开 planner registry 或可执行 pipeline DSL。

模型输入中的 `target_evidence_id` 标识证据投影里待替换的精确 Experience Revision。存在 target 时，其他
Experience 只提供上下文和支持材料，模型只能针对该目标提出替换；没有 target 时该字段为 null，表示创建新制品。
该标识由 Runtime 从请求的 target 构建，模型不能自行选择另一个替换目标。

生成器只返回下列三种强类型结果之一：

| outcome | 内容 |
| --- | --- |
| proposed | Family 对应的完整 proposal、使用的 evidence_ids、变化意图、reason |
| no_change | 有界 reason，无 proposal |
| needs_evidence | 有界 reason，说明缺失证据，无 proposal |

变化意图为 `create | corroborate | refine | correct | derive`：无 target 的 Experience 使用 create，
有 target 的 Experience 使用 corroborate/refine/correct，Skill 使用 derive。reason 复用现有 Candidate 的
2,000 字符上限。模型返回未知 evidence ID、错误 Family、无效 proposal 或不兼容意图时，本次生成失败。

Runtime 将实际使用的 evidence_ids 映射为精确引用。Candidate 保留被使用的条目和制品，以及其必要的根来源依赖，
不把全部请求输入自动算作支持证据。每个被引用条目的合规根 Source 由解析器补入 sources；这些是溯源依赖，
不表示每条来源都支持 proposal 的全部结论。replacement 额外保留精确 target ArtifactRef。
sources、artifacts、memory_citations 合计最多 32 项，每个条目引用计一项，根 Source 去重计数。
根 Source 被使用时，Runtime 同时保留 manifest 中通向它的所选 MemoryCitation／Experience 引用；模型只返回
根 Source ID 也不能丢掉该来源的条目路径。引用补齐后超限返回 evidence_limit_exceeded，不静默丢弃溯源。

对于 derive_skill，直接 Artifact lineage 只使用 Experience，复用 `SkillGenerationOrigin.EXPERIENCE`；
proposal 经现有 Skill canonicalization 与标准包校验。Dreaming 不生成任意文件修改指令或执行新包中的脚本。
Experience 内的 MemoryCitation 可用于追溯根证据，但 derive_skill 不将这些条目直接加入 Skill 的
memory_citations；其中间条目仅作溯源，模型直接证据仍是所选 Experience 和合规 Source。

### Candidate 与正式 Revision 的条目溯源

共享 Candidate envelope 和 ArtifactLineage 增加 `memory_citations`，默认空数组，元素复用现有 MemoryCitation。
首版仅 Experience 可持有非空值。它是直接条目证据，不以整个 Memory Ref 代替，也不创建承载正文的合成 Source。

普通 Candidate 的提议、修订和批准会校验传递证据，不套用 Dream 的生成深度、图大小或投影预算。
32 项上限约束直接引用，包括显式 MemoryCitation 所需的根 Source 依赖。仅通过 Artifact 引用可达的
Source 继续保留在该 Artifact 的 lineage 中，审核不会把它们复制到 Candidate 的直接引用。
当前权限以及直接、传递 Memory 条目的有效性仍须校验，批准时通过 Memory head 锁防止并发停用绕过检查。
Review 沿本地制品保存的 lineage 逐级遍历，包括 Skill 的历史 Revision 和上游 Experience；提案、修订和批准均
重新校验其间接引用的 Memory 条目及当前读取权限。此遍历不受 Dream 输入 Family 限制，也不把 Skill 正文加入
Dream 的证据投影。Prompt 仍只代表生成配置，跨 Scope 发布制品的边界不被隐式展开。

- Experience propose 与 Candidate revise 请求接受该可选字段；revise 省略或设为 null 时保留当前集合，显式数组完整替换，
  `[]` 表示移除。来源依赖重新解析和校验，不能保留条目引用却移除其必要根来源。现有审核端点不增加。
- Candidate Get 返回实际 memory_citations 和 sources/artifacts；审核者通过现有 Memory 条目、Artifact 和 Source
  精确读取接口核对正文与历史版本，通过 Run Get 核对生成时的证据分组。修订和批准按当前 Candidate 版本重新校验证据。
  正文始终按 Reviewer 的当前权限读取，
  不能通过 Run 或 Candidate 引用扩大读取权限。
- 批准时把当前 Candidate version 的 memory_citations 原样写入 Experience Revision 的 lineage，与正式内容、
  原有 sources/artifacts 和审核结果原子提交。Artifact exact read 返回这些引用；后续 Dream 可沿它们继续追溯。
- 运行 manifest 保存全部输入与处理情况，Candidate/Artifact 保存实际采用的条目及必要来源。审核修订后的溯源
  以新 Candidate version 为准，不能只依赖原 DreamRun、自由文本 reason 或 Dashboard 缓存。

Reviewer 继续调用现有 revise/approve/reject，Candidate 保留自己的不可变 version 和终态。DreamRun 记录创建时
的 candidate_id/version；Reviewer 后续修订不会改写运行结果，也不会把 Run 自动重开。新的 Candidate version
使用其自身完整 proposal 和证据，按新的证据集合校验，无需与原 Run manifest 完全相同；所有授权与来源准入
规则仍然适用。

### Dashboard 阅读边界

Dashboard 是默认关闭的个人内容查看器，启用要求静态 Bearer token 和 enforced 访问控制。
它展示已批准的 Experience 和 Skill，保留可点击的精确制品引用与 MemoryCitation；Skill 可沿 Experience
继续读取当时的 Memory 条目。点击引用复用现有 API，继续校验当前权限，不用最新正文代替历史版本。
Dream 创建、Run 查询及候选审核通过现有 HTTP API／Client 完成；Dashboard 不提供 Dream 管理或候选审核页面。
审核溯源和批准校验属于后端契约，不依赖 Dashboard 是否启用。

## HTTP、Client 与操作权限

本 RFC 增加三个 operation：

| operationId | Method | Path | 成功响应 |
| --- | --- | --- | --- |
| `create_dream_run` | POST | `/v1/scopes/{scope_id}/dream` | 首次受理 202；幂等重放返回同一运行 |
| `get_dream_run` | GET | `/v1/scopes/{scope_id}/dream/{run_id}` | 200 |
| `list_dream_runs` | GET | `/v1/scopes/{scope_id}/dream` | 200，游标分页 |

List 支持可选 `status`、`operation`、`cursor` 和 `limit`；limit 默认 20、最大 100。按受理顺序倒序稳定
分页，同一创建时间用 run_id 区分；新增运行不使旧页重复。List 返回摘要，Get 返回完整引用清单与结果；
二者不返回证据正文、凭据或模型原始响应。现有 exact read 接口负责按授权读取正文。
List 响应为 `{"runs": [...], "next_cursor": null}`，末页 next_cursor 为 null；摘要包含 run_id、scope_id、
operation、status、outcome、target、candidate、reason、error 和三个时间字段。时间统一为 UTC RFC 3339；
started_at 在首次执行前为 null，completed_at 在终态前为 null。

从三条 Memory 生成新 Experience 的创建示例：

~~~http
POST /v1/scopes/scp_project/dream
Content-Type: application/json
~~~

~~~json
{
  "operation": "refine_experience",
  "artifacts": [],
  "memory_citations": [
    {
      "memory_ref": {"family": "memory", "artifact_id": "mem_project", "revision": 7},
      "entry_id": "entry_pool_diagnosis",
      "entry_version_id": "ev_2"
    },
    {
      "memory_ref": {"family": "memory", "artifact_id": "mem_project", "revision": 7},
      "entry_id": "entry_pool_fix",
      "entry_version_id": "ev_1"
    },
    {
      "memory_ref": {"family": "memory", "artifact_id": "mem_project", "revision": 7},
      "entry_id": "entry_pool_counterexample",
      "entry_version_id": "ev_3"
    }
  ],
  "sources": [],
  "target": null,
  "idempotency_key": "pool-lesson-20260907-01"
}
~~~

202 响应带 `Location` 指向同 Scope 的运行 URI。Get 的完成结果示例：

~~~json
{
  "scope_id": "scp_project",
  "run_id": "dr_01",
  "operation": "refine_experience",
  "status": "succeeded",
  "outcome": "proposed",
  "target": null,
  "candidate": {"candidate_id": "cand_01", "version": 1},
  "reason": "三条记忆追溯到两次任务，补充连接池耗尽的排查边界。",
  "error": null
}
~~~

该示例省略 manifest、时间和 usage 字段。正式响应还包含 accepted_at、started_at、completed_at、attempt_count、
input_manifest（解析完成前为 null）、usage（未知值为 null），以及服务端固定的有效预算。queued/running 的
outcome、candidate、error 为 null；failed 的 outcome/candidate 为 null，error 包含稳定 code；succeeded 的
error 为 null，只有 proposed 的 candidate 非空。

| 操作 | 首版授权要求 |
| --- | --- |
| Create | Scope 的 scope.read 和 scope.contribute；有 target 时还要求目标 artifact.write |
| Get / List | Scope 的 scope.read，不提供单 Run 分享 |
| 候选 revise/approve/reject | 继续使用现有 Review 授权与 expected_version |
| 调度与模型配置 | 服务端部署配置权限，不由 Dream 请求授予 |

复用已有 AccessAction 与 ResourceRef，不新增 dream.* 角色。仅有 Artifact 分享权限不能通过 DreamRun 获得
Scope-wide 来源读取权限。引用可读不自动授权其关联来源；解析时逐项检查现有授权和生成准入。

后台保存请求主体的稳定身份，不保存凭据，也不以后台服务身份扩大其权限。在读取输入和提交候选前重新检查
所需权限；权限撤回返回 access_revoked，并停止生成或提交。查询仍检查当前权限。

Python Client 与 Runtime 暴露对应 create/get/list；CLI 可以投影为 `dream run/show/list`。MCP 如暴露这些操作，
其注解分别为创建非只读、查询只读，权限与证据边界不改变。既有 artifact-candidates API 和 Skill 发布 API
继续负责审核与分发。

## 幂等、事务与故障恢复

受理记录以 `(scope_id, principal_id, idempotency_key)` 唯一。服务端规范化输入引用后计算 request digest：

- 相同 key、相同请求返回原 run，不重新运行；queued/running 返回 202，终态返回 200；
- 相同 key、不同请求返回 409 idempotency_conflict；
- 受理时固定策略和预算；首次执行固定模型配置标识，重试与接管不能切换配置；
- failed run 使用原 key 仍返回原失败；显式重试使用新 key；
- 首版不承诺不同 key 之间的语义去重。自动发现上线前需增加稳定 work key 和已拒绝提案抑制。

身份、请求格式与当前 Scope 读取权限检查通过后，先查幂等记录，再执行新 Run 的准入检查。匹配记录按当前
查询权限返回；目标已前进、模型配置已移除或容量已满均不影响原结果重放，也不触发重新生成。

`pc_dream_runs` 保存规范化请求、manifest、生成配置标识、预算、状态、attempt、请求代次及结果。
`request_generation` 把每个 Run 关联到所属 Family 的已接受调用；Run 与通用调度意图在同一事务中受理。
`pc_artifact_candidate_versions` 和 `pc_artifacts` 各增加可空的 memory_citations 序列化列，旧行解码为空集合；
Candidate 与 Artifact 继续使用已有表和关系。无需新增 Memory 副本表或条目证据关系表。幂等唯一键、状态和结果
均位于权威数据库；正式 Revision 的条目溯源不依赖 Run 留存或日志。

运行状态只有 `queued | running | succeeded | failed`。Supervisor 任期是执行归属的唯一权威；每次 Run 尝试
另有单调 generation 防止覆盖其他尝试，但不拥有独立租约或心跳。第一次开始执行时记录绝对截止时间及模型配置标识，
重试和接管不重置它们。尚未执行时 `model_config_id` 为 null；输入快照持久化后不可改写。

模型调用、内容投影和必要的包准备在写事务外完成。成功提交使用同一短事务：

1. 在同一事务中检查 Supervisor holder、generation、有效任期和请求代次，再检查 Run 尝试 generation、运行状态、
   当前授权、条目锚点与当前 active 状态、来源可用性和目标 head；
2. 通过绑定同一事务的 Review writer 创建至多一个 Candidate；
3. 写入候选归属、outcome、精确候选引用和 completed_at，将运行标记 succeeded，并确认本次 Scope 调用；
   仍有未完成 Run 时，在同一事务中保留或登记后继调用。

无变化和证据不足同样保存有理由的终态，但不创建 Candidate。任一步失败整体回滚，不能留下 Candidate 已生成、
Run 却无法确认的半成品。若事务已提交但响应丢失，恢复直接读取终态，不再次调用生成器。

输入内容始终按 manifest 的精确版本处理。非 target 输入出现新 Revision 不改写旧快照，也不能被标为已经处理；
运行 manifest 与候选精确引用保留其历史基准。target 在生成期间前进则运行以 artifact_conflict 失败；
target 在候选落库后前进则由既有 approval CAS 阻止发布。

超时、网络瞬态故障或 Worker 崩溃允许在总预算内最多一次额外尝试。确定性输入错误、权限撤回、目标冲突和
无效模型输出直接失败。任期接管和内部 provider retry 都计入总尝试/调用预算。无法确认的调用用量保存 unknown，
不能报告为零成本。

Dream-origin Candidate 批准时仍需重新检查当前候选版本实际引用的证据是否可用且允许使用。实现可以加强共用
Review evidence validation，但不得新增自动批准分支；Reviewer 已修订的候选应校验其新证据集合。
Memory 校验包含直接及传递引用的精确锚点、hash、当前 active 状态和必要根来源；批准所需的读取权限按 Reviewer 当前身份检查。
最终状态检查与候选／Artifact 提交在同一事务中完成，并与 Memory 停用操作通过 owning Memory head 的锁串行化。
来源之后变化不抹除历史溯源；后续使用展示不可用状态，自动级联撤回既有 Artifact 不在首版范围。

## 执行归属、预算与现有处理器

Dream 使用 [Artifact Processing Supervisor](1515_artifact_processing_supervisor.md) 执行，没有独立的 scheduler、
轮询执行器或 Run 租约。DreamRun 是用户请求的业务记录，不是新的 Artifact Family，也不是通用调度任务历史。

| Dream operation | 所属 Family | Supervisor binding |
| --- | --- | --- |
| `refine_experience` | `experience` | 复用 Experience 孵化的规范 binding |
| `derive_skill` | `skill` | `skill.dream.v1` |

每个 Family 保持一个 binding。Experience 处理器先检查当前请求代次内是否有待执行 Dream；有则处理最早的一个
Run 的一次尝试，否则执行既有 Source 孵化流程。一次 Dream 调用不推进、清空或重新解释 Experience 的 Source Cursor。
Skill 的 Dream 处理没有自动 Source 扫描。两者均不自动发现值得提炼的新制品，也不因启用周期配置获得自动审核权限。

API 在短事务中保存精确请求、请求者身份和 Run，并推进所属 `(binding, scope_id)` 的 requested generation；提交后
唤醒本地 Supervisor。OceanBase 后台通过既有发现机制读取跨进程请求；SQLite 不新增空闲数据库轮询。受理事务失败
不留下 Run 或孤立调度意图。相同幂等请求的重放不再次推进调度代次。

Scope Worker 只选择不晚于 claimed request generation 的 Run。排队期间的唤醒可以合并，但各 Run 的输入、身份、
结果和预算分别保留；运行期间新增请求不能被当前调用确认。一次尝试完成后，Run 结果或有预算的重试状态与 Scope
完成确认一致提交。尚有 Run 且没有更新的已接受调用时，原子登记一个后继调用；因此关闭自动周期也能完成全部显式请求。
普通 Source dirty 保留给 Experience 的业务周期，不能用 Dream 的完成状态代表已消费这些 Source。

Supervisor 管理子进程、按 Family 的 Worker 额度、Scope single-flight、超时和故障接管。`global` 共享一个控制器
任期，`dedicated` 按 Family 分开；两种模式都使用 Experience 与 Skill 各自的 Worker 配置。
`experience_max_workers` 和 `skill_max_workers` 默认均为 1，对应 Scope Worker timeout 默认均为 600 秒。
这些是执行资源上限；每个 DreamRun 的总时间、模型次数和证据预算独立生效，子进程按该 Run 的预算构建生成器。
前台同步生成的 `generation_concurrency` 不表示后台子进程的全局配额。

Run 的确定性失败和预算耗尽是合法业务终态：持久化 failed 后确认调用，不抛给 Supervisor 无限重复生成。
未完成事务、Worker crash 或失主由 Supervisor 恢复；恢复读取同一 Run、固定快照、累计尝试和绝对截止时间。
Candidate、归属记录、Run 终态及调用确认在同一事务内提交；旧任期的任何写事务都必须整体失败。
审核等待不占 Worker，终态 Run 不重新调用模型。

OceanBase 支持 `all` 及 `api` / `background` 分离部署；SQLite 保持单宿主 `all`，两种 Supervisor mode 都可用。
API 可通过 `artifact_processing_families` 声明 Experience/Skill 能力而不配置执行模型；实际模型与 Worker 资源由后台
配置。执行端必须能重建生成器和授权适配器，不能依赖父进程内存闭包。Dream 执行使用原请求者的当前权限，
不会借用后台服务 Principal 扩大证据读取或写入权限。

`dream_enabled` 控制新请求准入；`dream_max_pending_per_scope` 限制同 Scope 未完成 Run 总数。缺少所属 Family 的
处理能力时，新请求返回 `503 capability_unavailable`，已有结果仍按当前查询权限读取和重放。
`/v1/scopes/{scope_id}/dream` 的 create/get/list 及现有 Review 接口负责业务交互，不增加通用 Job 查询 API。

初始服务端预算如下，部署者可以收紧；API 调用方不能放宽：

| 预算 | 首版上限 |
| --- | --- |
| 显式选择的 MemoryCitation 与 Experience | 合计 20 |
| 模型可见的证据项／Candidate 引用 | 各最多 32 |
| 来源关系遍历 | 最多 128 个不同节点、256 条边、8 层；仅元数据的节点也计入 |
| 整个证据投影的 UTF-8 大小 | 65,536 bytes；固定系统提示另计，仍需满足 provider context 限制 |
| 单次模型输出 | 4,096 tokens |
| 每 Run 的模型调用总数 | 2，包含重试 |
| 从首次执行起的总时间 | 120 秒，包含重试与接管 |
| 每 Run 的 Candidate | 1 |

共享并发与待执行容量必须有部署上限。达到受理容量时返回 429 并附 Retry-After；接入共享执行器不得绕过
前台／其他后台任务的预算。每 Run 的所有调用都进入 usage 统计，不仅记录最后一次成功调用。

## 错误与兼容性

| 阶段 | 结果 |
| --- | --- |
| 缺少身份／权限不足 | 401／403，沿用既有错误 envelope |
| 直接引用不存在 | 404，遵守当前资源不可见策略 |
| 无效类型、引用过量、显式 lineage_only Source | 422，包含稳定原因 |
| Memory 锚点或 hash 不匹配／条目已停用 | 422 invalid_memory_citation／memory_entry_inactive |
| 受理时目标过期／幂等冲突 | 409 artifact_conflict／idempotency_conflict |
| 执行能力缺失／容量不足 | 503／429，不创建 Run |
| 受理后证据展开超限或来源失效 | Run failed：evidence_limit_exceeded／evidence_unavailable |
| 受理后权限撤回或目标变化 | Run failed：access_revoked／artifact_conflict |
| 受理后 Memory 条目停用／内容完整性失效 | Run failed：memory_entry_inactive／evidence_unavailable |
| 批准时条目停用／证据失效 | 422 memory_entry_inactive／evidence_unavailable；Candidate 保持 pending |
| 推理失败、输出无效、预算耗尽 | Run failed：generation_failed／invalid_generation_output／budget_exceeded |

GET 一个 failed run 返回 200 和结构化 error，不把运行失败伪装成查询请求失败。错误与日志只记录稳定 code、
身份、计数和用量，不记录证据正文、完整 prompt 或凭据。proposal 与 reason 始终按不可信内容展示。

实施时新增 Dream OpenAPI operation/schema、生成的 Python Client、Runtime 入口、Run repository、Supervisor Family 适配、
Memory resolver、Family generation 适配，以及 Candidate/Artifact 条目引用和 Inbox 溯源展示。复用现有
MemoryCitation schema；扩展 Experience propose、Candidate revise/get/list、Artifact exact read 的引用契约，
并让 ArtifactDraft/Repository 传递和保存条目引用。已有引用参数的语义保持；条目引用在当前 Candidate version 中保存。
受约束的模型输出应先变为纯生成计划，再由 Run 的事务统一调用 Review writer，不能循环调用会自行提交的
高层 generate API 后补记成功。

跨 Scope 发布副本沿现有 publication_source 回到原 Revision 的条目溯源，不把 MemoryCitation 重新解释为目标
Scope 的引用。读取原 Scope 证据仍需独立授权；Dream 首版不会展开跨 Scope 的证据正文。

Dream 表按本设计直接创建，不为未发布的 Dream 数据结构提供迁移兼容。已有 Candidate/Artifact 表的条目引用字段
按增量 schema 更新覆盖 SQLite 与 OceanBase。既有数据无需重写；Memory flush、Experience incubation、Handoff、
基础 Artifact 管理、PreparedContext 和 Skill 分发的既有行为保持。OpenAPI 与生成绑定和实现一同更新。

## 验收

| 场景 | 可观察结果 |
| --- | --- |
| 只有 MemoryCitation、没有 Experience 输入 | 精确解析条目正文，生成新 Experience Candidate；批准后可追溯到条目和根 Source |
| 引用其他条目版本、篡改 hash 或整份 Memory Ref | 准入失败，不通过 latest、摘要或邻近条目补全 |
| Memory 某一条目入选 | 模型只获得该条目和合规来源，未选中条目正文不进入输入 |
| 三条 Memory 与一个 Experience 共用两份任务结果 | 保留引用路径，根来源仅两组；未知任务独立性不虚报验证次数 |
| 仅偏好、计划或无可用行动结果证据 | no_change 或 needs_evidence，不编造观察结果 |
| 当前条目版本前进／条目停用 | 精确历史正文并标记版本差异／阻止提交或批准 |
| Reviewer 修改或移除 MemoryCitation | 新 Candidate version 重新校验来源；批准后的 lineage 与该版本一致 |
| 从已批准 Experience 再派生 Skill | 沿 MemoryCitation 去重根证据，Skill 直接 lineage 保持 Experience；不回灌 Dream 报告 |
| 循环来源或展开超限 | 循环检测终止，超限失败，无部分 Candidate |
| 三份独立任务经验补充旧判断 | 一个有证据和 target 的 pending Candidate；批准后同 identity 的下一 Revision |
| 同一根来源被多份 Experience 转述 | 不增加独立证据次数；不生成虚假的重复验证结论 |
| 输入包含 lineage_only 或跨 Scope 来源 | 不向模型泄漏内容，按准入规则拒绝或记录不可用 |
| 已足够完整／证据不足 | succeeded + no_change／needs_evidence，没有 Candidate |
| 模型伪造引用或错误 Family | failed，无 Candidate 和 Artifact 写入 |
| 相同 key 重放／相同 key 改请求 | 同一 Run／409 |
| Supervisor 接管，旧 Worker 迟到 | 最多一个结果提交；旧任期或尝试 generation 无法写入 |
| 多个 Run 合并唤醒／生成中又有请求到达 | 每个 Run 分别执行；不吞掉新代次，关闭自动周期也能完成后继请求 |
| Dream 与 Experience Source 孵化共用 Family | Dream 完成不推进 Source Cursor，不清除普通 Source dirty |
| API 无模型、后台配置模型 | OceanBase 的 global/dedicated 模式均可跨进程受理、生成、审核 |
| Candidate 事务中途失败／提交后响应丢失 | 整体回滚可重试／恢复已有终态且不重复产物 |
| 生成中或审核前 target 前进 | 运行冲突／审批冲突，不覆盖新 head |
| 排队后授权撤回或证据不可用 | 不继续读取或提交未获授权的内容 |
| derive_skill 成功 | pending Skill，通过既有审核与包校验；不自动执行或分发 |
| pending、rejected 和 DreamRun 文本命中查询 | 不进入 Artifact search 或 PreparedContext |

SQLite 与 OceanBase 使用同一套持久化、Supervisor 任期隔离、原子提交和审核行为测试。另以真实配置的模型完成端到端验收，
确认错误和 usage 可见，不把 mock 测试当成实际生成效果。

真实验收入口使用 `.env` 的推理配置和 OceanBase 连接，在隔离数据库中执行本地故障注入任务，形成 Memory 证据，
经真实 HTTP、Supervisor 子进程和 LLM 完成 Experience 与 Skill 的生成、审核、召回和条目停用检查，结束后清理数据库：

```bash
uv run python -m tests.e2e.artifact_dream_real --env-file .env \
  --case sqlite-global --output /tmp/dream-sqlite-global.json
uv run python -m tests.e2e.artifact_dream_real --env-file .env \
  --case oceanbase-split-dedicated --output /tmp/dream-oceanbase-split-dedicated.json
```

完整矩阵还包括 `sqlite-dedicated`、`oceanbase-global`、`oceanbase-dedicated` 和 `oceanbase-split-global`。
Memory 条目提取使用确定性适配器；Dream 生成使用配置的真实模型。能力验收不代表候选质量或后续任务收益的统计证明。

效果评估按时间分开历史输入与后续保留任务，比较无梦境、普通摘要和本设计三组，记录候选接受率、审核修改量、
错误概括、后续任务完成情况以及前后台总 token 成本。后续任务答案不能进入梦境输入。首版交付必须包含至少一个
“实际任务证据 → Memory 条目 → Experience 候选 → 审核 → 后续任务使用”的完整示例，覆盖重复根来源和
条目停用；自动发现的发布以质量和待审负担可接受为前提。

# Drawbacks

- 受控提炼需要额外模型成本和审核时间；没有新证据的重复请求仍可能产生相似候选。
- 保留根证据与历史边界增加解析复杂度，32 项和输入预算可能要求用户缩小选择范围。
- 人工审核与类型校验都不能保证归纳正确，需要后续任务验证；首版不承诺效果提升幅度。
- 条目级溯源需要扩展已有 Candidate/Artifact 序列化与审核校验；Memory 修改和 Skill 包修订仍需各自的变更契约。

# Rationale and alternatives

直接定时调用现有 generate 最省代码，但缺少耐久运行结果、幂等和故障后确认，并且其独立候选写入无法与运行
完成状态原子提交。本设计复用其 Family 能力，增加一个有界运行和统一提交边界。

直接覆盖正式制品可以降低审核成本，但会使错误归纳立即影响后续任务。Candidate 与 Revision 能展示具体变更，
也能利用现有 CAS 防止覆盖并发修改，因此所有 Dream 产物固定进入 Review。

全量 nightly 总结容易重复消耗并掩盖条件差异；Light/REM/Deep 多阶段或多 Agent 架构也不是首版收益的必要条件。
先验证明确选定的小集合及后续使用，再决定自动发现策略。

不做 Dreaming 时，用户仍可手动调用 generation，但需要自己组织来源、重试和结果追踪。该方案适合偶发操作，
难以支持后续稳定的后台提炼。

# Prior art

本节依据 2026-09-07 核验的固定开源源码，借鉴机制而不承诺相同运行效果：

| 项目 | 采用的启发 | PowerContext 的对应边界 |
| --- | --- | --- |
| [ReMe](https://github.com/agentscope-ai/ReMe/blob/354837f9af94cb8f0df13fc66a10380895a0343d/reme/steps/evolve/dream/integrate.yaml) | 跨制品抽象及新增、佐证、细化、纠正 | 通过 Candidate 发布，避免直接修改正式内容 |
| [Honcho](https://github.com/plastic-labs/honcho/blob/be54355545b64ddb10203829d323861f52423685/src/utils/agent_tools.py#L1531) | 原始观察与梦境派生结论分层 | 根证据去重，派生数量不证明独立支持 |
| [Letta Code](https://github.com/letta-ai/letta-code/blob/701f2a5367828847313876c735ade27b9df97689/src/agent/memory-worktree.ts) | 隔离修改、条件合并和成功消费 | 精确 target、Candidate version 与事务完成 |
| [Hindsight](https://github.com/vectorize-io/hindsight/blob/f19c424e0c5833d5219185c1fa0dcc6a10fc0a81/hindsight-api-slim/hindsight_api/engine/consolidation/consolidator.py) | 生成计划与输入进度原子提交 | Candidate 与 Run 终态同一事务 |
| [Cognee](https://github.com/topoteretes/cognee/blob/e93a4f0c76e6af27142c25189f14c200e1749d51/cognee/modules/memify/skill_improvement.py) | 使用结果驱动 Skill 改进提案 | 作为后续扩展，保留 Revision 与包生命周期 |
| [OpenClaw](https://github.com/openclaw/openclaw/blob/233dabe750abe9deb7aa73a67b9b9a28e5cca85e/extensions/memory-core/src/dreaming-consolidation-candidates.ts) | 来源准入和派生内容隔离 | lineage_only 与运行报告不成为新事实 |
| [EverOS](https://github.com/EverMind-AI/EverOS/blob/8754365c76daa2f13521fcd29a53044bba083403/docs/reflection.md) | 增量簇加工与替代关系 | 多制品合并需另行定义原子治理和恢复 |

# Unresolved questions

首版的操作、输入、审核、事务和权限边界由本文确定，没有依赖“模型自行决定”的未决契约。
自动发现的收益阈值、共享执行器的部署配额和跨 Family 的合并治理需要后续实测或专项设计；它们不阻塞手动提炼。

# Future possibilities

- 定时选择有新增根证据、纠正或使用反馈的主题，采用稳定 work key，抑制无变化与已拒绝提案的重复生成。
- 增加 Handoff、完整 Skill 包和 Topic Memory 的专门输入解析器；定义 Memory 条目修订的独立变更契约。
- 根据真实 Skill 使用 Source 修订精确 Skill Revision，复用既有 usage origin 和包校验。
- 用原子变更组表达多个制品合并、replacement、停用与恢复，避免单个 Candidate 冒充多目标事务。
- 维护持续更新的主题文档或在隔离环境做合成演练；合成结果始终保留其来源性质，不冒充真实执行证据。
