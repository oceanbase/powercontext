- Proposal Name: `decision_model_rerank_seam`
- Start Date: 2026-09-26
- RFC PR: [oceanbase/powercontext#1745](https://github.com/oceanbase/powercontext/pull/1745)
- Related RFCs: [RFC 0080](0080_memory_search_reranking.md)、[RFC 0014](0014_memory_layer_design.md)

# Summary

本 RFC 为 PowerContext Memory 搜索增加一种可选的 rerank 策略：复用 PowerContext 的跨家族决策模型端口（cross-family
decision model port），在 RFC 0080 建立的 rerank 接缝后面接入第二种 `MemoryReranker` 实现。它不再发出一次 listwise
生成请求，而是对每个粗排候选向决策模型提出一次窄范围的 keep/drop 判定，在保留的候选之间维持粗排顺序，并且只输出粗排整数
rank；因此每个被选中的 hit 都保持其准确的 Artifact、entry 与 Revision 身份。决策模型只由确定性 Runtime 代码在搜索路径上
调用，绝不暴露为可被模型调用的 tool。该策略为 opt-in 且默认关闭。由于 RFC 0080 要求 rerank 阶段 fail-closed，本 RFC 同时
把决策角色的**运行时失败策略**与既有的 fail-open 信封分离：reranker 失败时 fail-closed，而现有的咨询类消费者继续保持
fail-open。

# Motivation

RFC 0080 引入了一个 provider-neutral 的 rerank 接缝：融合产出的、有序且有界的粗排候选池被交给 `MemoryReranker`，后者返回
一组稀疏的原始 rank，每个被选中的 hit 都保持其准确身份。该 RFC 同时上线了一个策略——一次 listwise 结构化生成请求，复用
所配置的 generation model。

PowerContext 已经暴露了一个**跨家族决策模型端口**。它针对一个小而显式的请求，由确定性 Runtime 代码提出一次窄范围的
yes/no/abstain 判定；它服务于内部 runtime 步骤，而不是被模型调用的工具。有些部署具备可用的决策后端，但没有合适的 listwise
生成行为，因此希望能够基于这种决策能力进行 rerank，而不必再采用一套 listwise prompt 策略。

接缝本身已是合适的位置：它 provider-neutral、按构造即保持身份、且可组合。增加第二种 `MemoryReranker` 实现，可让部署选择
它真正能够支撑的 rerank 策略。

预期结果：

- 一种 opt-in、默认关闭、可逐部署选择的替代 rerank 策略；
- 与 RFC 0080 兼容的身份保持与 fail-closed 失败行为；
- 不新增持久化、不新增公开 HTTP/OpenAPI 面，且不改变粗排候选池的成员身份。

这是一个**接缝**，不是质量主张。本 RFC **不**断言决策式 rerank 比 listwise 策略或比粗排顺序召回更好的证据。没有评估授权
这一主张，本 RFC 有意对此保持沉默。

# Guide-level explanation

## 「决策式 rerank」是什么

决策式 rerank 是 Memory 搜索的第二阶段选择策略。第一阶段不变：词法与向量通道召回候选，Reciprocal Rank Fusion 产出粗排
顺序。第二阶段与 listwise 策略的区别在于**如何选择**：

- listwise 策略把整个有界候选池交给一次结构化生成请求，要求返回一组稀疏且有序的 rank 子集；
- 决策式 rerank 逐个遍历粗排候选，对每个候选只提出一次窄范围的 keep/drop 问题——「这个候选是否回答了 query？」——然后
  按粗排顺序保留答案为 *yes* 的候选，直到调用方的 limit。

由于决策模型对单个请求只返回单一裁决，决策式 rerank 天然是**逐候选**的，不是 listwise 选择。这是该策略的定义性特征，
也是它灵活性与成本的共同来源（见下文）。

## 用户应如何理解它

与 RFC 0080 一致，rerank 仍是 **deployment policy**，默认关闭。是否启用是装配期选择：部署通过 RFC 0080 已定义的同一
条**注入通路**提供一个 `MemoryReranker`，且该选择即使环境上的 rerank 开关为 false 也生效。本 RFC 不引入任何新的环境开关
或配置字段。搜索请求 API 不变，`limit` 仍表示最终 hits 的最大条数。

启用决策式 rerank 后，部署应预期：

- 返回的 hits 始终是同一粗排池的子集，按粗排顺序，最多 `limit` 条；
- 模型只选择位置、从不选择身份——它绝不提供 Artifact ID、entry ID、score 或 citation，也无法修改已存储的 Memory；
- 该策略只作用于非空的 rerank 搜索；没有候选时，搜索会像今天一样快速返回。

## 关键立场：Runtime 步骤，而非 tool

决策模型**只**由搜索路径内的确定性 Runtime 代码调用。它**绝不**注册进可被模型调用的 tool 目录，也绝不作为函数交给
agent 调用。

这是设计决策，不是实现细节：

- rerank 选择是一个确定性的组合步骤，其输入输出均归 PowerContext 所有。把它留在 Runtime 代码中，可使契约保持小而
  可测、且独立于任何 tool schema。
- 把它暴露为可调用 tool，会允许模型递归地驱动 rerank，从而放大成本与影响面，并使契约无法治理、无法设界。
- RFC 0080 本就把 rerank 选择视为一个有界、经校验、非权威的步骤。决策式 rerank 保持这一性质：模型提出位置，由
  PowerContext 校验并解析。

## 失败与成本预期

有两种失败行为，刻意**不**与咨询类决策对称：

- **启用却不可运行＝配置错误。** 若启用了决策式 rerank 却没有可用的决策后端，Runtime 在启动时以配置错误失败。这与
  RFC 0080 一致（rerank 启用但无 reranker 或 generation model 时启动失败）。理由相同：配置错误的 reranker 必须响亮，
  而不能被静默地当作不存在。
- **搜索时的后端失败不会被吞掉。** provider 超时、provider 不可用以及非法结构化输出，**不会**被静默地转换成粗排结果。
  幂等的搜索调用可以重试，但调用方观测到的是 rerank 降级，而不是悄悄回退到粗排顺序。

成本是主要取舍。决策式 rerank 会发出**每个候选一次**决策请求（受候选上限约束），而 listwise 策略是每次搜索一次请求。
当模型延迟与 token 成本比新增选择阶段更重要时，应保持决策式 rerank 关闭。

# Reference-level explanation

## 与 RFC 0080 的关系

RFC 0080 定义了接缝及其契约。本 RFC 增加同一端口的第二种实现，且不得改变 RFC 0080 的任何保证。对齐关系如下：

| RFC 0080 元素 | 本 RFC |
| --- | --- |
| `MemoryReranker` 端口（`policy_id`、`rerank(query, candidates, limit)`） | 原样复用；在其后新增一种实现。 |
| `MemoryRerankDecision`（`selected_ranks`、`usage`、`discarded_rank_count`、`used_fallback`）与进程内 trace | 原样复用；不改字段。 |
| 粗排池成员身份与 rank→hit 解析 | 不变。适配器只输出 rank，由 service 解析。 |
| 身份保持（模型绝不提供 ID/score/citation） | 保持；适配器只输出粗排整数位置。 |
| 默认关闭的 deployment policy | 保持；新策略为 opt-in 且默认关闭。 |
| 注入通路（注入的 `MemoryReranker` 是应用组合选择，即使环境开关为 false 也生效） | 复用为启用机制；不新增配置字段。 |
| fail-closed 失败行为 | 保持，并针对本策略显式化（见 *失败策略分离* 与 *装配期失败与运行时失败*）。 |
| 无持久化、无迁移、无 HTTP/OpenAPI 变更 | 保持。 |

RFC 0080 并不要求新增一个环境开关来增加一种 reranker 实现；它已经定义了启用通路：

> An injected reranker is an application composition choice and is applied even when the environment flag is false.
> This supports tests and deployments with a provider-specific adapter while keeping environment-driven composition
> explicit.

决策式 rerank 是第二种 `MemoryReranker` 实现，因此走同一条通路：部署通过应用组合提供该实现，且该选择即使环境上的
rerank 开关为 false 也生效。因此本 RFC **不**新增任何配置字段、**不**新增任何环境变量，**不**复活既有的零消费者
`MemoryRerankMode` 枚举（复活它会在 `memory_rerank_enabled` 布尔开关旁再并出一套开关），也**不**改动 RFC 0080 配置契约的
任何文字。

## 决策式 reranker 适配器

新增实现是一个 `MemoryReranker`。按 RFC 0080，它接收归一化后的 query、有序的粗排候选以及最终 limit，并返回
`MemoryRerankDecision`。

```text
rerank(query, candidates, limit):
    validate bounds (len(candidates), min(limit, len(candidates)))
    kept   = []
    usages = []
    for rank, candidate in enumerate(candidates, start=1):
        result = await decision_model.evaluate(DecisionRequest(
            decision_kind = memory.rerank,      # 低基数消费者标签
            question      = "does this entry contain evidence that answers the query?",
            subject       = candidate.text,
            evidence      = (query,),
        ))
        usages.append(result.usage)
        if result.used_fallback:                 # DecisionResult.used_fallback：后端降级 -> fail closed
            raise RerankFailure()
        if result.outcome is keep_on:            # keep_on 默认为 YES
            kept.append(rank)
        if len(kept) == limit:
            break
    selected = tuple(kept) or fallback_ranks(len(candidates), limit)
    return MemoryRerankDecision(
        selected_ranks       = selected,
        usage                = sum_usages(usages),
        discarded_rank_count = len(candidates) - len(selected),   # 未进入最终选择的候选数
        used_fallback        = not kept,                          # MemoryRerankDecision 字段：使用了内建回退
    )
```

要点：

- **粒度。** `DecisionModel.evaluate` 对单个请求返回单一 `DecisionOutcome`（`yes`/`no`/`abstain`），无法表达稀疏且有序的
  rank 子集。因此决策式 rerank 不可能是 listwise；它是每个候选一次、有界的 keep/drop 判定。
- **身份。** 适配器最多输出 `limit` 个粗排整数 rank，由 service 解析回原始 `MemoryHit` 对象。不新增、不删除、不重新
  标识任何候选。
- **`abstain` 与后端失败的区别。** 健康后端对某候选弃权时，按保守保召回处理为 keep（不丢弃证据）。**失败**的后端
  （`used_fallback = true`）不是一个裁决，不能被当作 keep；它会使 rerank fail-closed（见下）。
- **两个同名、语义相反的 `used_fallback` 字段。** 适配器**读取** `DecisionResult.used_fallback`——后端降级标记：为 `true`
  表示决策后端失败，裁决不可信，故 rerank fail-closed。适配器**写入** `MemoryRerankDecision.used_fallback`——RFC 0080
  定义的另一字段，含义是「因无有效选择而使用了内建粗排回退 rank」。两者同名却语义相反，适配器绝不能混淆。这一读一写
  正是上文「`abstain` 与后端失败的区别」规则背后的字段对。
- **`discarded_rank_count` 必须计算，不能写 0。** 适配器自行产生 rank、不走 RFC 0080 的 rank 归一化，因此必须显式上报该
  字段，取值为**未进入最终选择的粗排候选数**：`len(candidates) - len(selected)`。该口径**含回退情形**，此时 `selected`
  为 `1..min(limit, n)`。硬编码 `0` 是错误的，因为既有 listwise 策略为该字段记录的是真实计算值。
- **回退 rank。** 当没有任何候选被保留时，适配器回退到粗排 rank `1..min(limit, n)`，与 listwise 策略的内建回退、以及
  「仅在无有效 rank 时才回退」规则一致。
- **usage。** `MemoryRerankDecision.usage` 是单一的可移植 usage 值；适配器必须聚合逐候选 usage，而不是只上报其中一个。

## 失败策略分离

决策端口已经对其句柄套了一层 **fail-open 信封**：后端的任何非取消异常都被转换成带 `used_fallback = true` 的 `abstain`
裁决，而 `CancelledError` 仍被重抛，使取消得以传播。对咨询类消费者而言这是正确的：配置错误的咨询决策绝不能阻塞它所装饰
的写入或召回路径。

把该信封复用于 rerank 角色会构成缺陷。后端故障会被转换成 `abstain`，适配器会把它当作「keep」，从而静默返回粗排顺序——
正是 RFC 0080 所禁止的「把降级质量伪装成正常输出」（"They are not silently converted to coarse results"）。

因此本 RFC 引入**角色级运行时失败策略**，由消费者角色声明、由 composition 解析：

- 一个 `DecisionFailurePolicy`，取值 `fail_open` 与 `fail_closed`；
- rerank 角色为 `fail_closed`；
- 咨询类消费者保持 `fail_open`。

必须保持的不变量：

- 保留 fail-open 的**唯一实现点**。fail-open 仍只实现一次；fail-closed 角色只是**不**把其句柄包进它，而不是引入第二条
  降级路径。
- `except Exception` 降级与 `CancelledError` 穿透语义不变。
- 对所有未选择 fail-closed 的消费者，fail-open 仍是默认。
- 不引入新依赖；失败策略是标准库枚举。
- 依赖方向不变（Runtime 可依赖 Memory 家族；反之不行）。

分离发生在**包装层**，而非后端层。同一个决策后端实例可以同时服务两个角色：咨询/门控角色把其句柄包进 fail-open 信封，
而 rerank 角色使用**未包装**的句柄。正因如此，决策式 rerank **可以复用其它决策消费者所用的同一套决策后端配置**；按后端
设策略会自相矛盾，因为同一后端本就在合理地服务失败偏好相反的角色。真正需要满足的是：**启用决策式 rerank 却完全没有任何
可用决策后端**必须是启动配置错误（见下）。

## 装配期失败与运行时失败

这是两个正交维度，二者必须一并说明，以免把 fail-closed 误读为「无后端也能跑」：

- **装配期（composition 时）：** 启用决策式 rerank 却没有任何可用决策后端，是启动配置错误，与 RFC 0080 一致。
- **运行时：** 搜索过程中后端失败或超时，会被传播，而不会降级为粗排顺序。

因此 rerank 角色是「装配期 fail-fast，运行时 fail-closed」，而咨询类消费者是「装配期 fail-fast（仅在配置时），运行时
fail-open」。

## 成本记账

RFC 0080 的策略每次非空 rerank 搜索的成本是一次 generation 请求，搜索路径为此记录固定的 `generation_calls` 为 1。
决策式 rerank 的成本是**每个候选最多一次**决策请求，受候选上限约束。因此固定记账会低估决策式 rerank 的成本，并使其成本
不可观测。

本 RFC **固定**记账口径：被路由到决策式 rerank 的搜索必须记录**实际发生的决策调用次数**，在扫描过程中累加（在达到 limit
时提前 break 的情形只计入真正发生的调用；回退情形计入整轮扫描）。不接受任何其它口径——尤其是「一次逻辑 rerank 操作」，
因为它正是「发出 N 次却固定记 1」、掩盖真实成本的做法。

## 并发与一致性

每个候选都锚定到某个精确、不可变的 Memory Revision。只读搜索在调用决策模型时不持有 scope mutation lock，因此慢速决策调用
不会串行化同一 scope 内的并发搜索。并发的 Memory mutation 可能使某次搜索结果指向紧邻的上一个 Revision；该结果仍是精确且
有效的 citation，且不会跨 Revision 混合 hit 身份。这与 RFC 0080 的并发契约一致。

## 配置与兼容性

- 本 RFC**不**新增配置字段、**不**新增环境开关。决策式 rerank 通过应用组合注入一个 `MemoryReranker` 来启用，复用
  RFC 0080 的注入通路。默认必须让今天的行为逐字节不变：不装配 reranker、不发出决策调用、搜索返回粗排顺序。
- 不新增持久化表、projection、cursor 或 Artifact Revision；启用或关闭无需迁移，且不改变 embedding profile 身份。
- HTTP v1 响应不变；`openapi/powercontext.yaml` 与生成客户端不变。

## 可观测性

进程内 rerank trace 继续携带带版本号的 policy ID、精确的粗排候选 hits、被选中的原始 rank、丢弃/回退诊断以及可移植 usage。
HTTP v1 映射继续只返回最终 hits。决策策略自身的消费者标签是一个由 Runtime 共享的低基数常量，因此在 trace 与结构化日志中
一致出现。

# Drawbacks

- **成本与延迟。** 每个候选最多一次决策请求，明显比每次搜索一次 listwise 请求更贵。候选上限较大时，这是主要缺点。
- **丢失 listwise 上下文。** 逐候选问题无法一次看到全部候选，模型无法直接比较候选。不主张其选择质量优于 listwise 或优于
  粗排顺序。
- **多出一个角色失败策略。** 引入 `DecisionFailurePolicy` 为决策端口增加了一个概念。RFC 0080 的 fail-closed 要求使其有
  必要，但它仍是额外表面积。
- **后端未针对 rerank 调优。** 决策模型并非为重排而调优。部署不应把该策略读作质量升级。
- **数据敏感性。** 候选文本会发送给决策后端；trace 保留候选文本，须按普通搜索输出同等敏感地对待。

# Rationale and alternatives

**为什么要做。** rerank 接缝 provider-neutral，且本就是正确的组合点。具备决策后端、却无 listwise 生成行为的部署，得以在
不再采用第二套 listwise prompt 策略的前提下进行 rerank，且改动不触及持久化或公开 API。

**替代方案：为 rerank 角色复用既有的单一 fail-open 句柄。** 否决。RFC 0080 要求 rerank 阶段 fail-closed；把它包进
fail-open 信封会把后端故障静默地变成粗排结果。

**替代方案：把失败策略放在端口或后端实例上。** 否决。同一后端实例可能服务失败偏好相反的角色，因此按后端设策略自相矛盾；
而是否容忍降级，是「该角色在后端失败时应当怎样」的属性，不是后端能力的属性。把它钉在端口上还会迫使每个实现者声明它，
从而放大一个已冻结的契约。

**替代方案：让决策式 rerank 变成 listwise——一次向模型要多个 rank。** 否决。决策端口对每个请求只返回单一裁决；若不发明
新 schema，就无法从中读出稀疏 rank 子集，而那是超出本 RFC 范围的。

**替代方案：通过新增配置字段、或复活零消费者 `MemoryRerankMode` 枚举来选择实现。** 否决。RFC 0080 的注入通路已让部署
可以自行选择 `MemoryReranker` 实现，无需选择器；复活 `MemoryRerankMode` 会在既有 `memory_rerank_enabled` 布尔开关旁再
并出一套开关，而新增字段则会改动 RFC 0080 的配置契约。环境开关至多只是将来的一种便利（见 *Future possibilities*）。

**不做的后果。** 接缝维持单策略。没有 listwise 生成策略的部署无法 rerank，也没有办法以非 listwise 的**实现**来使用该接缝。

# Prior art

PowerContext 内部：RFC 0080 定义了 rerank 接缝、其身份保持规则、其默认关闭的部署策略，以及其 fail-closed 失败行为；
RFC 0014 定义了 Memory 的 hybrid retrieval 与 RRF 融合，即粗排池的来源。跨家族决策模型端口及其 fail-open 信封，是既有的
内部 Runtime 契约，本 RFC 组合它们而非替换它们。

作为通用检索范式，「先召回一个有界的第一阶段候选池，再在第二阶段从中重选」早已确立；此处的决策论变体——对候选做一串独立
的 keep/drop 判定——是该范式的直接实例。

本设计不以任何外部工具、库或框架为来源；其动机来自 PowerContext 自身的契约以及 provider-neutral 的重排实践。

# Unresolved questions

- **trace 暴露。** HTTP 客户端是否应能请求或接收一份脱敏的决策式 rerank trace（listwise 策略亦有此问题）？
- **部署指引。** 本 RFC 是否应要求文档化指引：若部署需要不依赖模型的搜索可用性，则必须保持 rerank 关闭？
- **范围外／后续。** 一个真正的 listwise 决策策略（若决策端口将来长出 rank-set schema）属于范围外，若推进应独立成 RFC。
  此处讨论之外、与本 RFC 无关的计划态适用性观测（plan-state applicability observation）不得搭本 RFC 的车。

# Future possibilities

- 可选的、以环境变量选择 rerank 实现的方式，纯粹作为便利。它对本 RFC 明确不是必需的（注入通路已能启用决策式 rerank），
  且若引入，不得在 `memory_rerank_enabled` 旁再并出两套开关。
- 专门的决策式 rerank usage 契约，以及对若干候选发出一次请求的批量决策调用。
- 面向 query 类别的候选上限与决策式 rerank 的延迟预算。
- 若出现更多 fail-closed 决策角色，将其泛化为共享的「角色失败策略」机制，对本文引入的 `DecisionFailurePolicy` 加以推广。
- 在端到端评估中分别对粗排池与最终选择打分，复用 listwise 策略已暴露的同一条 trace。

任何此类扩展都必须保持精确的 hit 身份、显式策略版本、默认关闭的兼容性，以及粗排池评估与最终选择评估的分离。
