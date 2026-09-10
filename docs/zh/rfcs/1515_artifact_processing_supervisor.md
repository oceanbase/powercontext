- Proposal Name: `artifact_processing_supervisor`
- Start Date: 2026-09-08
- Status: Proposed
- Amends: [Topic Memory](1417_topic_memory.md) 中的 Artifact Processing Supervisor 调度与恢复契约
- Implementation baseline: [Topic Memory PR #1490](https://github.com/oceanbase/powercontext/pull/1490)
- Related RFCs: [产品定义](0001_product_definition_and_vision.md)、[Core SDK](0002_core_sdk_product_model.md)、
  [本地 Source-to-Memory Runtime](0019_local_source_memory_runtime.md)、
  [Experience 与 Skill](0051_experience_skill_artifact_families.md)、[Candidate 与 Review](0050_artifact_candidate_review_inbox.md)、
  [Profile](1485_profile_artifact.md)、[Handoff](0048_handoff_artifact.md)、
  [Scope 组织](1345_scope_organization_and_agent_integration.md)、[Access Control](1396_handoff_access_control.md)、
  [可观测性](0046_observability_foundations.md)

# Summary

[Topic Memory RFC](1417_topic_memory.md) 已首次定义 `ArtifactProcessingSupervisor`，用于调度 Topic Memory 的
Source-driven 后台处理。本 RFC 不引入另一个 Supervisor；它把既有 Supervisor 扩展为所有具备后台处理能力的
Artifact Family 共用的调度基础，并修订其通用职责。Supervisor 只负责调度：先检查制品类型是否可调度，再发现
该类型下可调度的 Scope，最后把一次 Scope 调用交给对应处理器。输入选择、Source 连续消费、生成、检索、审核、
发布和业务进度均由制品处理器负责。

运行模式只有 `global` 和 `dedicated`。默认 `global` 使用一个逻辑 Supervisor 管理所有制品类型；`dedicated`
为每种制品类型分别组织一个 Supervisor。两种模式都使用相同的处理器接口、持久化协议和逐制品 Worker 预算，
不支持自定义分组或混合拆分。

每种制品独立配置调度策略、Worker 并发额度和单次 Scope 调用超时。内置的 Memory、Topic Memory 和 Experience
使用 interval；现有 Profile 保留 cron 与 timezone。失败的 Scope 释放 Worker 后进入内存退避队列，不影响其他
Scope。已接受的调用意图持久化，业务进度由处理器持久化；恢复不需要 Job 历史或阶段 checkpoint。Topic Memory
已经按原 RFC 合入；本文定义其 Supervisor 的后续修订，以及其他制品迁入后的目标契约。

# Motivation

Memory、Topic Memory、Experience 和 Skill 的业务过程不同，但后台处理都有相同的运行问题：何时检查工作、
如何分配 Worker、如何处理失败、如何在重启和换主后恢复。为每种制品分别维护调度系统，会重复实现这些机制。

Topic Memory PR #1490 已把原 RFC 的可复用调度与 Topic 的 Source-driven processing substrate 一起实现：通用 substrate 读取
Source Cursor、选择 Source Window，并让自动波次的完成时间取决于全部冻结 Scope。旧设计已经允许其他 Scope
并行和 catch-up；但持续失败的目标仍会让旧波次无法完成，使通用调度契约继续依赖 Source Window 和整波状态。
本 RFC 保留其 Pending、Worker、任期、fencing、失败隔离和退避基础，把 Source Window 与业务进度明确留给
处理器，并改为按制品类型准入 Scope。

统一调度也不能变成统一消费流程。Source Window 适合部分 Source 驱动的处理器，却不应成为 Handoff、Skill
导入或未来其他制品接入 Supervisor 的前提。Supervisor 不应为了判断如何派发而调用模型、解析 Source 或读取
完整 Artifact 内容。

共享 Supervisor 需要同时解决两种阻塞：一个制品的长任务不能用光其他制品的 Worker；同一制品的失败 Scope
不能阻止健康 Scope 的后续工作。自动调度不能依赖该制品所有 Scope 全部处理成功。

# Guide-level explanation

## 与 Topic Memory RFC 的关系

本文是 [Topic Memory RFC](1417_topic_memory.md) 的补充与修订，不取代其中的 Topic Memory 产品和处理器设计。
两份 RFC 同时生效时，Topic 的内容模型、Source 顺序、Window、证据、生成、检索、原子发布及 API 继续以 Topic
Memory RFC 为准；Supervisor 的职责、调度单位、资源预算、自动准入和运行模式以本文为准。

Topic Memory RFC 中以下 Supervisor 基础保持不变：

- `ArtifactProcessingSupervisor` 是可供多个 Artifact Family 复用的通用执行层，Topic Memory 只是首个使用者；
- Supervisor 管理选主、队列、公平性、Worker、超时和退避，制品内容及处理逻辑属于 Processor；
- Source 写入与长处理解耦，显式请求先持久化意图再返回；
- 同一处理键 single-flight，不同 Scope 可以并行；
- 自动处理周期按 binding 独立计算，一个繁忙 binding 不重置另一个 binding 的周期；
- 一个 Scope 失败时 Cursor 不推进并重试，其他 Scope 仍可继续；
- Worker 在独立子进程运行，由 Supervisor 管理启动、超时、终止和重试；
- SQLite 使用单进程任期，OceanBase 使用 Lease、Leader 和 fencing；
- 旧任期 Worker 的业务提交必须整体失败，新任期从持久化事实恢复；
- 退避留在 Supervisor 内存，不建立持久化 Job、运行历史或阶段 checkpoint。

上述内容已经由 Topic Memory PR #1490 实现为 `ArtifactProcessingBinding`、`ArtifactProcessingWorkAssignment`、
`pc_artifact_processing_pending`、binding state、auto-wave target 和 global Lease 等实际兼容面。本文的修订必须
通过明确迁移完成，不能把已部署的数据和配置当成尚未落地的草案直接重新解释。

本文把旧 RFC 已有的“按 binding 独立周期”提升为统一的逐制品调度策略，并补充它明确没有实现的多制品能力：
统一处理器注册、先检查制品类型再发现 Scope、逐制品 Worker 额度和超时、`global` 与 `dedicated` 两种整体模式，
以及各制品分批迁移的兼容和验收规则。旧 RFC 的首个使用者只有 Topic Memory，并明确不迁移 Memory、Experience
或 Skill；之后合入的 Profile 仍使用 APScheduler cron，因此也需要纳入本 RFC 的兼容范围。

本文修订下列首版约定：

| Topic Memory RFC 的首版约定 | 本文修订 | 修订原因 |
|---|---|---|
| 所有 binding 共用一个 global Worker pool 和统一超时 | 两种运行模式下都为每种制品设置独立 Worker 额度和超时 | 长任务不能耗尽其他制品的执行能力 |
| 自动波次冻结所有 Pending Scope；虽然失败不阻塞同波其他 Scope 和 catch-up，但全部目标成功后才推进完成时间 | interval/cron 到达时先准入一个制品类型，再有界发现 Scope；扫描完成不等待所有 Worker 成功 | 彻底取消跨 Scope 的波次完成屏障，让失败隔离也适用于后续调度机会 |
| 通用 substrate 以 `source_through`、Cursor 和自动波次目标恢复工作 | 调度层只持久化通用 dirty、调用请求和扫描代次；Source 水位及业务进度留给处理器 | 保留旧 RFC 的可恢复调用意图，同时使非 Source-driven 制品无需伪造 Source 状态 |
| Worker 只能执行一次最终业务事务 | 处理器可以执行一个或多个受 fencing 保护的短事务，并以自身进度保证重试安全 | 不同制品的原子发布边界不同，Supervisor 只能要求幂等和完成确认 |
| 当前固定 `global`，未来可演进为任意命名 group | 仅支持默认 `global`，或所有制品分别使用 Supervisor 的 `dedicated` | 目前没有逐个拆分或混合分组的明确场景 |

这些修订改变的是通用调度契约，不改变 Topic Memory 必须按 Source 顺序处理、失败位置不可跳过、Revision 与
检索投影原子发布等业务保证。Topic Memory 接入修订后的 Supervisor 时，需要按本文的兼容与迁移规则映射已有
Pending、Cursor、flush generation 和自动波次状态。

## 两种整体运行模式

| 模式 | Supervisor 的组织方式 | Worker 预算 |
|---|---|---|
| `global` | 一个逻辑 Supervisor 负责所有已注册的制品类型 | 每种制品独立 |
| `dedicated` | 每种已注册的制品类型使用一个专属 Supervisor | 每种制品独立 |

用户只选择模式。Runtime 自动根据制品注册表组织 Supervisor；用户无需创建分组、给分组命名或逐个绑定制品。
`dedicated` 不承诺独立主机、独立数据库或模型资源隔离。它把调度状态、任期和生命周期按制品分开。

`global` 仍然只有一个 Supervisor、一个选主边界、一条 Lease、一个有效任期和一个调度循环。这个 Supervisor
在循环内维护每种制品各自的到期时间、队列和 Worker 额度；这些是一个 Supervisor 内的调度分区，不会产生额外
Lease。旧 RFC 的 `global` 同样
在一个 Supervisor 内维护多个 binding 的独立周期，只是它们共用 Worker pool 和超时。`dedicated` 才会把选主、
Lease、任期和调度循环都按制品拆开：每种已注册制品对应一个 Supervisor，也对应一条 Lease。

OceanBase 的一个逻辑 Supervisor 可以有多个候选实例，但同一时刻只有一个有效 Leader。SQLite 使用单个
Runtime 宿主进程；`dedicated` 在该进程内组织多个 Supervisor，不增加 SQLite 多进程部署能力。

## 先制品类型，再 Scope

一次调度检查遵循：

~~~text
检查制品类型
  -> 该类型已注册可用处理器，并由当前有效 Supervisor 负责
  -> interval 或 cron 已到期，或存在已接受请求、到期重试
  -> 该类型还有 Worker 额度和队列容量
  -> 有界发现该类型下的可调度 Scope
  -> 将 Scope 交给处理器
~~~

不可调度的类型不会阻塞后面的类型。Supervisor 不先枚举全部 Scope 再为每个 Scope 判断所有制品。
调度策略属于制品类型，不为每个 Scope 配置或维护独立周期。每种类型可关闭自动准入，或注册一种 interval/cron
策略；不能同时启用两种。Supervisor 使用数据库时间计算到期，不把业务处理完成时间当作下一次检查的起点。

例如 Topic Memory 每 5 分钟检查一次，Experience 每 15 分钟检查一次。Topic Memory 的 Worker 全部忙碌时，
Experience 仍可按自己的周期和额度运行。Profile 可以保留每天 `02:00 Asia/Shanghai` 的 cron。调度策略决定何时
检查和准入工作；实际启动时间还受该类型的额度影响。

## 一次调用的含义

调度键沿用 `(binding_name, scope_id)`，不是某个 Artifact ID、Source、Source Window 或模型请求。`binding_name`
是全局唯一但对 Supervisor 不透明的处理器注册标识；它让 `global` Supervisor 能直接选择处理器，并不要求
Supervisor 理解 binding 内部如何处理。当前每种 Artifact Family 只向 Supervisor 注册一个 binding，因此它与
`artifact_family` 一一对应。同一个键在有效任期内最多运行一个 Worker。

这个键只定义一次派发和 single-flight 的身份，不决定有多少个 Supervisor；`global` 和 `dedicated` 使用相同的
调度键。两种模式下，Worker 收到的仍是一次 Scope 处理器调用，而不是由 Supervisor 选好的 Source Window。

处理器收到 Scope 后决定本次处理哪些输入、处理多少内容及如何提交。一次调用可以产生 Artifact、生成待审核
Candidate，也可以确认无需变更。成功返回只表示本次调用选定的工作已经完成，不表示整个 Scope 已无积压，
也不表示 Candidate 已经通过审核。

~~~text
Scope A：A1 成功 -> A2 成功 -> A3 等待下一次可调度机会
Scope B：B1 失败 -> 退避重试 B1；B2 等待 B1 成功
~~~

A3 不等待 B1。对采用顺序消费的制品，处理器必须保留其连续进度，不能先消费 B2 再回来处理 B1。

## 配置示例

~~~dotenv
POWERCONTEXT_SERVER_RUNTIME_ARTIFACT_PROCESSING_SUPERVISOR_MODE=global

POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_SCHEDULE_SECONDS=300
POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_MAX_WORKERS=4
POWERCONTEXT_SERVER_RUNTIME_TOPIC_MEMORY_WORKER_TIMEOUT_SECONDS=600

POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=900
POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_MAX_WORKERS=2
POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_WORKER_TIMEOUT_SECONDS=1800

POWERCONTEXT_SERVER_RUNTIME_PROFILE_SCHEDULE_ENABLED=true
POWERCONTEXT_SERVER_RUNTIME_PROFILE_CRON="0 2 * * *"
POWERCONTEXT_SERVER_RUNTIME_PROFILE_TIMEZONE=Asia/Shanghai
POWERCONTEXT_SERVER_RUNTIME_PROFILE_MAX_WORKERS=4
POWERCONTEXT_SERVER_RUNTIME_PROFILE_WORKER_TIMEOUT_SECONDS=600
~~~

改为 `dedicated` 只改变 Supervisor 的组织方式，不改变这些调度策略、额度和超时。
关闭某类制品的自动调度表示不进行自动准入，仍可接受该制品支持的显式触发，并恢复已接受的调用。

## 所有制品的接入边界

所有 Artifact Family 使用相同的注册和调度契约；是否提供周期处理、如何生成以及如何被消费，由各 Family 定义。

| 类型或能力 | 可以接入的工作 | 必须保持的业务边界 |
|---|---|---|
| Memory | Scope 内的增量提取和维护 | 保留直接 remember；Source 消费进度、去重及检索由 Memory 负责 |
| Topic Memory | Scope 内的主题生成和演进 | 连续输入、上下文预算、Probe、演进、索引就绪和原子发布由处理器负责 |
| Experience | 孵化、基于证据的更新、已定义的后台维护 | 成功可以是持久化 Candidate；审核等待不占 Worker |
| Skill | 已定义的生成、导入、fork、usage 演进等后台步骤 | 精确输入与 target、包验证、审核和发布策略由 Skill 负责；不限定来源必须是 Experience |
| Profile | 已启用 Policy 的 Scope 扫描及 Source Window 画像生成 | 保留 cron/timezone、启动 catch-up、Policy、Cursor、单个待审 Candidate 和可信后台身份语义 |
| Handoff | 已获得业务授权且适合后台运行的准备工作 | 周期配置不授权自动提交 milestone、替用户选择快照或执行 Continue |
| Routine、Procedure、SOP，以及其他规划中的 Family | 在其领域契约和处理器注册完成后使用相同调度接口 | 接入调度不等于获得定时执行外部动作的权限 |
| 偏好、约束、Task Outcome、小工具等产品规划内容 | 按最终所属 Family 接入 | 不仅因产品概念出现就注册一个没有领域契约的空处理器 |

Candidate、Prepared Context、Prepared Handoff、External Skill Registry 和 Handoff Report 不因此成为新的
Artifact Family。它们保留候选、结果或投影的角色；需要后台计算时由所属制品处理器或既有服务承担。
读取、检索、审核与同步领域操作不必经过 Supervisor。已有同步 API 不在本 RFC 中静默改成异步 accepted。

# Reference-level explanation

## 1. 职责与注册

| 组件 | 负责 | 不负责 |
|---|---|---|
| Runtime | 注册处理器、读取部署配置、组织 Supervisor 与资源生命周期 | 自动安装操作系统服务、替用户开启制品业务能力 |
| Supervisor | 类型准入、Scope 发现、公平排队、Worker 生命周期、重试和任期 | Source 选择、模型调用、消费规则、审核和业务发布 |
| 制品待处理信息提供者 | 以有界查询提供本类型的待处理 Scope，并维护领域变化标记 | 在发现阶段执行生成或长业务计算 |
| 制品处理器及其存储适配层 | 选择有限输入、计算、提交、进度恢复、完成确认 | 重新实现选主、Worker 池或退避等待 |

注册至少包含全局唯一且稳定的 `binding_name`、稳定的 `artifact_family`、唯一的配置前缀、待处理信息提供者和
可在子进程中启动的处理器入口，以及关闭、interval 或 cron 三者之一的调度策略。cron 策略同时声明 IANA
timezone。Family 使用领域注册表中的规范名称，例如 `topic-memory`；配置前缀为 `TOPIC_MEMORY`。名称冲突、
重复注册、同时配置 interval 与 cron，或缺失能力的显式配置在启动时拒绝，不能悄悄忽略。

首版要求一个 Artifact Family 只注册一个 Supervisor 可见的 binding；`binding_name` 用于派发和持久化兼容，
`artifact_family` 用于周期、Worker 额度以及 `dedicated` 归属。处理器内部可以组合多个 phase 或业务步骤，但
不会把它们作为额外 binding 暴露给 Supervisor。已有 binding 名称及业务 Cursor 不因调度归一而重命名或合并。
具体 phase、target 或请求参数保存在该制品自己的持久化输入中，Supervisor 不解析这些字段。

Worker 调用在逻辑上为：

~~~text
process_scope(scope_id, execution_context)

execution_context:
  binding_name
  artifact_family
  claimed_request_generation
  supervisor_key, holder_id, supervisor_generation
  worker_id
~~~

处理器从自身存储读取工作；接口不包含 `source_after`、`source_through`、Window limit、Prompt 或业务结果。
控制上下文可用于关联和提交校验，不是业务输入选择器。处理器正常返回表示成功，异常或进程异常退出表示失败。
首版不增加“让出 Worker 后继续本次调用”的第三种调度结果。

待处理发现必须是可取消、有限时限的短查询。单个提供者异常只延后该制品的发现并记录错误；一个 Scope 的初始化
或处理异常只进入该键的失败路径。它们不能冒充全局失主事件、清空其他类型的队列或阻塞续租循环。

## 2. 两级公平调度与预算

类型检查由 interval/cron deadline、显式请求通知、重试 deadline、Worker 退出及必要的后端轮询唤醒。
调度时间到期只为相应类型开启准入机会，不立即调用所有 Scope 的处理器。

Supervisor 轮流检查类型。类型满足注册、任期、触发和容量条件后，才查询其 Scope。每类拥有独立的 ready
队列、分页位置、退避索引及容量上限；单次查询和内存缓存都有固定上限，不能实体化全部积压。

- Worker 额度计算正在启动和正在运行的子进程；正在终止但尚未确认退出的进程仍占额度。
- 排队和退避不占 Worker。退避键不能占满 ready 队列或阻止扫描继续到后页。
- 同一键的 ready、running、retry-wait 是互斥的内存状态，不持久化为 Job 状态机。
- 某类型 Worker 或 ready 容量耗尽时跳过该类型，继续其他类型；容量恢复后继续未完成的发现。
- 同一类型按 Scope 公平派发；显式请求与到期重试不拥有无限优先权，不能长期饿死普通可执行工作。
- 首版不借用其他类型的闲置额度，也不设置低于各类型额度之和的隐藏全局 Worker 上限。

`global` 下总的正常派发并发上限是各已注册类型额度之和。`dedicated` 下同一类型仍只有有效 Leader 可派发，
额度不是每个候选副本各一份。该限制约束有效任期的派发；故障接管时残留进程可能短时继续计算，数据库 fencing
负责阻止旧任期提交。Worker 额度不是模型 token、CPU、内存或数据库连接的硬配额。

## 3. 持久化：调度意图与领域进度分别记录

目标实现新增 `pc_artifact_processing_intents`，为每个 `(binding_name, scope_id)` 保留一条可合并的调度意图。
它不能原地复用现有 `pc_artifact_processing_pending`：该表已经由 Topic Memory PR #1490 创建，且非空的
`source_through`、flush generation 都具有 Topic Source 处理语义。Topic 迁移后继续由其处理器拥有这些字段；
其他制品不写入或伪造它们。

~~~text
pc_artifact_processing_intents

binding_name, scope_id                   PRIMARY KEY
pending_sequence                        UNIQUE，单调递增，首次登记时分配且不复用
dirty_generation                        DEFAULT 0
clean_generation                        DEFAULT 0
requested_generation                    DEFAULT 0
handled_generation                      DEFAULT 0
last_auto_scan_generation                DEFAULT 0

0 <= clean_generation <= dirty_generation
0 <= handled_generation <= requested_generation
~~~

两组 generation 互相独立，不能进行跨组大小比较：

- `dirty_generation / clean_generation`：制品侧是否还有普通待处理工作。领域输入变化时推进 dirty；只有处理器
  确认相应版本的工作已无剩余时，才能推进 clean。这些版本不是 Source journal position。
- `requested_generation / handled_generation`：已接受的 Scope 调用是否完成。显式触发或自动准入推进 requested；
  Worker 成功只确认启动时捕获的 requested generation。
- `last_auto_scan_generation`：同一自动扫描对该 Scope 的准入去重标记，不表示业务完成。

领域输入提交与通用 dirty 标记必须一致提交。外部大对象可以先准备，但可消费的输入引用与 dirty 标记必须在同一
数据库事务中发布；显式请求的输入引用与 requested 更新也遵守这一规则。不能同事务提交的适配器必须提供已有的
可恢复投递机制，不能依赖提交后的内存回调。无 Source 的处理器使用自己的领域变化版本，不需要伪造 Source Journal。

通用 dirty 只表示该处理器可能有工作，是对 Supervisor 的粗粒度通知。Topic 的 `source_through`、Profile Policy、
Memory/Experience Cursor 等领域事实仍决定实际可处理内容。二者短暂不一致时，处理器必须能安全 NOOP 或由领域事实
有界补建 dirty，Supervisor 不能根据通用 generation 推断业务输入已被消费。

一次成功调用可能只处理了制品自己选定的一部分输入。因此它可以推进 handled，同时保留 dirty > clean。
下一次自动准入或显式触发再处理剩余工作。Supervisor 不能因返回成功而自行令 clean = dirty。

处理器在开始处理时读取一个领域快照及对应 dirty generation。即使本次确认该快照已无剩余，也只能确认该版本；
运行期间出现的更新不能被覆盖。业务结果、业务进度、对应的完成确认使用处理器定义的短事务一致提交。
长期保留计数行以避免删除再创建造成 generation 重用；Scope 删除时的清理必须确保没有有效调用或扫描引用。
这些记录不包含每次运行的历史、模型中间结果或 checkpoint。

## 4. 自动检查的时间和分页恢复

每个 binding 在现有 `pc_artifact_processing_binding_states` 中持久化最小检查状态。该表从 Topic Memory 已实现的
`last_auto_wave_completed_at` 演进，保留原 binding 主键：

~~~text
binding_name                            PRIMARY KEY
last_schedule_checkpoint_at             NULLABLE TIMESTAMP
scan_generation                         DEFAULT 0
scan_in_progress                        DEFAULT false
scan_upper_pending_sequence              NULLABLE INTEGER
~~~

对于 interval T，未开始过检查时有待处理工作即可进行首次检查；之后在数据库时间达到
`last_schedule_checkpoint_at + T` 时允许新的自动扫描。对于 cron，该字段保存已被准入的 cron fire 时间；停机期间
错过多个 fire 时合并为一次 catch-up，并推进到数据库当前时间之前最新的 fire。Profile 保留现有“启用后首次立即
catch-up，之后按 cron”的语义。没有容量的类型保持到期，不推进 checkpoint，也不假装已经执行检查。

开始扫描的短事务递增 scan generation、记录 interval 的数据库开始时间或 cron fire、设置 scan-in-progress，
并保存本类型当前最大的 pending sequence。它们一起提交；因此即使进程随后退出，接管者也知道扫描没有完成，
不会因为 checkpoint 已更新而把已到期工作延后一个完整周期。

扫描按 pending sequence 稳定分页，只访问 dirty > clean 且登记序号不超过本次上界的 Scope。后来登记的 Scope 留给下一次自动
扫描；显式请求独立发现。这使一轮扫描的成员数有限，不会因持续新增 Scope 永远无法结束。登记序号只标识 Scope
的待处理记录，不是 Source 水位，不规定处理器消费范围。扫描遵守：

1. 只为当前没有 ready、running 或 retry 占用的键自动准入；忙碌键不等待、不阻塞后页。
2. 对尚未处理过当前 scan generation 的键，在带任期校验的短事务中推进 requested generation，并记录
   last-auto-scan generation，然后才把工作放入内存队列。
3. 已有未确认请求的键沿用该请求，不因自动扫描产生重复请求；仍记录本轮去重标记。
4. 容量限制只能暂停分页，不能将尚未完成的扫描标成完成。
5. 全部发现页完成后清除 scan-in-progress，不等待任何 Worker、失败重试或业务 Cursor。

恢复时沿用未完成扫描的 generation，从第一页重新扫描也必须幂等。last-auto-scan 标记防止已成功的前页 Scope
因为重扫而在同一轮被重复自动准入。迟到可见、登记序号位于已扫描位置之前的记录，由下一次自动检查处理；
显式触发独立发现，不受该分页位置限制。现有 Scope 的新 dirty 不会重置本轮准入去重标记。

interval T 是检查开始之间的间隔，不是全部业务处理完成后的休息时间。扫描期间再次到期只合并一个待检查机会，
不叠加多个扫描；cron 同样合并错过的 fire。扫描完成时已经超期，可以再次检查，仍受类型容量和公平性约束。
重启不补出每个错过时刻的重复任务。

关闭自动处理后不继续接纳尚未准入的普通 dirty Scope；恢复已有 requested > handled 的调用。未完成自动扫描
可以在确认关闭配置后结束，已经持久化的准入请求不撤回。配置恢复开启时，按持久化时间重新检查。

## 5. 显式触发、合并与完成

显式触发由制品 API 校验 Scope、权限、参数及业务能力。接受请求的短事务同时发布必要输入的引用并推进
requested generation；提交后才能返回 accepted。没有业务工作时是否返回 idle，由该制品的公开 API 定义。

调度层只持有 Scope 和请求代次，不保存业务参数或同步等待模型处理。Topic Memory 的既有
`HTTP 200 {"status":"accepted"}` / `{"status":"idle"}` 可以原样保留；本 RFC 不新增通用任务查询 API，
也不改变其他制品已有同步 API 的响应契约或 MCP 暴露范围。

派发时读取最新的 requested generation 为 G：

- 排队期间多个请求合并为一次携带最新 G 的调用。
- 运行期间的新请求继续推进 requested；当前调用只确认 G，因此不会吞掉后到请求。
- 成功后仍有 requested > handled 时，最多保留一次后继调用；实际开始时再次合并最新请求。
- 失败不确认 G，保留该 Scope 的重试责任。新输入、普通自动检查和 flush 均不清除现有退避。
- 普通输入变化只推进 dirty，不直接产生显式请求；它等待下一次可调度的自动检查或显式触发。

派发或处理前若发现 handled generation 已经达到携带的 G，直接确认该调用已完成，不继续消费新的业务输入。
后到请求使用新的 G 另行调度，不能借已完成调用的重试继续推进业务。正常退出却缺少对应持久化完成确认的 Worker
属于处理协议失败，不能仅凭退出码从内存中丢弃请求。

Source 的冻结位置、窗口大小以及某个 flush 在业务上需要覆盖哪些输入，由处理器决定和验证。Supervisor
不比较 Source 水位，也不以“整个 Scope 还有 Source”为理由自行续跑。

## 6. Worker、超时和业务提交

Worker 是受管理的子进程，一次运行一个 Scope 调用。输入选择、token 估算、模型调用、检索及其他可能耗时的
业务准备都在 Worker 内执行，不放到 Supervisor 的派发路径中。子进程按配置重建必要资源，不能依赖只在父进程
内存中存在且无法重建的模型、连接或闭包。

超时从 Worker 启动开始覆盖整个 Scope 调用，不包含此前的排队和退避。它独立于模型单次请求超时。
首版没有 Worker 进度心跳或“仍有进度所以无限续期”机制。

超时后停止该 Worker，先进行有限的正常终止等待，再执行必要的强制终止；确认退出后才释放额度并安排重试。
无法确认退出时，不在同一任期直接启动第二个同键 Worker。任期变化必须先使旧提交失效，再恢复新派发。
已经成功提交的事务不会因为随后超时而被撤销；`deadline_at` 不进入业务提交的正确性条件。

处理器在数据库事务外计算，在短事务中提交业务结果。每个业务提交都校验有效任期和自身的版本/进度条件；最后
的成功确认与相关业务提交一致。一次调用如包含多个合法短事务，途中失败可以保留已提交的业务进度，但不能确认
尚未完成的调用。重试从持久化业务进度恢复，不重放已经生效的副作用。

NOOP 可以成功确认调用；生成 Candidate 后等待审核也可以成功确认。Supervisor 不接收完整模型输出，不代替
处理器发布。传输丢失或提交后进程退出可能导致重复调用，因此不承诺恰好执行一次；处理器必须以原子进度、版本
CAS 或业务幂等键保护重复调用。

Supervisor 不替处理器作业务授权。显式触发在领域 API 接受意图前完成权限检查；自动处理由注册的处理器包装层
恢复当前部署的可信后台 Principal，并对实际 Scope 执行领域要求的授权和审计。Profile 已有的可信 Runtime 服务
身份以及 Memory/Experience 已有的 scheduled access runner 语义必须保留。授权失败属于该 Scope 的处理失败，
不能升级为整个 `global` Supervisor 失主。

## 7. 失败与退避

每个 `(binding, scope)` 在 Supervisor 内存中维护 consecutive-failures 和 next-retry-at。失败退出后移入延迟
队列，到期且本类型有额度时重新进入公平 ready 队列。

- 采用带小幅抖动的指数退避：约 30 秒、1 分钟、2 分钟，直到约 30 分钟上限。
- 不设置最大重试次数，不自动跳过失败输入，不新增 failed/人工恢复任务状态。
- 同一 Scope 新增输入和显式触发不重置退避；成功后清除失败次数。
- 模型、校验、检索、Embedding、数据库写入、Worker crash 和 timeout 等真实失败统一采用该策略，并记录原因。
- 处理器进度或 Head CAS 冲突使用固定的短延迟重调度，不增加普通失败次数，也不能立即自旋。
- leadership lost 是控制事件：停止该 Supervisor 的派发和提交，交给新的有效任期恢复。

退避只放内存。重启或换主后可以立即重试已接受的失败调用，随后重新建立退避序列；这不会跳过其业务失败位置。
永久错误可能持续消耗调用成本，需要通过可观测性发现，首版不自动弃置。

## 8. 任期、后端和进程角色

一条 Lease 对应一个逻辑 Supervisor，而不是一个 binding、Scope、Worker 或 Worker 预算分区。`global` 模式只有
一个 Supervisor，因此所有制品共同竞争并续租唯一的 `global` Lease；某种制品的队列或处理器异常不能另建 Lease。
`dedicated` 模式下每种已注册制品各有一个 Supervisor，因此各有一条 `artifact:<规范 Family 名称>` Lease。多个
后台副本只是同一 Supervisor 的候选者，共同竞争同一个 key，不会因候选副本数量增加 Lease 数量。

内部 Lease key 由模式确定：`global` 或 `artifact:<规范 Family 名称>`。它不是用户配置的路由名称。
Lease 至少保存 supervisor key、随机 holder ID、单调递增的 supervisor generation 及可空的 expires-at。
generation 防止同一 holder 失主后重新获得领导权的 ABA，不能只靠进程 UUID。

### OceanBase

多个候选者通过原子竞争获得已过期 Lease；接管递增 generation，正常续租不递增。Supervisor 按短周期续租并
发现跨进程请求；续租失败立即停止派发，尽力终止 Worker。数据库时间是租约和持久化自动检查时间的基准。

Worker 的每个业务写事务和 Supervisor 的每个调度控制写事务，都必须在该事务中锁定并校验 Lease，验证 holder、
generation 和未过期条件，锁持续到提交。调度控制包括扫描开始/结束、请求准入和控制状态清理；领域 API 发布
dirty 或接受显式请求不要求成为 Leader。不能先在独立事务中检查任期，再无条件写业务数据或调度状态。

### SQLite

单个 Runtime 宿主以 `all` 角色运行。每个内部 Supervisor key 在合法启动时更新 holder、递增 generation，
expires-at 为 NULL；没有选主、租约超时或续租。`dedicated` 只在该宿主内增加按类型组织的控制器。

任期替换和 Worker 提交必须由 SQLite 的真实写事务串行保护，例如 `BEGIN IMMEDIATE` 或等效的条件写与锁。
不能依赖 SQLite 会忽略的 `FOR UPDATE`，也不能以未锁定的“先读任期再写入”实现 fencing。
Supervisor 对扫描开始/结束、请求准入和控制清理的写事务同样必须校验 holder/generation，并由真实写事务保护。

SQLite 以显式请求通知、自动 deadline、重试 deadline 和 Worker 结束唤醒调度，不增加空闲时的周期数据库轮询。
一次扫描开始时记录通知序号；扫描期间收到新通知或因容量不足中断，必须保留重扫责任。尾页完成后若通知已变化，
从头继续检查，再决定休眠，避免先前扫过的 Scope 在后来 flush 后永远滞留。

### 角色与恢复

保留 `runtime.artifact_processing_role`：`all` 运行 API 与后台；`api` 只提供 API；`background` 只运行后台。
OceanBase 支持三种角色；SQLite 只支持 `all`。后台副本根据模式自动组织候选 Supervisor，standby 是健康状态，
不因未当选而反复重启。API 接受意图不等于后台当前存活，后台可用性通过健康状态和积压指标表达。

API 与后台必须对 mode、binding 到 Family 的映射以及可触发能力形成相同理解。Worker 额度、timeout、模型和
其他仅执行端需要的配置可以只出现在后台候选；API 不因没有这些执行资源而伪装成处理器实例。`global` 中某个
制品的发现或处理错误只降低该制品的健康状态，Supervisor 的选主和其他制品继续运行；共享 Lease 本身失败才改变
整个 global 控制器状态。

恢复顺序为：建立有效任期，加载制品检查状态，恢复 requested > handled 的调用和未完成扫描，再按 deadline
检查普通 dirty。不接管旧进程的 ready 队列、退避时间或业务中间结果。Worker 提交成功但通知丢失时，持久化
完成确认和处理器自己的进度决定是否还有工作。

## 9. 关闭与模式切换

正常关闭停止发现和新派发，对运行 Worker 执行有限清理。已接受请求没有完成确认就保留，不能通过清空队列
宣称完成。必要时作废当前任期，保证残留 Worker 无法继续提交。

`global` 与 `dedicated` 的切换采用协调停机：

1. 停止全部旧后台候选及其 Worker，阻止旧配置自动重启；迁移维护阶段暂停新的写入和显式触发。
2. 在数据库中作废旧模式的所有有效任期并保留、推进 generation；不能只新建另一模式的 Lease key。
3. 各副本采用一致的新模式、注册表及制品配置，再启动新任期；退出维护状态。

此步骤同样适用于切回 `global`。尤其 SQLite 的旧永久任期不会仅因出现新 key 自动失效。
模式切换不重置请求计数、普通待处理标记、制品检查时间或处理器 Cursor。首版不支持滚动混用两种模式、
在线路由表、动态租约归属或逐制品混合分组。

## 10. 配置契约

环境变量统一以前缀 `POWERCONTEXT_SERVER_RUNTIME_` 开始；Runtime 模型采用对应的小写字段。

| 后缀 | Runtime 字段 | 默认及校验 |
|---|---|---|
| `ARTIFACT_PROCESSING_SUPERVISOR_MODE` | `artifact_processing_supervisor_mode` | `global`；仅允许 `global`、`dedicated` |
| `ARTIFACT_PROCESSING_ROLE` | `artifact_processing_role` | `all`；后端支持范围见上文 |
| `<ARTIFACT>_SCHEDULE_SECONDS` | `<artifact>_schedule_seconds` | 未设置/None 关闭自动准入；配置值必须大于 0 |
| `<ARTIFACT>_SCHEDULE_ENABLED` | `<artifact>_schedule_enabled` | cron 制品的显式开关；首个使用者为 Profile |
| `<ARTIFACT>_CRON` | `<artifact>_cron` | 五段 cron；必须与合法 IANA timezone 一起解析 |
| `<ARTIFACT>_TIMEZONE` | `<artifact>_timezone` | cron 时区；Profile 保持 `Asia/Shanghai` 默认值 |
| `<ARTIFACT>_MAX_WORKERS` | `<artifact>_max_workers` | 必须为正整数；内置兼容默认值见下表 |
| `<ARTIFACT>_WORKER_TIMEOUT_SECONDS` | `<artifact>_worker_timeout_seconds` | 必须大于 0；Topic 默认 600 秒，其余迁移类型默认 600 秒 |

| Family | Worker 默认额度 | 兼容依据 |
|---|---:|---|
| Memory | 1 | 现有 APScheduler activation 和共享 processor lock 串行执行 |
| Topic Memory | 10 | 现有 `ARTIFACT_PROCESSING_MAX_WORKERS=10` |
| Experience | 1 | 现有 APScheduler activation 和共享 processor lock 串行执行 |
| Profile | 4 | 现有 `PROFILE_MAX_CONCURRENCY=4` |
| 以后接入的 Family | 1 | 接入 RFC 可以基于已验证负载修改 |

内置规范前缀包括 MEMORY、TOPIC_MEMORY、EXPERIENCE、PROFILE、SKILL、HANDOFF，只有存在相应可调度处理器及
业务能力时配置才可启用。未来 Family 在注册时声明无冲突的前缀。设置调度策略不会创建处理器、选择默认模型或
打开外部动作权限。

配置在启动时生效；所有 OceanBase 实例必须使用一致的模式、binding/Family 映射和触发能力声明，后台候选还必须
使用一致的调度、额度及 timeout。显式开启能力却缺少处理器或依赖时返回明确配置错误。未开启的生成能力不阻止
已有制品读取和审核。

默认 Worker 数是逐制品预算，不是部署总数。启用多种制品前应按额度之和配置部署资源。
模型、上下文窗口、Source 窗口、检索形态和审核策略继续使用各自配置，不移入 Supervisor mode。

## 11. 兼容与接入顺序

本 RFC 修订既有 Supervisor 的通用调度与领域处理边界。Topic Memory RFC 继续定义 Topic 内容、Source 顺序、
Window、生成、检索和发布；其中由 Supervisor 选择 Window、使用全局共享 Worker 额度、等待全体 Scope 完成后再
推进自动周期，以及任意命名 group 的未来方向，由本文替代。其他相关 RFC 的 Source、Artifact、Review、Scope
和检索契约继续有效；调度组织、准入、Worker 单位及恢复以本文为准。

当前主干的迁移基线是 Topic Memory PR #1490：`ArtifactProcessingBinding` 仍包含 `source_window_limit`、
`window_selector` 和 Window launcher，`ArtifactProcessingWorkAssignment` 仍携带 Source 范围；Supervisor 直接
读写 Source Cursor、Pending、binding state 和 auto-wave target。目标注册接口增加 Family、调度策略、Scope
提供者、逐制品额度和 Scope processor；Topic 的现有 binding 名称保持不变，但 Source 选择和这些领域表的操作
移入 Topic processor。这个重构必须先有行为等价适配器，再删除旧 Supervisor 路径。

配置迁移遵循确定规则：

- Topic Memory、Experience 已有周期 Key 保持名称与关闭语义。
- `SCHEDULE_SECONDS` 作为 `MEMORY_SCHEDULE_SECONDS` 的兼容别名；同时显式配置且值不同则启动失败。
- Profile 保留 `PROFILE_SCHEDULE_ENABLED`、`PROFILE_CRON` 和 `PROFILE_TIMEZONE`。现有
  `PROFILE_MAX_CONCURRENCY` 作为 `PROFILE_MAX_WORKERS` 的兼容别名；新旧同时显式配置且值不同则启动失败。
- 已有 `ARTIFACT_PROCESSING_MAX_WORKERS` 和 `ARTIFACT_PROCESSING_WORKER_TIMEOUT_SECONDS` 仅作为 Topic
  Memory 对应新字段的兼容别名，因为它们对应的首个 Supervisor 处理器是 Topic Memory。不能继续把前者解释为
  所有类型共享总额度，也不能悄悄复制为每种制品的自定义值。新旧同时显式配置且不一致则启动失败。
- 有效配置日志标明使用的兼容别名。默认 600 秒的含义是一次 Scope 调用；Topic 迁移时必须验证其内部选定工作
  与该预算相容，不得静默通过延长超时或调整 Source 选择掩盖差异。

接入时先停用该类型的旧后台入口，再启用统一 Supervisor，禁止 APScheduler job、旧 flush 执行器与新 Worker
同时处理同一领域进度。Memory/Experience 的旧共享 processor lock 和 Profile 的进程内 semaphore 不能代替
多副本提交幂等；逐制品通过提交与恢复验收后，才能解除其旧角色限制。未完成接入的类型不注册虚假的后台能力。
迁移可以逐制品完成：尚未迁移的 Memory、Experience 或 Profile 继续使用现有 APScheduler sidecar，但不能因此
被报告为已经受 Supervisor 管理。

存储迁移在停机维护阶段完成：

- 新建通用 intent 表；不把现有 `pc_artifact_processing_pending.source_through` 改成可空通用字段，也不让非 Topic
  制品写入该表。
- 旧 processing binding 显式映射到 Family，独立业务 Cursor、Topic Pending 和证据引用保持不变。旧未覆盖的
  Source pending 在通用 intent 表中补建 dirty；未确认 flush 同时补建已接受请求，不能变成仅等待自动周期的数据。
- 领域进度显示仍有工作但缺少旧 Pending 的 Scope，由制品提供者有界补建普通 dirty；新处理器首次接入或恢复
  启用也执行这一校准，不能只等待未来输入事件。补建不绕过关闭的自动周期，也不自行接受业务动作。
- 已启动但未完成的 Topic 自动目标转为已接受调用，保留恢复责任。不能简单删除旧自动目标表后仅复制完成时间。
- 没有进行中工作的类型可用旧自动完成时间作为检查时间基准，保留原有剩余等待；无法证明旧自动工作已完成时，
  保守保留调用意图，由处理器进度去重。
- Profile 首次迁移时保留立即 catch-up；最近 cron fire、Policy、Cursor 和 pending Candidate 共同决定是否有工作，
  不能因切换 scheduler 重复生成 Candidate 或跳过待审核状态。
- 只有新 intent、领域 pending、检查状态和业务进度完成一致性校验后，才停用 Topic 的旧调度字段和临时自动目标表。

全部类型最终通过同一注册契约接入，但各领域业务管线可以分批交付；本 RFC 不以新增空 Family 或修改消费行为
充当接入完成。

## 12. 可观测性与验收

启动日志记录模式、角色、注册 binding/Family、有效调度策略、额度、超时及候选任期。调度失败日志包含 binding、
family、scope、触发原因、
请求代次、worker ID、任期、stage、error code、异常类型、重试次数和延迟；Source 范围等领域字段由处理器记录。
禁止记录 Source 正文、Prompt、模型完整输出、凭据或完整数据库连接串。

按制品提供可用/已用额度、ready 数、retry-wait 数、未确认请求、发现延迟、调用时长及失败/超时统计。
Scope、Worker、请求 ID 等无界标识不作为 metrics label。采用现有日志、metrics、trace 和健康接口，不新增任务
控制台或查询每次运行的 API。现有总览 `artifact_processing_supervisor` readiness 保留，并增加逐 Family 状态；
`global` Lease 有效而单个 Family provider 异常时，总览报告部分降级，其他 Family 仍可调度。

行为验收必须覆盖：

1. 同一 binding 键不并行；不同 Scope 和不同类型按独立额度并行。
2. 长任务占满一类制品，其他类型仍能发现和派发；大量前页或退避键不能饿死后页。
3. B1 持续失败时 B2 不越过，A 完成后新增的 A3 仍在后续周期获准处理。
4. 处理器只完成部分业务输入而成功返回，剩余普通工作保留到下一周期。
5. 各制品调度策略互相独立；满额时不错误推进 checkpoint；扫描完成不等待 Worker 成功。
6. 自动准入后、入内存队列前崩溃，以及自动分页中途崩溃，都不丢请求、不重复准入同轮前页。
   持续登记新 Scope 时本轮仍有限结束，旧 Scope 的新 dirty 能在后续检查中得到准入。
7. 自动关闭时普通 dirty 不运行，但显式触发和已接受请求仍可恢复。
8. 排队和运行期间多次 flush 合并；成功确认不吞新触发、新 dirty 或已经被扫描过的 Scope 的后来通知。
9. 提交成功但返回通知丢失、Worker crash、超时和部分合法业务提交后失败，都可按业务进度安全恢复。
   已确认 G 的重复调用不继续消费新输入；输入引用与意图发布之间的崩溃不能产生不可发现的工作。
10. 任期竞争、同 holder 再次当选、旧 Worker 存活、新旧提交竞态均不能产生旧任期写入；SQLite 验证真实事务锁。
11. `global` 与 `dedicated` 双向切换保留进度并使旧模式任期失效；SQLite 不意外开启多进程模式。
12. Topic Memory 从 PR #1490 的 Source Window assignment、Pending 和 auto-wave target 迁移后，已有未覆盖 Source、
    未确认 flush、运行中 wave、Cursor 和 `last_auto_wave_completed_at` 均不丢失，既有 API 行为保持不变。
13. Profile 保留 cron/timezone、启动 catch-up、Policy、Cursor、pending Candidate 和后台 Principal；迁移不会在同一
    cron fire 重复生成，也不会因 APScheduler sidecar 停用漏过已有 Source。
14. 每个制品分别验证 Source 顺序或对应业务幂等、Candidate 审核边界、Handoff 授权边界；不以调度器测试代替。
15. 新旧配置别名、无效数值、未知模式、interval/cron 冲突、缺失能力和前缀冲突产生可诊断的启动结果。

# Drawbacks

- 每种制品独立预算会保留闲置容量；多个类型同时运行时总资源消耗可以高于旧共享池。
- `dedicated` 增加控制器和租约数量，但不会自动隔离共享数据库、模型或宿主故障。
- 新增通用 intent 会与 Topic Pending、Profile Policy 等领域状态保存少量重复的粗粒度待处理信息；其用途是调度
  恢复和去重，不能成为业务消费进度的第二权威来源。
- 无限退避重试不能自动解决永久错误；不保存阶段 checkpoint 时可能需要重新计算未提交部分。
- 每个处理器都需要证明自己的提交幂等、完成确认和待处理标记一致性，迁移工作不能仅替换 scheduler。

# Rationale and alternatives

- `global` 默认降低运行成本；`dedicated` 表达按制品专属调度，比暗示完整资源隔离的 `isolated` 更准确。
- 只提供两种整体模式，避免无明确场景的任意分组、逐制品路由和在线归属迁移。
- Scope 调用保持处理器自主权；把 Source Window 定为通用调度单位会排除非 Source 工作，并使 Supervisor
  承担领域输入选择。
- 类型先准入、Scope 后发现，使独立预算同时作用于发现、排队和执行，而不只是最后启动 Worker 时的计数。
- interval 按检查开始计算，cron 按 fire 记录 checkpoint；扫描结束不等待业务完成，避免把一个失败 Scope 变成
  整个制品的处理障碍。
- 持久化可合并请求和普通 dirty，内存保存执行队列及退避，提供恢复所需事实而不建立通用 Job 平台。
- 处理器成功与清空普通积压分别确认，避免强迫每次调用排空全部输入，或错误丢弃未选中的工作。

# Prior art

PowerContext 的 [Topic Memory RFC](1417_topic_memory.md) 首次定义了 `ArtifactProcessingSupervisor`，并提供
Pending、独立业务 Cursor、Worker 子进程、
SQLite/OceanBase 任期、短事务发布、flush generation 和内存退避的基础。[RFC 0019](0019_local_source_memory_runtime.md)
提供 Source 驱动的本地处理模型；[RFC 0051](0051_experience_skill_artifact_families.md) 和
[RFC 0050](0050_artifact_candidate_review_inbox.md) 定义了不同制品的精确证据、Candidate、Review 与版本提交。
[Profile RFC](1485_profile_artifact.md) 提供现有 cron、启动 catch-up、逐 Scope Cursor、Policy 和 Candidate 边界，
是除 Topic Memory 外另一个必须保持行为的后台处理基线。

本文使用这些已有领域与存储契约，统一它们的调度入口。用户侧的挂载、检索、Continue 和外部动作仍遵守各领域
契约；[RFC 1299](1299_local_server_availability_and_service_installation.md) 的操作系统服务生命周期也不由
Artifact Processing Supervisor 接管。

# Unresolved questions

本文范围没有留给实现者任意选择的产品语义。注册接口的具体 Python 类型、内部页大小、轮询时长和存储迁移脚本
由实现确定，并须满足上述有界性、恢复和后端验收要求。

以下能力明确排除：按 Scope 覆盖调度配置、运行时动态配置、任意分组、Worker 额度借用、优先级抢占、持久化
retry history、Job 查询/取消、模型阶段 checkpoint、在线模式迁移、统一 Source 消费算法和统一制品审核策略。

# Future possibilities

已有领域契约的新 Artifact Family 可以直接注册处理器；不存在处理器的规划概念需要先完成自己的领域设计。
只有出现明确需求时，再分别评审共享模型资源预算、在线模式切换、运行时配置或运维控制能力，不能以实现便利
为由隐式加入当前调度契约。
