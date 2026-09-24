- Proposal Name: `multi_artifact_dreaming`
- Start Date: 2026-09-22
- Related RFCs: [Artifact Dreaming（1510）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1510-artifact-dreaming.md)、[Candidate 与 Review Inbox（0050）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/0050_artifact_candidate_review_inbox.md)、[Profile（1485）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1485_profile_artifact.md)、[Topic Memory（1417）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1417_topic_memory.md)、[Prompt（1468）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1468_scope_owned_prompt_management.md)、[Tag（1467）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1467_artifact_tags.md)、[Processing Supervisor（1515）](https://github.com/oceanbase/powercontext/blob/master/docs/zh/rfcs/1515_artifact_processing_supervisor.md)

# Summary
将 Dream 从现有的 Experience 提炼与 Skill 派生，扩展为按制品生命周期执行的受控复盘能力。新增 Memory 条目修订、Profile 画像校正、Topic Memory 主题校正、Handoff 交接刷新、现有 Skill 修订和 Prompt 改进，并为 Tag 提供独立的分类变更提案。所有操作沿用“精确证据、后台运行、待审候选、条件提交、完整来源”的原则，一次运行只处理一个目标，不直接覆盖正式状态。所有新增 Dream 操作都必须先写入待审核 Candidate；只有 Reviewer 批准当前候选版本并通过目标当前基准校验后，变更才能生效：Artifact 由所属 Family 的写入器提交新 Revision、推进 head，Tag 按其 ETag 条件替换标签集。Dream 生成成功或候选入库本身不改变正式状态。

Dream 负责提出可解释的变化，Family adapter 负责语义校验和提交，Reviewer 负责批准，应用负责后续实际业务验证。Source 保持原始证据身份，不被 Dream 改写；Tag 保持 catalog 属性身份，不伪装成 Artifact。第一阶段交付 Memory、Profile、Topic Memory，后续阶段扩展 Handoff、Skill、Prompt 和 Tag。自动发现、全量夜间扫描、多目标原子合并及无人值守发布不作为本提案的交付前提。

本 PR 的所有扩展操作统一沿用 Experience 提炼、Skill 派生的证据驱动提案与人工审核逻辑。可信评测为后续可选增强，本 PR 不实现相关服务、接口、存储或审批门槛，也不以其配置作为能力启用条件。

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
| Prompt 是运行配置，正式 head 会影响后续推理选择 | 发布不是纯内容编辑，需要确定性校验、人工审核、明确权限及回滚 |
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
| `revise_skill`，新增 | 当前 managed Skill 的替换候选 | 使用反例、步骤、前置条件与检查方法 | 通过内容校验和人工审核后发布，安装执行仍独立 |
| `revise_prompt`，新增 | 已注册 Prompt key 的完整配置候选 | instructions、demonstrations 的失败模式 | 内容校验、人工审核与配置写权限通过后发布，后续推理解析新 head |
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
| effect | 审核后的实际影响 |

注册表是服务端固定配置，不接收请求内 Python 路径、任意工具名、SQL 或自定义执行代码。Family registry 可发现读取能力，不等于已经支持 Dream。只有解析、生成、审核、提交和后台执行能力全部就绪时，operation 才能对外启用。

现有 `DreamService.execute()` 按 Skill 与 Experience 二分 operation，需要改成按所属 binding 查询它支持的 operation 集合，再按 accepted_at、run_id 稳定排序领取一个 Run。不得通过“非 Skill 就当 Experience”的默认分支执行新增操作。

## 2. 协议与升级边界
Candidate 统一表示待审核的变更提案。现有候选接口去掉 artifact 命名，直接替换为 `/v1/candidates/*`，通过 `candidate_kind=artifact|tag` 区分提案和结果；不新增平行的 v2 Dream/Review 路径。

| 接口 | 契约 |
| --- | --- |
| `POST /v1/candidates/list` | 按 Scope 分页查询，支持 status、family、candidate_kind 筛选；共用排序与游标 |
| `POST /v1/candidates/get` | 根据 scope_id、candidate_id 返回当前候选、类型、版本、证据和审核结果 |
| `POST /v1/candidates/history` | 查询两种候选的不可变提案版本历史，不把最终审核状态伪装成历史提案内容 |
| `POST /v1/candidates/revise` | 携带 expected_version 和类型化 proposal 修订提案，产生新版本；类型、目标不可修改 |
| `POST /v1/candidates/approve` | 携带 expected_version 审核精确版本，按类型执行条件提交 |
| `POST /v1/candidates/reject` | 携带 expected_version、reason 拒绝精确版本 |
| `POST/GET /v1/scopes/{scope_id}/dream`、`GET /v1/scopes/{scope_id}/dream/{run_id}` | 复用提交、列表和详情入口，返回统一 Candidate 引用 |
| `GET /v1/capabilities` | 声明 Dream 操作、可用性、输出种类与生效方式 |

所有单候选操作使用 scope_id、candidate_id；服务端从持久化候选读取 candidate_kind，不能信任调用方切换类型。list 未指定 candidate_kind 时返回两类候选，并沿用既有权限过滤；family 是目标所属 Artifact family，不新增 family=tag。Experience、Skill、外部技能导入、Profile 和 Dream 等已有业务入口继续负责创建候选，不新增通用 create 接口。

直接移除 `/v1/artifact-candidates/*`，不保留别名、重定向或兼容过滤。本 PR 的 `/v1/catalog-change-candidates/*` 及 `/v1/catalog-candidates/*` 尚未发布，不提供这些入口或其兼容层。接口替换是显式破坏性变更，调用方必须与 Server 协调升级；旧接口不再可用。已有数据的自动迁移不等于旧 Client 兼容。

`openapi/powercontext.yaml` 是源契约。交付时同步更新模型和 operationId，重新生成服务端代码及 Client，并更新 Python SDK、CLI、MCP、集成插件、Dashboard、示例和契约测试。官网的中英文 HTTP API、接口总览、候选审核指南及由 OpenAPI 生成的 API reference 同步更新；生成目录不手工修改。发布说明须明确旧路径移除及最低匹配 Client 版本。可信评测的注册、存储与审批门槛不在本 PR 范围内。

新增操作请求复用 `operation`、`artifacts`、`memory_citations`、`sources` 和 `idempotency_key`。Artifact 操作的 `target` 保持精确 ArtifactRef，并必须同时出现在 `artifacts` 中；Memory 的目标条目及版本通过 `memory_citations` 指定，必须属于该目标 Memory。引用去重后计入统一预算；目标内容不能单独证明自身断言。Profile、Topic Memory 和 Handoff 的待修订目标仅按权限、存在性与精确 head 校验；其历史引用失效不阻止以新的有效证据纠正目标。本次选中的支持证据仍须递归校验，不能通过目标角色豁免。Tag 在相同 Dream 提交接口中通过 tag_target 携带 TagTarget、expected_etag 和正文基准；Artifact 操作继续使用精确 ArtifactRef target。

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

## 8. Skill 修订与审核契约
`derive_skill` 保持原语义，`revise_skill` 明确指向一个已有 managed Skill。首期支持 instruction-only Skill，按包内实际文件判断支持范围，不以 package 字段是否存在判断。现有纯指令内容也会规范化为仅含根目录 SKILL.md 的标准包；这种单文件包属于支持范围。对于尚无 package 的旧纯指令内容，沿用既有规范化流程生成单文件标准包。

服务端读取目标精确 package digest 对应的包快照，只有包内仅含根目录 SKILL.md 时才允许本期修订；包含额外附件、脚本或依赖文件的包返回明确的 unsupported_target，不默默丢弃文件。修订保留未授权改变的 frontmatter 和元数据，生成新的标准包，经过既有包校验与人工审核后提交精确 package 引用；不得只修改缓存的 instructions 字段而保留旧 package 引用。多文件包的路径、签名和资产替换另行设计。

输入需要与目标版本关联的任务结果、使用反馈或已批准 Experience。输出保留适用范围、前置条件、指令与验证要求，不推断“被选中”就是“被执行”或“有效”。负面用例不删除，不以新增步骤数作为质量指标。

所有本 PR 扩展的 Dream 能力（Memory、Profile、Topic Memory、Handoff、Skill、Prompt、Tag）统一沿用 Experience 提炼与 Skill 派生的逻辑：基于精确证据由模型生成改进提案，程序执行结构、来源、权限和版本等确定性校验，先形成 pending Candidate，再由人工审核批准后按所属资源的条件提交规则生效。Tag 使用统一 Candidate 的 tag 类型，保留 ETag 和正文双基准约束。

确定性 schema、来源和禁止越权检查必须全部通过；Reviewer 修改实质内容或证据后产生新候选版本，重新校验并审核。未接入可信评测服务、没有评测集或评分阈值，不阻塞本 PR 任一扩展操作的实现、启用或批准。批准不安装、不执行、不自动分发 Skill，原有显式使用路径保持。模型给出的改进理由、validation 文本和人工批准均不代表已证明质量提升。

## 9. Prompt 改进与生效契约
仅支持已注册且允许自定义的 Prompt key，例如 `memory.extract`。输入包含当前精确 Prompt、已核验的错误输出及原始 Source，以及有依据的期望输出或纠正反馈。错误输出作为被诊断对象，不作为事实来源；expected output 需要人工标注或其他可信来源。

候选只修改现有 `mode/instructions/demonstrations`，所有 demonstration 按该 Prompt Definition 的输入输出 schema 校验。首期输出固定为 custom 模式，不修改 Prompt key、模型配置、trust rules、工具集合或资源上限。

Prompt 与其他扩展操作采用相同的证据提案、确定性校验和人工审核流程。保留目标 key 的 schema、证据来源、权限、版本与 trust rules 校验；本 PR 不实现保留测试集登记、baseline/candidate 自动评分或可信评测准入。缺少合理改进依据时返回 no_change 或 needs_evidence，不因模型自述通过测试而宣称有效改进。

当前 Prompt head 在后续 inference 中解析，因此批准该候选就是配置发布。审批 UI 和响应必须明确显示这一影响，Reviewer 同时需要 review authority 与现有 Prompt 写权限。先保存一个“已批准但不生效”的 head 会与现有语义冲突，本期不增加该中间状态。

在途推理继续使用已冻结的旧 Prompt Revision；发布后启动的新推理解析新 head。回滚读取旧内容，再写成新的单调递增 Revision，不回退版本号。Dream 自身的服务端提示、Review 校验及评测评分器不能成为 `revise_prompt` 的目标，避免修改自身审核标准。

## 10. Tag 变更契约
Tag 复用统一 Candidate 接口如下。请求通过 `scope_id` 指定 Scope；单候选操作通过 `candidate_id` 指定候选，修改、批准和拒绝还须携带 `expected_version`。

| 接口 | 用途 |
| --- | --- |
| `POST /v1/candidates/list` | 分页查询候选 |
| `POST /v1/candidates/get` | 获取候选详情 |
| `POST /v1/candidates/history` | 获取候选提案的版本历史 |
| `POST /v1/candidates/revise` | 修改标签提案，生成新的待审候选版本 |
| `POST /v1/candidates/approve` | 批准当前候选版本，通过标签 ETag 与正文基准校验后替换标签集 |
| `POST /v1/candidates/reject` | 拒绝当前候选版本，不修改正式标签集 |

### 调用流程与请求参数
候选由现有 Dream 创建接口产生，不单独提供 create candidate 接口。调用方先读取目标当前标签及 ETag，再读取目标当前正文版本；随后提交 `revise_tags` Dream。Artifact 标签使用 `basis_ref` 固定正文版本，Memory 条目标签使用 `basis_citation` 固定条目版本。`target` 与 basis 必须指向同一目标，另须提供至少一项支持证据（Source、Artifact 或 Memory citation）；单次最多选择 32 项证据。

Artifact 标签目标示例：

```json
{
  "operation": "revise_tags",
  "tag_target": {
    "target": {"type": "artifact", "family": "experience", "artifact_id": "exp-17"},
    "expected_etag": "<从当前标签读取响应取得>",
    "basis_ref": {"family": "experience", "artifact_id": "exp-17", "revision": 4}
  },
  "sources": [{"source_type": "content", "source_id": "src-42"}],
  "idempotency_key": "dream-tags-exp-17-001"
}
```

Memory 条目目标将 `target` 替换为 `{"type":"memory_entry","family":"memory","artifact_id":"mem-3","entry_id":"entry-8"}`，并用 `basis_citation` 提供相同 `memory_ref`、`entry_id` 和精确 `entry_version_id`。Dream 返回的 `candidate` 引用给后续审核操作使用。

下表列出审核 API 的请求参数。所有接口均为 JSON POST；读取接口要求 `scope.read`，修订、批准和拒绝要求 `scope.review`。

| 接口 | 必填参数 | 可选参数 / 结果 |
| --- | --- | --- |
| `POST /v1/candidates/list` | `scope_id` | `candidate_kind=tag`、`status`（pending/approved/rejected）、`cursor`、`limit`（1–100，默认 50）；返回 `candidates` 与 `next_cursor` |
| `POST /v1/candidates/get` | `scope_id`、`candidate_id` | 返回当前候选版本及其提案、证据和状态 |
| `POST /v1/candidates/history` | `scope_id`、`candidate_id` | 返回该候选的不可变版本列表 `versions` |
| `POST /v1/candidates/revise` | `scope_id`、`candidate_id`、`expected_version`、`proposal`、`reason` | `sources`、`artifacts`、`memory_citations`；成功后生成新的 pending 版本 |
| `POST /v1/candidates/approve` | `scope_id`、`candidate_id`、`expected_version` | 通过候选版本、标签 ETag 和正文基准校验后替换标签集 |
| `POST /v1/candidates/reject` | `scope_id`、`candidate_id`、`expected_version`、`reason` | 拒绝当前版本，不更改正式标签 |

修订提交完整的类型化 proposal；服务端验证 target、expected_etag、basis 和 before_tags 与候选原始基准一致，不允许借修订绕过冲突。`after_tags` 是完整的目标标签集合（0–32 个标签，每个 1–64 个字符）；空数组表示清空标签。标签按 NFC 与 casefold 规则规范化，规范化后重复的标签会被拒绝。`expected_version` 是候选版本的并发保护值，旧版本不能被修订、批准或拒绝。

修订候选的请求示例：

```json
{
  "scope_id": "scope-1",
  "candidate_id": "cat-candidate-9",
  "expected_version": 1,
  "proposal": {
    "target": {"type": "artifact", "family": "experience", "artifact_id": "exp-17"},
    "expected_etag": "<候选保存的原始 ETag>",
    "basis_ref": {"family": "experience", "artifact_id": "exp-17", "revision": 4},
    "before_tags": ["物流", "门卫代收"],
    "after_tags": ["物流", "投递错误"]
  },
  "reason": "核验记录确认签收地址错误",
  "sources": [{"source_type": "content", "source_id": "src-43"}]
}
```

批准请求仅需提供 `scope_id`、`candidate_id` 和当前 `expected_version`；拒绝请求还需提供 `reason`。若候选版本已变化，返回 409；若标签 ETag 已变化，返回 412；若目标正文版本与提案基准不一致，返回 409，均需重新读取并形成新提案，不自动 rebase。

候选响应包含 `candidate_kind=tag`、`candidate_id`、`version`、`status`、`operation=revise_tags`、`origin`、`proposal`、证据、`reason` 和审核结果。`proposal` 保存 `target`、`expected_etag`、`basis_ref` 或 `basis_citation`，以及完整的 `before_tags`/`after_tags`。批准后的 `result` 返回实际标签集合及新的 `tag_digest`/ETag；不会生成 `result_artifact` 或 Artifact Revision。

Tag 不是 Artifact Family。统一 Candidate 使用 `candidate_kind=tag`，复用两张候选表、版本历史、状态流转和审核接口，保留 Tag 专用 proposal/result schema 与提交 adapter。状态为 pending/approved/rejected；批准结果为 target、完整标签集及新 tag_digest/ETag，不填充 result_artifact。

每次仅更新一个现有 TagTarget，保存 expected_etag、basis_ref/basis_citation、完整 before/after tags、证据和理由。沿用现有 NFC/casefold 规范化、数量及长度限制。标签增删都需要审核，不能给 target 以外的资源自动迁移标签。

审批时同时核对标签 ETag 与正文基准。正文 head 或条目版本改变，即使 ETag 未变也要求重新提案，防止按旧内容打标。按既有权限读取 basis；ETag 冲突返回 412，正文基准冲突返回 409。事务内替换标签集合并标记候选 approved，Artifact Revision、digest、embedding 均不改变。

统一 Inbox 直接查询同一候选存储，使用稳定排序和同一游标；不创建 family=tag，响应按 candidate_kind 区分 Artifact 与 Tag 结果。首期不批量改名、不定义全局别名、不检查或修改外部系统自动化。涉及业务规则的标签需要业务方确认其影响，PowerContext 标签本身始终不授予权限。

## 11. 候选模型与审批事务
对新增 Artifact family 注册专门 proposal 类型和审核 adapter，复用已有候选版本、pending/approved/rejected 及 result_artifact 语义。所有 Dream-origin Artifact 变更，无论该 Family 的既有自动生成是否需要审核，都只能先进入 pending Candidate；批准时重新核对候选版本、证据、权限和目标当前 head，条件提交新 Revision 并原子标记 approved。审核前以及冲突时不得让 Dream 结果进入正式 head 或检索生效状态。既有非 Dream 自动生成路径保持原有策略，不以 Family 级开关统一改变其行为。MemoryCitation 从“仅 Experience”改为逐 operation/family 的白名单校验，而不是无条件放开。

新增 Candidate 保存 origin、operation、精确 target、proposal digest、evidence manifest 引用、validation policy digest；本 PR 不增加 evaluation refs。需要模型生成的只有内容计划、已给定 evidence ID 及说明，目标身份、CAS token、事务结果和 generation 元数据均由服务端填写。

批准已完成的同一候选重试时，验证权限和请求语义后返回已保存结果，不重复提交正文或标签。

审批共同步骤：校验身份及当前权限；读取候选以确定服务端注册的 Family/origin adapter；完成所需的事务外准备；由 adapter 从事务开始决定完整锁顺序并锁定候选 expected_version；核对目标基准；复核证据及当前 active 状态；核对 schema 和适用的确定性 policy；调用事务内 Family writer；原子保存正式结果和审核状态。通用层不得先锁 Candidate 再交给 adapter 补锁其他资源；预读的候选版本、origin 和内容必须在事务内重新校验。

同一 Family 的 HTTP、SDK 及其他审核入口共用相同的事务与锁顺序。Profile 的完整锁顺序包含调度状态：Processing Intent → Policy → Candidate → Source Cursor（仅 Source-window 候选）→ Profile head 校验与条件写入。Dream admission 与 Worker 先锁同一 canonical binding 的 Processing Intent；approve/reject 和 Policy 更新也必须在获取 Policy 前锁定该 Intent，禁止持有 Policy 后再反向申请 Intent。只编辑候选且不访问 Intent 的 revise 从 Policy 开始，保持其余资源的相对顺序。Dream Profile 不读取或推进 Source Cursor。approve/revise/reject 中涉及相同资源的操作也必须遵守该顺序，不允许某一入口改成 Candidate → Policy。其他 Family 按既有写入流程确定锁顺序，并通过并发验收验证。

发生冲突或校验失败时，候选保留 pending 并返回可操作原因，不自动 rebase。拒绝 Dream 候选不会修改 Artifact/Tag，也不会推进普通 Source cursor；既有 Source-window 候选仍按原有规则处理游标。修订不能换 target，目标变化需要新候选。候选正文修改会产生新 version，旧版本及其证据继续可追溯。

## 12. 持久化、自动迁移与后台执行
候选只使用两张业务表：将 `pc_artifact_candidate_heads` 改名为 `pc_candidate_heads`，将 `pc_artifact_candidate_versions` 改名为 `pc_candidate_versions`，在原表上扩展，不新增永久候选表或迁移记录表。不创建 `pc_catalog_change_candidate_heads`、`pc_catalog_change_candidate_versions`。`pc_dream_runs` 与各 Family 的存储继续复用。

| 表 / 字段 | 设计 |
| --- | --- |
| heads.candidate_kind | 非空 artifact/tag，旧候选回填 artifact；创建后不可变，versions 通过候选身份继承类型 |
| heads.version/status/decision_reason | 复用当前版本、pending/approved/rejected 和审核理由 |
| heads.result_family/result_artifact_id/result_revision | 仅 artifact 批准结果使用，保留对真实 Artifact Revision 的外键 |
| heads.result_payload | 新增可空的类型化结果载荷，tag 批准后保存目标、完整标签集及提交后的 ETag |
| versions.proposal | 复用已有载荷；artifact 保存制品提案，tag 保存目标、expected_etag、basis_ref 或 basis_citation、before_tags/after_tags |
| versions 的来源、证据、目标与理由 | 保留精确依据和不可变提案历史，不把 Tag 当作 Artifact family |

批准约束按 candidate_kind 分支：artifact 必须具有真实的 result revision 且无 Tag 结果；tag 必须具有有效 result_payload 且三个 Artifact 结果列均为空。pending/rejected 不保存成功结果，拒绝仍须保存理由。Tag 的正文基准使用真实引用及服务端类型校验，不使用 0、-1 或默认 1 伪造 revision。候选身份仍为 (scope_id, candidate_id)，版本身份仍为 (scope_id, candidate_id, version)。仅为筛选和事务校验增加必要索引。

### 升级时自动处理已有数据
自动迁移属于本功能的必交付实现：安装新包本身不修改数据库；新版 Runtime/Server 首次初始化持久化时，在候选表 create_all、读取候选、开放请求及启动 Worker 之前执行专用 schema migration。仅修改 SQLAlchemy 表名或调用 create_all 不能迁移已有表。

1. 部署先停止旧 Server 和 Worker，备份数据库，再启动新版；不支持新旧二进制混跑或滚动跨越此次 schema/API 变更。部署级互斥保证仅一个迁移执行者，其他新版实例等待迁移完成。
2. 检查旧表、新表、字段、外键和约束：空数据库直接建立新结构；旧结构原位改名并补充字段、回填 candidate_kind=artifact、调整结果约束；已完成结构校验后跳过。原本 nullable 的结果和来源保持原义。
3. 保留所有候选 ID、当前版本、全部历史 proposal、状态、审核理由、批准结果、证据与来源。同步更新 Profile policy 的 pending_candidate_id 外键；保留 Dream run 引用和访问归属。历史载荷通过明确的 reader 规范化或无损转换读取，不能要求用户重新生成或审核已有候选。
4. SQLite 的约束变更可使用事务内临时重建表，完成复制、外键与行级内容校验后替换；最终只保留两张候选表。OceanBase/MySQL 方言的 DDL 不能假定可整体回滚，逐步检查真实 schema、幂等续跑，并在任何中间状态阻止业务启动。
5. 校验行数、候选/版本对应关系、批准结果、Profile 外键与历史引用后才允许服务就绪。失败时保留可恢复数据并输出可操作错误，不能静默跳过、清空旧表或创建空新表后继续服务。旧新表同时存在且无法证明迁移来源时停止，不能猜测覆盖。
6. 本 PR 的独立 Catalog Change 存储尚未发布，不设计其生产兼容或数据合并；若检测到该实验结构，明确提示它不属于支持的发布升级路径，不自动删除。回退旧程序需要恢复升级前备份，不承诺旧程序可以读取新结构。

验收同时覆盖 SQLite 与 OceanBase：空库、旧库 pending/approved/rejected、多个历史版本、Profile 待审指针、Dream 运行引用、访问归属、重复启动、迁移中断重试、多实例启动和异常双表。验证历史已批准结果可读取、旧 pending 候选可继续审核，以及 Tag 批准不生成正文 Revision。

迁移与 API 替换在启用新 operation 前完成。持久化 reader、HTTP serializer、Client、Dashboard 同步识别 candidate_kind；保留原有非 Dream 自动生成策略。operation spec、target、origin、证据等优先使用已有类型化 payload，不新增评测表或预留接口。Dream 唯一约束继续使用 (scope_id, principal_key, idempotency_key)。

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
| policy 改变或候选 expected_version 不匹配 | 409，要求重新检查 |
| Topic 投影准备失败或准备期间候选、head、检索配置改变 | 批准失败，Candidate 保持 pending，正式 head 和检索投影均不变；重新准备后再审核 |

记录 operation/family、等待与执行时长、模型调用和 token、三类结果、失败原因、候选复用、审批冲突和投影准备/发布耗时。Metrics 标签使用低基数维度，不放正文、用户 ID、Source ID 或标签原文。Trace 默认不输出 Prompt、Memory、Profile 和原始聊天内容；受权限控制的精确读取用于调查。

量化效果要记录后续任务是否实际获得并使用了对应版本。仅有召回、没有注入记录时标记 unknown；仅有任务成功、没有相关核验时不能归因于 Dream。不得因为一份用户画像被频繁使用，就把它判为真实或有效。

## 15. 实现阶段与验收
| 阶段 | 交付 | 启用条件 |
| --- | --- | --- |
| A0 | Operation registry、现有 v1 契约扩展、证据角色、typed target、共用审核扩展；复用现有 Run/Candidate 表 | Client 与 Server 协调升级，旧操作回归、请求者幂等隔离与统一锁顺序通过，未注册操作关闭 |
| A1 | Memory、Profile、Topic Memory 显式复盘；不依赖 Tag 或可信评测存储 | 条目原子提交、Profile 游标隔离、Topic 完整投影原子发布及启动一致性通过 |
| B1 | Handoff 刷新、仅含 SKILL.md 的纯指令 Skill 包修订 | 激活边界、包内容准入、证据校验与人工审核链路通过，真实任务端到端验证完成 |
| B2 | Prompt 改进 | 同 key schema、证据校验、人工审核、配置发布权限及回滚通过 |
| C | Tag 单 target 分类治理，复用统一 Candidate 接口、表与 Inbox | ETag/正文双基准、类型化结果、历史记录、幂等批准和权限校验通过 |

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
| 候选编辑后使用旧 expected_version 批准 | 版本冲突；新版本重新完成确定性校验与人工审核 |
| 普通调用方提交自述 passed=true | 不替代证据校验和人工审核，不宣称质量已经提升 |
| Prompt demonstration 不符合目标 key 的 schema | 校验失败，不发布配置 |
| 所有扩展操作均未配置可信评测服务 | 已实现且其他配置就绪时可生成候选并按确定性校验与人工审核批准 |
| Prompt 发布与回滚 | 新推理使用新 head，在途推理固定旧版本，回滚产生更高 Revision |
| Tag 标签未变但正文已变 | 正文基准冲突，不按旧内容修改分类 |
| Tag 审核成功 | 只改变标签集，不创建 Artifact Revision |
| 同根 Source 被多次转述 | 不增加独立证据数量 |
| 相同幂等请求、响应丢失、Worker 接管 | 同一请求者和协议版本内至多一个正式候选/结果，旧任期不能提交 |
| 同 Scope 不同请求者使用相同 idempotency_key 或相同证据 | 不误报幂等冲突，不复用对方 Run/Candidate，归属与执行身份各自保持 |
| Profile Dream admission 与 Source-window approve/reject、Policy 更新并发 | 有界完成，无 Intent/Policy 循环等待；仅普通审批按原逻辑推进 Source Cursor |
| HTTP/SDK 并发审核同一 Source-window Profile 候选 | 各入口使用一致的 Policy 优先锁顺序，按版本条件完成或返回冲突，不产生反向锁等待 |
| A0/A1 部署未配置 B1/C 存储与接口 | 可以启动并启用已完成的首期操作，后续阶段能力保持关闭 |
| 缺证据、无变化、执行失败 | 三者可区分，不产生虚构正式制品 |
| 连续 Dream 请求与普通 Source 工作 | 两类工作均取得进展，普通游标仅由自身流程推进 |
| 旧 Client 与扩展后的 v1 Server | 未升级时收到明确升级提示或只读取其可解析类型；升级后可读取新增 Run 与 Candidate |

SQLite 与 OceanBase 都执行并发冲突、租约隔离和原子失败恢复验收。使用隔离数据完成一条真实链路：原始会话写入、生成初始制品、追加纠正证据、Dream 提案、人工审核、后续任务使用、结果回查。工程测试验证公开行为、约束和事务正确性，不构成产品运行时的可信评测依赖，也不将模型提案或人工批准视为质量提升证明。

# Drawbacks
+ 每种 Family 都需要专门 writer 和校验，统一入口不能消除条目、游标、激活和配置发布的语义差异。
+ 全量 Memory head CAS 在活跃 Scope 中可能频繁冲突；采用更细粒度 CAS 会增加 manifest 合并复杂度，本期选择可解释的保守冲突。
+ 完整 Profile/Topic 快照增加上下文和输出成本，预算可能使大型目标暂不可处理。
+ 人工审核增加等待和运营负担；模型提案与人工批准本身不能证明实际任务质量提升。
+ 扩展现有 v1 封闭枚举与 Profile generation 需要协调升级 Client；静默伪装成旧类型会更危险。
+ 统一候选需要按类型校验结果约束和提交逻辑；表改名及旧路径移除需要协调升级，不能依赖旧 Client 继续工作。

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

1. 确定服务端、Client 和集成的协调发布版本号；旧路径直接移除，自动数据迁移不提供接口兼容。
2. Skill 与各 Prompt key 的证据展示、差异比较及配置发布影响提示应如何呈现给 Reviewer。
3. 现有 Handoff 非激活提交能力应如何从 prepare/commit 代码提取，确保与现有 token 和 receiver checks 完全一致。
4. 统一 Inbox 中 Tag 差异和正文变更的展示方式；分页使用同一存储、排序和游标，不再合并两套结果。
5. 发布后质量异常由哪个应用监控并发起显式回滚；本 RFC 不承诺自动因果归因或无人值守回滚。

这些问题不阻塞 A0/A1 的独立实现；涉及后续阶段的能力必须在问题解决、契约测试通过后才对外宣告可用。

# Future possibilities
+ 可信评测作为后续可选增强，另行定义评测器注册与鉴权、独立保留集、版本化质量门槛、绑定精确候选版本的结果记录及隔离执行。是否接入由后续方案决定；本 PR 不实现，也不阻塞本 PR 的审核发布链路。
+ 基于新增根证据、反例和纠正的周期性 discovery，设置静默期、候选去重和待审容量限制。
+ Memory entry 合并、明确停用与恢复，Topic 拆分/合并，以及可解释的多目标原子变更组。
+ 完整 Skill package 的静态差异、隔离测试和制品签名，保持执行与发布权限独立。
+ 跨 Scope 的脱敏共享经验与独立授权读取，不从内容相似推导访问权。
+ 对低风险确定性变更研究自动批准策略，但应另立 RFC，明确可逆性、质量阈值、审计和撤回机制。
+ 用受控实验验证不同 Dream 策略对任务质量和成本的实际影响，再决定是否扩大自动化范围。
