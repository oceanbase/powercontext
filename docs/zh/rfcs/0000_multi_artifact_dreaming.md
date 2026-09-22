- Proposal Name: `multi_artifact_dreaming`
- Start Date: 2026-09-22
- Related RFCs: [Artifact Dreaming（1510）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1510-artifact-dreaming.md)、[Candidate 与 Review Inbox（0050）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/0050_artifact_candidate_review_inbox.md)、[Profile（1485）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1485_profile_artifact.md)、[Topic Memory（1417）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1417_topic_memory.md)、[Prompt（1468）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1468_scope_owned_prompt_management.md)、[Tag（1467）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1467_artifact_tags.md)、[Processing Supervisor（1515）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1515_artifact_processing_supervisor.md)


# Summary
将 Dream 从现有的 Experience 提炼与 Skill 派生，扩展为按制品生命周期执行的受控复盘能力。新增 Memory 条目修订、Profile 画像校正、Topic Memory 主题校正、Handoff 交接刷新、现有 Skill 修订和 Prompt 改进，并为 Tag 提供独立的分类变更提案。所有操作沿用“精确证据、后台运行、待审候选、条件提交、完整来源”的原则，一次运行只处理一个目标，不直接覆盖正式状态。所有新增 Dream 操作都必须先写入待审核 Candidate；只有 Reviewer 批准当前候选版本并通过目标当前基准校验后，变更才能生效：Artifact 由所属 Family 的写入器提交新 Revision、推进 head，Tag 按其 ETag 条件替换标签集。Dream 生成成功或候选入库本身不改变正式状态。

Dream 负责提出可解释的变化，Family adapter 负责语义校验和提交，Reviewer 负责批准，应用或评测服务负责实际业务验证。Source 保持原始证据身份，不被 Dream 改写；Tag 保持 catalog 属性身份，不伪装成 Artifact。第一阶段交付 Memory、Profile、Topic Memory，后续阶段扩展 Handoff、Skill、Prompt 和 Tag。自动发现、全量夜间扫描、多目标原子合并及无人值守发布不作为本提案的交付前提。

# Motivation
## 现有能力与缺口
当前 `DreamOperation` 只有 `refine_experience` 和 `derive_skill`。前者读取精确 Memory 条目和 Experience，后者读取 Experience，均可补充合规 Source，输出最多一个候选。DreamRun 已包含幂等、后台执行、证据 manifest、预算和运行结果。

其他制品也会因证据增加而失准：Memory 中旧事实未校正，Profile 把临时出差当成常驻变化，Topic Memory 没有吸收新排查结论，Handoff 落后于真实进度，Skill 的适用范围缺少反例，Prompt 持续产生同类提取错误，Tag 出现重复或错误分类。这些问题无法全部通过新增 Experience 解决。

扩展不能只是放宽一个 family 枚举。当前实现至少存在以下差异：

| 当前实现 | 扩展必须解决的问题 |
| --- | --- |
| Memory 正文位于 Entry Version，Artifact 保存 manifest | 不能通过替换 manifest JSON 绕过条目写入、hash 与索引维护 |
| Profile 已有 Candidate 审核，但绑定 Source window、Policy pending 指针和游标推进 | Dream 选取离散证据，不能冒充连续消费窗口 |
| Topic Memory 有自身生成、融合和检索投影 | 手动修订不能被旧后台结果覆盖，旧 chunk 不得与新 head 混用 |
| Handoff 有 prepare、commit/activate、接收方核验等语义 | 生成文本不能替代交接激活或接收确认 |
| Prompt 是运行配置，正式 head 会影响后续推理选择 | 发布不是纯内容编辑，需要评测门槛、明确权限及回滚 |
| Tag 使用独立的可变集合和 ETag | 标签变化不应创建 Artifact Revision |
| Candidate 的 MemoryCitation 当前限制为 Experience | 新 family 必须显式增加合法引用与审核校验，不能全局取消约束 |


## 目标与非目标
目标是降低过时背景、错误概括和不完整交接造成的后续任务错误，并使每次变化可核验、可拒绝、可追溯。候选数量、摘要长度或版本增长均不代表效果提升。

本提案不执行业务工具，不扩张现有权限，不修改 Source，不自动忘记用户信息，不自动合并不同主体，不自动发布 Skill 包，也不从缺失使用记录推断某条记忆没有起效。跨 Scope 证据正文读取和跨目标事务不在本期范围。

# Guide-level explanation
## 1. 用户模型
用户在一个 Scope 内选择目标的精确版本和相关证据，选择对应 Dream 操作。系统后台比较现状与证据，给出完整候选、差异、理由、逐项引用和证据不足说明。Reviewer 批准后，目标按自身生命周期产生新状态。其他制品需要更新时，用户再提交独立运行，不自动串联发布。

```latex
选定目标、精确证据和问题
              |
              v
DreamRun：解析来源、冻结证据、生成与类型校验
              |
       +------+----------------+
       |                       |
  有充分改进依据         无实质变化 / 证据不足
       |                       |
       v                       v
版本化候选及差异         no_change / needs_evidence
       |
       v
审核当前候选版本、证据、权限和目标基准
       |
       +-- Artifact：Family writer 提交新 Revision
       +-- Tag：以 ETag 条件替换当前标签集合
       |
       v
应用显式使用；实际结果作为新 Source 留存
```

## 2. 操作矩阵
| operation | 目标与输出 | 复盘内容 | 生效边界 |
| --- | --- | --- | --- |
| `refine_experience`，已有 | 新 Experience 或现有 Experience 的修订候选 | 情境、行动、结果、反例及适用范围 | 沿用现有审核 |
| `derive_skill`，已有 | 新 Skill 候选 | 从 Experience 派生指令 | 沿用现有审核和显式导出 |
| `revise_memory`，新增 | 一个 Memory Artifact 内指定条目的修订候选 | 事实校正、时效表述、偏好和约束冲突 | 新 Entry Version 与 Memory Revision 原子提交 |
| `revise_profile`，新增 | 当前 Profile 的完整快照候选 | 主体归属、稳定偏好、临时安排与明确变更 | 人工审核后替换 head，不消费 Source window |
| `revise_topic_memory`，新增 | 一个 Topic Memory 的完整内容候选 | 新进展、已排除假设、未决问题及来源 | 保持 topic identity，提交新版本与检索更新意图 |
| `refresh_handoff`，新增 | 当前 Handoff 的完整修订候选 | 目标、已完成事项、阻塞、下一步 | 批准只形成正式版本，仍需现有显式激活与接收核验 |
| `revise_skill`，新增 | 当前 managed Skill 的替换候选 | 使用反例、步骤、前置条件与检查方法 | 通过内容校验、评测和审核后发布，安装执行仍独立 |
| `revise_prompt`，新增 | 已注册 Prompt key 的完整配置候选 | instructions、demonstrations 的失败模式 | 评测与配置写权限通过后发布，后续推理解析新 head |
| `revise_tags`，新增 | 单一逻辑 target 的标签集合变更候选 | 错误分类、同义重复、缺失分类 | ETag 条件替换，不创建 Artifact Revision |


所有新增操作要求目标已存在，第一期不通过 Dream 创建空 Profile、空主题、空交接或新 Prompt key。条目合并、主题拆分、批量重命名标签作为后续治理能力单独定义。

## 3. 客服场景：同一组原文如何驱动不同复盘
用户 U1 的个人 Scope 为 `S_U1`，订单工单 Scope 为 `S_TICKET_2048`。应用通过已有主体绑定分别投递属于该范围的 Source；不因同一客户而自动跨 Scope 读取。以下两个表分别展示各自 Scope 内的模拟证据。

**个人背景证据：**

| Source | 原文与时间 | 语义 |
| --- | --- | --- |
| S01 | 9/01：“我长期在上海工作，联系我尽量在下午。” | 原常驻地与明确联系偏好 |
| S02 | 9/08：“这周去深圳出差，15 号回上海。” | 带期限的临时安排 |
| S03 | 9/21：“计划变了，已经搬到深圳，以后常驻这里，仍然下午联系。” | 明确长期变更，偏好未变 |


**工单处理证据：**

| Source | 原文或实际结果 | 语义 |
| --- | --- | --- |
| S11 | 客户：“页面显示已签收，但我没有收到。” | 待核验投诉 |
| S12 | 客服：“猜测是门卫代收，尚未核对凭证。” | 猜测，不是事实 |
| S13 | 核验记录：“签收照片并非客户地址，已提交物流复核。” | 已核验观察与下一步 |
| S14 | 任务结果：“物流确认投递错误，补发已创建；物流单号尚未回传。” | 已完成与待办分别记录 |


这些证据形成以下可独立审核的变化：

| 制品 | 复盘前 | 候选示例 | 不允许的推断 |
| --- | --- | --- | --- |
| Memory，S_U1 | E_CITY@v1：“长期在上海工作” | E_CITY@v2：“9/21 起常驻深圳，依据 S03” | 仅凭 S02 就认定搬家 |
| Profile，S_U1 | 常驻上海，下午联系 | 常驻深圳，下午联系；保留未受影响背景 | 根据客户投诉推断性格或收入 |
| Topic Memory，S_TICKET_2048 | “疑似门卫代收，待查” | “已确认投递错误；补发已创建，单号待回传” | 将未完成补发写成客户已收到 |
| Handoff，同工单 Scope | “下一步核对签收凭证” | “凭证已核验、补发已创建；接手确认单号并通知客户” | 代替接手方确认权限和当前物流状态 |
| Experience，同工单 Scope | 无该任务的复盘 | “签收争议先核对签收人与地址凭证，再决定处置” | 单个案例足以证明普遍有效 |
| Skill，同 Scope 且有合法使用反馈 | “未收到货则直接补发” | 增加凭证核验、适用条件、人工升级点和结果检查 | 自动补发、退款或执行脚本 |
| Prompt，S_U1 | 把短期出差提取成常驻变更 | 加入临时安排反例和明确搬家的正例 | 覆盖服务端 trust rules 或增加工具权限 |
| Tag，工单主题 | `[物流, 门卫代收]` | `[物流, 投递错误, 单号待回传]` | 标签等于根因证明或资源授权 |


操作彼此独立。Memory 修订后不会暗中重写 Profile，Topic 更新后也不会自动激活 Handoff。若需要协调，应用在前一步正式发布后读取精确结果，另行提交下一步 Dream，并承担协调与失败处理。

## 4. 触发与结果
新增能力先使用显式触发：用户点击复盘，或 integration 在有授权的任务结束、明确纠正、使用反馈到达时提交请求。事件只是触发理由，不是事实证据，必须携带可核验引用。

Dream 保留 `queued/running/succeeded/failed` 状态及 `proposed/no_change/needs_evidence` 结果。`succeeded` 不表示候选获批，也不表示业务效果提升。证据不存在、权限撤回、目标冲突和预算耗尽是失败，不能降格为无变化。

周期性选择证据需要后续独立的 discovery policy；不能将已有 Source-window 周期生成描述成自动扫描所有制品的 Dream。新增操作的审核要求只约束 Dream 发起的变更；同一 Family 已有的自动生成流程继续沿用原有触发、审核策略和生效逻辑，不因接入 Dream 被强制改为待审，也不能借其自动发布配置绕过 Dream 审核。

# Reference-level explanation
## 1. 扩展架构
在既有 DreamService、Evidence Resolver、ReviewService、Family writer 和 Processing Supervisor 上引入内部 `DreamOperationSpec` 注册表。每个 spec 显式声明：

| 字段 | 责任 |
| --- | --- |
| operation / spec_version | 稳定操作名和操作规范版本 |
| target_kind / allowed_evidence | 合法目标、正文输入及引用角色 |
| proposal_schema / validator | 类型化输出与确定性业务约束 |
| processing_family / binding | 所属执行资源与现有 Supervisor 绑定 |
| review_adapter / commit_adapter | 审核校验、锁顺序和原子提交 |
| effect / evaluation_policy | 审核后的实际影响及必须满足的评测要求 |


注册表是服务端固定配置，不接收请求内 Python 路径、任意工具名、SQL 或自定义执行代码。Family registry 可发现读取能力，不等于已经支持 Dream。只有解析、生成、审核、提交和后台执行能力全部就绪时，operation 才能对外启用。

现有 `DreamService.execute()` 按 Skill 与 Experience 二分 operation，需要改成按所属 binding 查询它支持的 operation 集合，再按 accepted_at、run_id 稳定排序领取一个 Run。不得通过“非 Skill 就当 Experience”的默认分支执行新增操作。

## 2. 协议与兼容性

扩展现有 `/v1` Dream、Artifact Candidate 与 capabilities 接口，不新增平行的 v2 Dream/Review 路径。现有请求与响应字段保持原义；新 operation 使用类型化目标、证据和候选 proposal。`openapi/powercontext.yaml` 是源契约，Client 与服务端生成代码同步更新。

| 现有资源 | 扩展内容 |
| --- | --- |
| `POST/GET /v1/scopes/{scope_id}/dream`、`GET /v1/scopes/{scope_id}/dream/{run_id}` | 提交、列出、读取新增操作；沿用状态、预算、幂等与 run_id |
| `POST /v1/artifact-candidates/list`、`get` | 读取新增 Artifact Family 的类型化 Candidate |
| `POST /v1/artifact-candidates/revise`、`approve`、`reject` | 复用 candidate_id、expected_version 和现有审核权限；由 Family adapter 校验并提交 |
| `GET /v1/capabilities` | 增加 `artifact_dreaming_operations`，声明操作、可用性、输出种类与生效方式 |

当前生成 Client 将 Dream operation 和 Candidate proposal 解析为封闭枚举/联合类型。新操作上线前必须协调升级服务端、Python Client、CLI、集成插件和 Dashboard；旧 Client 不得在不理解新类型的情况下读取新 Run 或 Candidate。不能以忽略未知字段、伪造旧 operation 或返回空 proposal 掩盖不兼容。部署验收须明确最低 Client 版本；若生产环境无法协调升级，应先提供按能力过滤的兼容读取策略，再启用新 operation，而不是让旧 Client 在 list/get 上解析失败。

Tag 不是 Artifact；C 阶段的 Catalog Change Candidate 与 Artifact Candidate 的结果类型不同。只在该阶段证明现有接口无法准确表达 ETag 与结果类型后，才增加独立的 Tag 候选接口。可信评测登记接口同样归 B1 阶段，不成为 A0/A1 前置条件。

新增操作请求复用 `operation`、`artifacts`、`memory_citations`、`sources` 和 `idempotency_key`。Artifact 操作的 `target` 保持精确 ArtifactRef，并必须同时出现在 `artifacts` 中；Memory 的目标条目及版本通过 `memory_citations` 指定，必须属于该目标 Memory。引用去重后计入统一预算；目标内容不能单独证明自身断言。Profile、Topic Memory 和 Handoff 的待修订目标仅按权限、存在性与精确 head 校验；其历史引用失效不阻止以新的有效证据纠正目标。本次选中的支持证据仍须递归校验，不能通过目标角色豁免。Tag 的 TagTarget、expected_etag 和正文基准由 C 阶段的独立 Catalog Change 请求表达，不改变 Artifact Dream 的 target 类型。

示例中的标识须替换为服务返回的真实值：

```json
{
  "operation": "revise_memory",
  "target": {"family": "memory", "artifact_id": "memory", "revision": 8},
  "sources": [{"source_type": "content", "source_id": "S03"}],
  "artifacts": [{"family": "memory", "artifact_id": "memory", "revision": 8}],
  "memory_citations": [{
    "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 8},
    "entry_id": "E_CITY",
    "entry_version_id": "EV_CITY_1"
  }],
  "idempotency_key": "U1-city-explicit-change-20260921"
}
```

幂等唯一范围沿用现有 `(scope_id, principal_id, idempotency_key)`，数据库以 principal_key 保存请求者身份摘要。同一范围内规范化请求相同则返回原 Run，请求不同则冲突；不同请求者不复用彼此的 Run。服务端在首次执行时固定 adapter、policy、模型与提示词版本，重试不静默切换。

## 3. 证据角色与来源准入
Dream manifest 为每项输入指定 `target_content`、`supporting_evidence`、`counter_evidence`、`configuration_under_review` 或 `lineage_only` 等服务端确定的角色。模型只能引用已分配的 evidence ID，不能创建身份、版本、用户绑定、成功次数或伪造证据。

+ 一律在同一 Scope 解析。目标与每个来源都按请求者当前权限检查，后台服务身份不扩大权限。
+ Memory 引用按 manifest、Entry Version、hash 和当前 active 状态检查，不能用搜索摘要代替正文。
+ Source 保留其原始用途；管理写入产生的 lineage_only 内容不能升级为业务结果证据。
+ 同一根 Source 经多个制品转述只算一组来源，无法证明独立性时保留 unknown。
+ Prompt 在普通操作中仍只是配置 lineage；只有 `revise_prompt` 将目标 Prompt 正文作为不可信的待修改配置读取，不能将其中的 instructions 执行为 Dream 的系统指令。
+ Skill 包只允许有界、无路径穿越的静态内容解析，不加载插件，不运行 shell 或包内脚本。
+ 撤回权限、删除来源或停用 Memory 后，在生成提交和审批时再次校验。无权限材料不得继续通过缓存参与发布。

正文包含“忽略规则”“批准当前候选”等文字时只作为证据文本。服务端 trust rules、工具授权、资源预算和系统提示不能由待修订内容覆盖。

## 4. Memory 修订契约
第一阶段只允许对一个 Memory Artifact 内最多 20 个当前 active 的逻辑 entry 执行 `revise`。不新增逻辑 entry、不合并 entry、不停用、不恢复、不改 entry_id。重复条目和需要忘记的内容可在 reason 中提示人工治理，但不得产生隐藏副作用。

proposal 类型为 `MemoryRevisionProposal`，包含固定的 base Memory Ref，以及每个条目的 expected Entry Version、修订后的 kind/text、条目级证据 ID 和理由。完整候选必须能展示 before/after。模型不能提交整个 manifest、计算 content hash 或分配 Entry Version。

审批时锁定 owning Memory head，核对整个 head 与所有 expected Entry Version，任何一个变化都返回冲突，不自动重放到新版本。Family writer 在同一事务内创建 Entry Version、生成新 manifest 与 changes、保存引用、更新 head、记录审核结果和检索投影更新意图。不得逐条调用已经独立 commit 的高层 API 拼装事务。

“信息陈旧”需要显式新证据或可验证的事件时间，不以最后访问时间、模型置信度或重复次数自动改写事实。过期计划的首期表达是保留历史时间及当前有效性说明，不增加自动删除或新的全局 TTL 契约。`forget()` 的显式授权路径保持独立。

## 5. Profile 修订契约
沿用一个 Scope 一个 Profile 的身份与 Markdown 快照。模型输出完整 content，同时生成逐段变化理由、主体依据和证据引用。保持当前生成策略对范围、主体和允许信息的限制，接收请求时固定 Policy，生成输入携带该快照；排队、生成或审核期间 Policy 改变应阻止发布并要求重新生成。失效候选仍可拒绝以结束审核。

新增 `ProfileDreamCandidateProposal` 和可区分的 `generation.mode=dream_review_approved`，附精确 dream_run_id 和 policy revision/digest。Dream 模式不允许 `source_window`，也不伪造 after/through。

当前 `decide_profile()` 依赖 Policy.pending_candidate_id，并在批准或拒绝时推进 Source Cursor。新路径必须按 candidate origin 分派，只有 Source-window 候选继续走原逻辑；Dream 候选不读取或修改该 pending 指针，不推进游标、不清除普通 Source dirty。两种候选都按同一 Profile head CAS 提交。任一先发布，另一候选保持 pending 并显示冲突，不能覆盖新 head。

`activation_mode=automatic` 继续按原有逻辑控制 Source-window 周期生成及其直接生效路径；`review_required` 继续沿用原有候选审核路径。两者都不决定 Dream 的审核策略：Profile Dream 一律先生成 Candidate，批准后才按 Profile head 的当前版本条件提交。发布新 Profile 后，PreparedContext 是否读取它仍取决于原有显式 assembly 配置。

## 6. Topic Memory 修订契约
Family 名称使用现有 `topic-memory`。候选保持 `title/summary/detail` 完整结构，按证据区分已确认事实、猜测、已排除项、未决问题和下一步。修订一个 topic identity，不在本阶段拆分或合并主题，不修改 topic routing identity。

审核与后台融合都以同一 head 为条件提交，并复用 Topic Memory 既有的完整投影原子发布契约。收到批准请求后，先针对精确 candidate_id、candidate_version 和 proposal digest，在发布事务外准备完整的 FTS、当前检索配置所需的 vector 及 detail chunk 投影；此时不推进 head，也不将投影加入正式检索。投影准备失败时返回错误，候选保持 pending，现有正式版本及检索结果继续可用。

投影准备完成后，由 Family adapter 按统一锁顺序进入批准事务，重新核对候选版本与 digest、目标当前 head、证据、权限和检索配置，调用既有 publish_revision 等事务内发布逻辑，原子提交新 Topic Revision、head、完整检索投影、发布时间及 Candidate approved/result。任何条件变化或提交失败都不产生部分发布。候选被修订后必须重新准备对应投影，不能复用旧正文的投影；外部模型或 embedding 调用不得占用发布事务的数据库锁。

本操作不增加“head 已更新但索引待重建”的中间状态，不新增 Topic 索引 outbox、待完成状态或专用恢复流程。启动时继续执行既有 head、active projection 和 chunk 一致性检查。

Dream 不推进 Topic Source Cursor。后续正常融合以新 head 作为基准处理新增 Source，不从旧快照重建后覆盖审核结论。相关后台 CAS 与投影一致性验收是开启该 operation 的前置条件。

## 7. Handoff 刷新契约
只针对精确 Handoff Revision，保持 work identity、目标任务与原有结构化内容约束。生成输出、候选修订及最终提交均要求 objective 与目标版本一致；改变任务应走独立的显式操作。对已完成、未完成、阻塞和下一步的每一项变更提供结果来源。没有新的状态证据时返回 no_change 或 needs_evidence，不能仅凭时间流逝宣称任务完成。

候选生成不改变当前激活交接，不写 accepted acknowledgement，不变更任务授权。批准形成正式 Handoff Revision，应用随后按现有接口显式激活该版本，接收方仍核对 live state、capability 和 authorization。

当前 prepare/commit 已校验的 token、basis、引用和激活条件不能因增加 Candidate 被旁路。实现需把可复用的内容校验和非激活提交提取到事务内 writer，再由 Dream 审核 adapter 调用；不得伪造接收方核验记录。已被接收的历史 Revision 保持不变，新版本不改写旧接收记录。

## 8. Skill 修订与评测契约
`derive_skill` 保持原语义，`revise_skill` 明确指向一个已有 managed Skill。首期支持 instruction-only Skill，按包内实际文件判断支持范围，不以 package 字段是否存在判断。现有纯指令内容也会规范化为仅含根目录 SKILL.md 的标准包；这种单文件包属于支持范围。对于尚无 package 的旧纯指令内容，沿用既有规范化流程生成单文件标准包。

服务端读取目标精确 package digest 对应的包快照，只有包内仅含根目录 SKILL.md 时才允许本期修订；包含额外附件、脚本或依赖文件的包返回明确的 unsupported_target，不默默丢弃文件。修订保留未授权改变的 frontmatter 和元数据，生成新的标准包，经过既有包校验、评测与审核后提交精确 package 引用；不得只修改缓存的 instructions 字段而保留旧 package 引用。多文件包的路径、签名和资产替换另行设计。

输入需要与目标版本关联的任务结果、使用反馈或已批准 Experience。输出保留适用范围、前置条件、指令与验证要求，不推断“被选中”就是“被执行”或“有效”。负面用例不删除，不以新增步骤数作为质量指标。

审核要求可信评测记录固定到 candidate_id、candidate_version、proposal_digest、目标版本、评测集版本和评测器版本。评测服务由部署者注册，单独鉴权提交。普通用户随手填写 `passed=true` 或上传自述 Source 不能满足门槛。评测在隔离环境运行，无生产凭据和任意外部副作用，预算独立计量。

确定性 schema、来源和禁止越权检查必须全部通过；业务门槛由版本化 evaluation policy 固定，失败和 unknown 都不能当成通过。Reviewer 修改实质内容或证据后，旧评测记录失效。批准不安装、不执行、不自动分发 Skill，原有显式使用路径保持。

## 9. Prompt 改进与生效契约
仅支持已注册且允许自定义的 Prompt key，例如 `memory.extract`。输入包含当前精确 Prompt、已核验的错误输出及原始 Source、期望输出与独立保留测试集。错误输出作为被诊断对象，不作为事实来源；expected output 需要人工标注或其他可信来源。

候选只修改现有 `mode/instructions/demonstrations`，所有 demonstration 按该 Prompt Definition 的输入输出 schema 校验。首期输出固定为 custom 模式，不修改 Prompt key、模型配置、trust rules、工具集合或资源上限。

训练示例与保留测试集必须按根 Source 分离，不能把保留答案写进 demonstrations 后宣称改进。评分对比同一输入和固定执行配置下的 baseline 与 candidate，检查误提取、漏提取、主体混淆、时态混淆、token 成本和 trust-rule 回归。只有已注册的评测器可以登记评测通过记录。

当前 Prompt head 在后续 inference 中解析，因此批准该候选就是配置发布。审批 UI 和响应必须明确显示这一影响，Reviewer 同时需要 review authority 与现有 Prompt 写权限。先保存一个“已批准但不生效”的 head 会与现有语义冲突，本期不增加该中间状态。

在途推理继续使用已冻结的旧 Prompt Revision；发布后启动的新推理解析新 head。回滚读取旧内容，再写成新的单调递增 Revision，不回退版本号。Dream 自身的服务端提示、Review 校验及评测评分器不能成为 `revise_prompt` 的目标，避免修改自身审核标准。

## 10. Tag 变更契约
Tag 不是 Artifact Family。新增 `CatalogChangeCandidate`，复用 Review Inbox 的列表展示、比较与审批交互，但使用独立 schema、存储和提交 adapter。其状态为 pending/approved/rejected，批准结果为 target、完整标签集及新 tag_digest/ETag，不填充 `result_artifact`。

每次仅更新一个现有 TagTarget，保存 expected_etag、basis_ref/basis_citation、完整 before/after tags、证据和理由。沿用现有 NFC/casefold 规范化、数量及长度限制。标签增删都需要审核，不能给 target 以外的资源自动迁移标签。

审批时同时核对标签 ETag 与正文基准。正文 head 或条目版本改变，即使 ETag 未变也要求重新提案，防止按旧内容打标。按既有权限读取 basis；ETag 冲突返回 412，正文基准冲突返回 409。事务内替换标签集合并标记候选 approved，Artifact Revision、digest、embedding 均不改变。

统一 Inbox 在服务层聚合两种候选，不创建 `family=tag`，不把新 Tag 审批混进要求 approved 必须有 result_artifact 的旧 DTO。首期不批量改名、不定义全局别名、不检查或修改外部系统自动化。涉及业务规则的标签需要业务方确认其影响，PowerContext 标签本身始终不授予权限。

## 11. 候选模型与审批事务
对新增 Artifact family 注册专门 proposal 类型和审核 adapter，复用已有候选版本、pending/approved/rejected 及 result_artifact 语义。所有 Dream-origin Artifact 变更，无论该 Family 的既有自动生成是否需要审核，都只能先进入 pending Candidate；批准时重新核对候选版本、证据、权限和目标当前 head，条件提交新 Revision 并原子标记 approved。审核前以及冲突时不得让 Dream 结果进入正式 head 或检索生效状态。既有非 Dream 自动生成路径保持原有策略，不以 Family 级开关统一改变其行为。MemoryCitation 从“仅 Experience”改为逐 operation/family 的白名单校验，而不是无条件放开。

新增 Candidate 保存 origin、operation、精确 target、proposal digest、evidence manifest 引用、validation policy digest 和可选 evaluation refs。需要模型生成的只有内容计划、已给定 evidence ID 及说明，目标身份、CAS token、事务结果和 generation 元数据均由服务端填写。

审批共同步骤：校验身份及当前权限；读取候选以确定服务端注册的 Family/origin adapter；完成所需的事务外准备；由 adapter 从事务开始决定完整锁顺序并锁定候选 expected_version；核对目标基准；复核证据及当前 active 状态；核对 schema、评测记录和 policy；调用事务内 Family writer；原子保存正式结果和审核状态。通用层不得先锁 Candidate 再交给 adapter 补锁其他资源；预读的候选版本、origin 和内容必须在事务内重新校验。

同一 Family 的 HTTP、SDK 及其他审核入口共用相同的事务与锁顺序。Profile 保持 Policy → Candidate → Source Cursor（仅 Source-window 候选）→ Profile head 校验与条件写入；Dream Profile 不读取或推进 Source Cursor。approve/revise/reject 中涉及相同资源的操作也必须遵守该顺序，不允许某一入口改成 Candidate → Policy。其他 Family 按既有写入流程确定锁顺序，并通过并发验收验证。

发生冲突或校验失败时，候选保留 pending 并返回可操作原因，不自动 rebase。拒绝 Dream 候选不会修改 Artifact/Tag，也不会推进普通 Source cursor；既有 Source-window 候选仍按原有规则处理游标。修订不能换 target，目标变化需要新候选。候选正文修改会产生新 version，旧版本和其评测记录继续可追溯。

## 12. 持久化、索引与后台执行
A0/A1 复用 `pc_dream_runs`、`pc_artifact_candidate_heads`、`pc_artifact_candidate_versions` 和各 Family 的既有存储，不新增业务表。operation spec version、类型化 target、result reference 及 proposal/origin 优先使用已有类型化 payload；只有查询、唯一约束或事务校验确实需要时才增加必要列和索引。Dream 的数据库唯一约束和请求查询沿用 `(scope_id, principal_key, idempotency_key)`，principal_key 沿用既有请求者身份编码。

可信评测记录存储归属 B1，记录主体、候选 digest、评测集版本、policy 版本、结果与用量，不复制用户凭据或完整训练集；仅在无法用既有可信存储满足不可伪造记录与版本绑定时新增专用表。Tag 候选及版本存储归属 C，以其独立结果类型和 ETag 事务语义论证所需新表，不复用 Artifact head 表。两者的接口、表结构与迁移均随所属阶段交付，不作为 A0/A1 的启动、迁移或能力启用前置条件。

所有 schema 变化同时覆盖 SQLite 与 OceanBase。新增 Profile generation mode 和 Candidate proposal 在对应 operation 启用前，必须使持久化 reader、HTTP serializer、Client 和 Dashboard 同步识别。上线后不理解新增封闭类型的旧 Client 应收到明确的升级提示或使用兼容过滤，不得伪造旧 mode、空 Source window 或成功的空结果。

Supervisor 保持一个 processing family 一个 canonical binding，不为 Dream 另建常驻 scheduler。Memory、Profile、Topic、Experience、Skill 复用各自资源归属；Handoff、Prompt 等尚无完整后台 Dream 能力的 Family，须补齐规范 binding、Worker 配置与能力声明后才能启用。Tag 作业路由到其 owning Artifact family binding，并在注册表区分 operation，不能为 `tag` 伪造 Family。

同一 binding 内显式 Run 按固定顺序执行。为避免普通 Source 处理饥饿，新增轮转策略：最多连续处理 4 个显式 Run 后，若存在可执行的 Source 工作则执行一次 Source pass，再继续 Dream。计数通过现有调用状态表的 `consecutive_dream_attempts` 列持久化，不新增表，不因 Worker 重启重置；启动先补齐该增量列，再执行 processing schema 完整性检查，旧数据库保留已有待处理请求；Source pass 完成时归零，并保留尚待处理的 Dream 请求。没有 Source 工作时不人为阻塞 Dream；现有 v1 Run 的语义与预算保持。

Run 的候选落库、结果记录、租约 fence 检查、调用确认和后继调度意图在同一事务提交。Family 审核复用各自已有的投影发布契约，不统一引入异步 outbox；Topic Memory 按第 6 节在事务外准备完整投影，在批准事务内原子发布。只有既有 Family 已采用异步投影时才沿用其 processing intent，不为本次扩展新增通用索引任务表。审批等待不占 Dream Worker。查询只读取，不因 prepare_context 或 search 自动启动 Dream。

## 13. 预算与重复提案抑制
第一阶段沿用现有默认 Run 上限：20 项显式目标/制品/条目引用，包含补充 Source 后至多 32 项模型可见证据，64 KiB 总证据投影，每次最多 4096 输出 token，最多 2 次模型调用，首次执行起 120 秒；同 Scope 最多 32 个未完成 Run。来源遍历沿用 128 节点、256 边、8 层上限。

完整快照必须在预算内，超限失败并提示缩小目标或证据，不能截断后声称完整修订。部署者可收紧预算，调用方不能放宽。

idempotency_key 按第 2 节包含请求者身份的唯一范围处理相同请求重放。额外的 proposal fingerprint 在 `(scope_id, principal_id)` 内使用 operation、完整 target baseline（含选中条目及其版本）、实际根证据摘要和 policy digest，防止同一请求者对同一目标和证据生成多个未审候选；首期不跨请求者复用 Candidate，也不转移候选归属或后台执行身份。命中 pending 候选时须重新核对当前候选版本、内容、证据与当前请求的匹配关系及可读权限，再关联精确 candidate_id/version 并记录 reuse；已被修订而不再匹配的候选不能沿用旧 fingerprint。命中已拒绝且证据未变的提案返回 no_change，并在具备读取权限时提供现有候选引用。新反例、目标版本或 policy 变化可以重新提案。抑制依据可解释，不把“模型换种措辞”当作新增价值。

## 14. 错误、可观测性与安全
| 情况 | 可观察行为 |
| --- | --- |
| operation 未注册、输入 family 不合法、仅 Source 无合法目标 | 422，稳定原因码 |
| capability 未就绪 | 503，不创建运行 |
| 目标已不是 current head | 409 target_conflict |
| 标签 ETag 过期 | 412 tag_precondition_failed |
| 引用越权或不可见 | 沿用既有 403/404 策略，不泄漏正文 |
| 生成期间目标改变、证据撤回 | Run failed，无候选或正式写入 |
| 内容太大、预算耗尽 | Run failed，明确 evidence_limit_exceeded/budget_exceeded |
| 模型输出伪造引用、错误类型或目标外变更 | failed: invalid_generation_output |
| 审批缺少有效评测 | 409 evaluation_required/stale_evaluation，保留 pending |
| policy 改变或候选 expected_version 不匹配 | 409，要求重新检查 |
| Topic 投影准备失败或准备期间候选、head、检索配置改变 | 批准失败，Candidate 保持 pending，正式 head 和检索投影均不变；重新准备后再审核 |


记录 operation/family、等待与执行时长、模型调用和 token、三类结果、失败原因、候选复用、审批冲突和投影准备/发布耗时。Metrics 标签使用低基数维度，不放正文、用户 ID、Source ID 或标签原文。Trace 默认不输出 Prompt、Memory、Profile 和原始聊天内容；受权限控制的精确读取用于调查。

量化效果要记录后续任务是否实际获得并使用了对应版本。仅有召回、没有注入记录时标记 unknown；仅有任务成功、没有相关核验时不能归因于 Dream。不得因为一份用户画像被频繁使用，就把它判为真实或有效。

## 15. 实现阶段与验收
| 阶段 | 交付 | 启用条件 |
| --- | --- | --- |
| A0 | Operation registry、现有 v1 契约扩展、证据角色、typed target、共用审核扩展；复用现有 Run/Candidate 表 | Client 与 Server 协调升级，旧操作回归、请求者幂等隔离与统一锁顺序通过，未注册操作关闭 |
| A1 | Memory、Profile、Topic Memory 显式复盘；不依赖 Tag 或可信评测存储 | 条目原子提交、Profile 游标隔离、Topic 完整投影原子发布及启动一致性通过 |
| B1 | Handoff 刷新、仅含 SKILL.md 的纯指令 Skill 包修订、可信评测接口与存储 | 激活边界、包内容准入与评测门槛通过，真实任务端到端验证完成 |
| B2 | Prompt 改进 | 同 key schema、保留测试集、配置发布权限及回滚通过 |
| C | Tag 单 target 分类治理、独立候选接口与存储、统一 Inbox | ETag/正文双基准、独立结果类型、历史记录通过，新表必要性明确 |


代码分工：`builtin/dream` 承载 operation spec 和运行编排；`builtin/evidence` 增加严格 Family resolver；`builtin/review` 和 Family adapter 完成候选及事务提交；`builtin/persistence`、SQLite/OceanBase 实现记录与 CAS；`builtin/runtime` 接入 Supervisor 和投影更新。`openapi/powercontext.yaml` 是 HTTP 源契约，更新后运行 `make api-generate` 和 `make contract-test`，禁止手写 `_generated` 代码。

测试验证公开行为和实际事务，不固化内部函数调用次数。新增公共契约、持久化与并发逻辑需完成相关单元/集成测试、`make check`、`make test`；涉及支持的 Python 版本时执行 tox。重要验收矩阵：

| 场景 | 必须观察到的结果 |
| --- | --- |
| 出差 S02 与明确搬家 S03 | 前者不改长期画像，后者生成可追溯修订 |
| 两个用户的相似聊天 | 不跨主体归并或生成画像 |
| 只修订一个 Memory 条目 | 未选中条目保持不变，旧 citation 仍指向旧内容 |
| Memory 在生成/审核时被修订或停用 | CAS 或证据校验失败，不覆盖、不复活 |
| Profile Dream 与 Source-window 候选并发 | 只有当前基准可批准，Dream 不推进游标或清除 pending 指针 |
| Topic Dream 与 fusion 并发 | 只对当前 head 原子发布完整投影；竞争失败不改变正式状态 |
| Topic embedding 失败、准备期间候选被修订、发布事务失败 | Candidate 保持 pending，旧 head 与完整投影继续可用，无部分发布 |
| Topic 批准提交前后服务重启 | 读取到完整旧版本或完整新版本，既有启动一致性检查通过 |
| Handoff 只有计划，没有执行结果 | 不把计划改成完成，不产生 accepted |
| Skill 仅含 SKILL.md 的标准包（含现有纯指令生成结果） | 可以生成修订候选，审核通过后提交新的合法标准包 |
| Skill 包含额外文件 | 明确拒绝，不能悄悄删附件、脚本或依赖文件 |
| 候选编辑后复用旧评测 | 审核拒绝，要求重测 |
| 普通调用方提交自述 passed=true | 不能获得可信评测通过记录 |
| Prompt 保留测试集进入 demonstrations | 隔离检查失败，不能报告有效改进 |
| Prompt 发布与回滚 | 新推理使用新 head，在途推理固定旧版本，回滚产生更高 Revision |
| Tag 标签未变但正文已变 | 正文基准冲突，不按旧内容修改分类 |
| Tag 审核成功 | 只改变标签集，不创建 Artifact Revision |
| 同根 Source 被多次转述 | 不增加独立证据数量 |
| 相同幂等请求、响应丢失、Worker 接管 | 同一请求者和协议版本内至多一个正式候选/结果，旧任期不能提交 |
| 同 Scope 不同请求者使用相同 idempotency_key 或相同证据 | 不误报幂等冲突，不复用对方 Run/Candidate，归属与执行身份各自保持 |
| HTTP/SDK 并发审核同一 Source-window Profile 候选 | 各入口使用一致的 Policy 优先锁顺序，按版本条件完成或返回冲突，不产生反向锁等待 |
| A0/A1 部署未配置 B1/C 存储与接口 | 可以启动并启用已完成的首期操作，后续阶段能力保持关闭 |
| 缺证据、无变化、执行失败 | 三者可区分，不产生虚构正式制品 |
| 连续 Dream 请求与普通 Source 工作 | 两类工作均取得进展，普通游标仅由自身流程推进 |
| 旧 Client 与扩展后的 v1 Server | 未升级时收到明确升级提示或只读取其可解析类型；升级后可读取新增 Run 与 Candidate |


SQLite 与 OceanBase 都执行并发冲突、租约隔离和原子失败恢复验收。使用隔离数据完成一条真实链路：原始会话写入、生成初始制品、追加纠正证据、Dream 提案、人工审核、后续任务使用、结果回查。模型质量评测使用时间隔离的后续任务，对比“无 Dream”“普通重新生成”“本提案”，报告错误更新率、遗漏率、审核修改量、后续任务质量、延迟与总成本，不预设提升百分比。

# Drawbacks
+ 每种 Family 都需要专门 writer 和校验，统一入口不能消除条目、游标、激活和配置发布的语义差异。
+ 全量 Memory head CAS 在活跃 Scope 中可能频繁冲突；采用更细粒度 CAS 会增加 manifest 合并复杂度，本期选择可解释的保守冲突。
+ 完整 Profile/Topic 快照增加上下文和输出成本，预算可能使大型目标暂不可处理。
+ 人工审核与 Skill/Prompt 评测增加等待和运营负担；错误的评测集仍可能选出错误配置。
+ 扩展现有 v1 封闭枚举与 Profile generation 需要协调升级 Client；静默伪装成旧类型会更危险。
+ Tag 的独立候选实现增加代码，但可避免把可变分类与不可变 Artifact Revision 混成一种生命周期。

# Rationale and alternatives
| 方案 | 优点 | 不采用的原因 |
| --- | --- | --- |
| 所有制品直接调用通用 replace | 改动小 | 绕过 Memory manifest、Profile 游标、Handoff 激活及 Tag ETag |
| 所有复盘结果都先转成 Experience | 可复用当前 Dream | 无法实际纠正画像、主题和操作配置，会留下错误的正式状态 |
| 每个 Family 单独实现一套 Dream 服务 | 局部实现直观 | 幂等、预算、证据与故障恢复重复，难以保持一致 |
| 统一外层 Run，内部注册 Family adapter | 共用运行能力，保留语义边界 | 采用，代价是显式注册与类型化协议 |
| 定时全量扫描、生成后自动覆盖 | 操作少 | 审核负担、成本和错误扩散难控制，尚无收益依据 |
| 一次改完 Memory、Profile、Topic、Tag | 表面一致 | 多目标事务与补偿复杂，本期只允许一个目标，协调另行定义 |
| Tag 强行创建 Artifact family | 可复用 result_artifact | 破坏已有 catalog 属性语义，产生无意义内容 Revision |


若不实施，仍可通过现有管理 API 人工修订，但证据收集、运行恢复、候选比较和权限复核分散在应用侧，无法形成一致的受控复盘流程。

# Prior art
本提案直接沿用 PowerContext 自身的设计，不以其他产品的宣传能力作为已验证依据。

+ [RFC 1510](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1510-artifact-dreaming.md)：精确证据、单候选、后台预算、根来源去重。
+ [RFC 0014](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/0014_memory_layer_design.md)：Memory 条目修订需显式证据，forget 独立授权。
+ [RFC 1485](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1485_profile_artifact.md)：Scope Profile 快照、Policy 与 Source-window 审核。
+ [RFC 1417](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1417_topic_memory.md)：主题生成、融合与渐进检索。
+ [RFC 0048](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/0048_handoff_artifact.md) 与 [1396](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1396_handoff_access_control.md)：交接及权限边界。
+ [RFC 1468](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1468_scope_owned_prompt_management.md) 与 [1467](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1467_artifact_tags.md)：版本化配置与可变标签的不同语义。
+ [RFC 1557](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1557_recurring_failure_repair.md)：区分被选中、失败复发与实际效果的提案，不能从缺失观测作归因。该 RFC 的存在不代表其能力已实现。

本次现状核对的关键源码：`builtin/dream/models.py`、`builtin/dream/service.py`、`builtin/dream/bindings.py`、`builtin/review/models.py`、`builtin/review/service.py`、`builtin/artifacts/profile/models.py`、`builtin/artifacts/profile/review.py`、`builtin/artifacts/memory/models.py`、`builtin/artifacts/topic_memory/models.py` 和 `builtin/tags.py`，均位于 `src/powercontext/` 下。

# Unresolved questions
下列问题应在对应阶段启用前由维护者决策，不能委托模型临时决定：

1. 扩展现有 v1 后的最低支持 Client 版本，以及旧 Client 的升级提示或兼容过滤策略。
2. Skill 与各 Prompt key 的首组保留评测集、版本化质量门槛和评测责任人。本文规定必须有可信门槛，具体业务分数需实测确定。
3. 现有 Handoff 非激活提交能力应如何从 prepare/commit 代码提取，确保与现有 token 和 receiver checks 完全一致。
4. Catalog Change Candidate 的统一 Inbox 分页是否由服务端合并游标实现，还是先通过类型筛选页提供；无论 UI 方案如何，底层生命周期不混用。
5. 发布后质量异常由哪个应用监控并发起显式回滚；本 RFC 不承诺自动因果归因或无人值守回滚。

这些问题不阻塞 A0/A1 的独立实现；涉及后续阶段的能力必须在问题解决、契约测试通过后才对外宣告可用。

# Future possibilities
+ 基于新增根证据、反例和纠正的周期性 discovery，设置静默期、候选去重和待审容量限制。
+ Memory entry 合并、明确停用与恢复，Topic 拆分/合并，以及可解释的多目标原子变更组。
+ 完整 Skill package 的静态差异、隔离测试和制品签名，保持执行与发布权限独立。
+ 跨 Scope 的脱敏共享经验与独立授权读取，不从内容相似推导访问权。
+ 对低风险确定性变更研究自动批准策略，但应另立 RFC，明确可逆性、质量阈值、审计和撤回机制。
+ 用受控实验验证不同 Dream 策略对任务质量和成本的实际影响，再决定是否扩大自动化范围。
