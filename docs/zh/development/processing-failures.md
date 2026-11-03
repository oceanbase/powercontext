# 处理失败诊断

本文是 Artifact 处理失败分类与安全诊断的共享契约。它扩展现有的可观测性日志字段，不引入第二套
telemetry 系统。

## 共享字段

每次处理失败保留现有的身份与结果字段（`family`、`operation`、`worker_id`、`request_generation`、
`outcome`、`error_code`、`exception_type`），并可按需增加：

| 字段 | 含义 | 取值 |
|---|---|---|
| `processing_stage` | 失败所属的处理阶段 | `generation`、`embedding`、`output_parsing`、`business_validation`、`persistence`、`usage_accounting`（预留给账务上报或持久化） |
| `failure_reason` | 有证据支持时才给出的有界原因 | `context_overflow`、`output_truncated`、`rate_limit`、`timeout`、`schema_invalid`、`provider_unavailable`、`configuration`、`invalid_output`、`storage_failure`、`usage_limit` |
| `input_item_count` | 失败调用涉及的输入条目数 | 非负整数 |
| `estimated_input_tokens` | 估算的输入 token 数 | 非负整数 |
| `token_estimator_id` | 产生估算值的估算器标识 | 有界字符串 |
| `effective_budget` | 本次调用生效的预算 | 非负整数 |
| `provider_request_id` | provider 返回的请求标识 | 有界字符串 |
| `finish_reason` | provider 返回的结束原因 | 有界字符串 |
| `failure_location` | 稳定的内部位置或操作标识 | 有界字符串 |
| `cause_exception_type` | 映射前的原始 provider/库异常类型 | 有界字符串 |

`processing_stage` 与既有的 `stage` 字段不同：`stage` 表示发出失败的位置或领域位置，包括
Worker/supervisor 值（`worker_process`、`work_admission`、`supervisor`、`lease_renewal`、`scope_discovery`、
`pending_observation`、`term_revocation`、`worker_termination`）、领域 block 名称（如 `topic_memory`），
以及异常未携带 stage 时 child Worker 的 `worker` 兜底；没有领域 block 的 child 失败可能完全省略 `stage`。
`processing_stage` 表示有界的领域阶段。计数与标识在写入日志前会被限界；未知或不受支持的值直接省略，
不做猜测。

## 隐私边界

诊断不得包含 Source/Artifact 正文、prompt、模型响应、凭据、授权头、SQL 参数或任意异常文本。provider
标识与原因只保留有长度上限的可打印文本。字段进入白名单不等于自动脱敏，调用点仍必须保证值本身安全。

## 覆盖矩阵

`Covered` 表示该阶段已有稳定分类与测试；`Partial` 表示部分失败可经 operation 或异常类型推断，但尚未覆盖
所有抛出点；`Missing` 表示尚未分类；`N/A` 表示该 Family 的处理路径不执行该阶段。

| Family | Generation | Embedding | Output parsing | Business validation | Persistence | Usage accounting |
|---|---|---|---|---|---|---|
| Memory / Atomic Memory | Partial | Covered | Partial | Covered | Partial | Missing |
| Topic Memory | Partial | Partial | Partial | Missing | Missing | Missing |
| Experience | Partial | N/A | Partial | Missing | Missing | Missing |
| Profile | Partial | N/A | Partial | Missing | Missing | Missing |
| Skill | Partial | N/A | Partial | Missing | Missing | Missing |
| Handoff | Partial | N/A | Partial | Missing | Missing | Missing |
| Prompt | Partial | N/A | Partial | Missing | Missing | Missing |

Memory / Atomic Memory 作为当前切片，按 operation 粒度跟踪：

| Operation | Stage | 状态 | 说明 |
|---|---|---|---|
| `atomic-memory.extract` | Generation | Partial | provider timeout、不可用、限流、配置错误已分类；仅 adapter 暴露 finish reason 时可见。 |
| `atomic-memory.extract` | Output parsing | Partial | schema 与 Pydantic 输出失败已分类；部分 provider 截断仍表现为 schema 失败。 |
| `atomic-memory.extract` | Business validation | Covered | 候选内容与引用证据规则输出 `business_validation` / `invalid_output`。 |
| `atomic-memory.reconcile` | Generation | Partial | 与提取相同的 provider 映射。 |
| `atomic-memory.reconcile` | Output parsing | Partial | 与提取相同的 schema 映射。 |
| `atomic-memory.reconcile` | Business validation | Covered | 对账输出与内容规则输出 `business_validation` / `invalid_output`。 |
| Embedding（提取、对账、查询） | Embedding | Covered | provider 状态、向量数量/顺序/类型错误、profile/input 失败均已分类。 |
| Publish / index | Persistence | Partial | `AtomicMemoryIndexError` 的 code 已映射；其他存储失败保持 unknown。 |
| Usage accounting | Usage accounting | Missing | 尚未分类账务上报与持久化失败；推理用量限制归入其来源阶段（generation，或 embedding adapter 上报时归入 embedding）。 |

第一个落地切片覆盖 Memory / Atomic Memory：provider 失败、预算耗尽、提取/对账校验均有分类，安全计数字段
可从子 Worker 传播到 supervisor 日志。其他 Family 在各自切片落地前保持现有行为；每次切片在同一变更中更新
本矩阵。

## 落地顺序

1. 共享诊断契约、日志白名单字段、Worker 传播，以及 Memory / Atomic Memory 切片（本次变更）。
2. Topic Memory：标注分阶段生成、索引/嵌入与持久化失败。
3. Experience、Profile、Skill：标注生成输出校验与持久化入口。
4. Handoff、Prompt：标注生成与发布失败，再补 usage accounting 分类并补全矩阵。

新增 instrumentation 时，需要覆盖可观察行为：分类正确、跨真实 spawn Worker 传播、敏感值不出现，以及诊断
失败不改变业务结果。
