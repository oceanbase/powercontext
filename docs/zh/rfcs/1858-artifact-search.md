---
title: 统一制品检索与能力契约
---

- Proposal Name: `artifact_search`
- Start Date: 2026-10-07
- RFC PR: [oceanbase/powercontext#1858](https://github.com/oceanbase/powercontext/pull/1858)

# Summary

为同一 Scope 内的单个 Artifact Family 提供统一 `search` 接口，返回按相关性排序、受 `limit` 限制的 Artifact
数组。公共层约定调用方式和基础行为，各 Family 按需声明检索能力并负责业务实现。现有业务专用接口为兼容性
继续保留，已接入 Family 的专用接口内部改为调用统一检索方法。

# Motivation

`ListActiveEntries` 缺少针对查询的相关性排序，结果在预算不足时被截断，可能丢掉最相关的记忆。调用方需要按查询
选取前 N 个相关制品。同时，各制品分散的检索入口增加了接入成本；新增 Family 应能通过注册接入统一接口。

# Guide-level explanation

调用方使用 `POST /v1/scopes/{scope_id}/artifacts/{family}/search`，例如提交：

```json
{
  "query": "数据库连接超时的处理经验",
  "limit": 5
}
```

响应的 `results` 是按相关性排序的 Artifact 数组，最多包含 5 项；每项内容由对应 Family 定义，没有匹配项时返回
空数组。`query` 必须非空，省略检索模式时使用 Family 的默认行为。

调用方还可使用该 Family 声明支持的模式、过滤条件、相关性准入、融合配置、评分阈值或重排能力。未知 Family、
不支持搜索或不支持所请求能力时，接口给出明确错误；运行失败不作为空结果返回。

每次请求检索一个 Family，只提供 `limit`，不提供分页。多制品编排和上下文预算分配继续使用 `prepare`。

# Reference-level explanation

- **能力分派**：Family 注册检索能力、参数约束和业务实现。公共入口按注册信息解析、分派请求，Family 负责专用
  参数及默认策略，框架不强制 `auto`，新增 Family 无需修改公共入口的分支逻辑。
- **能力范围**：鼓励支持全文、向量、融合、过滤、准入、评分阈值和重排；Family 按需支持并公开可用组合。显式请求
  不支持的能力必须报错，不能忽略参数或静默切换策略。
- **评分契约**：检索评分必须为有限的 `[0, 1]` 数值，越大表示评分越高。支持 `min_score` 时严格按归一化检索分数
  筛选，重排或补足数量不能绕过门槛。归一化不承诺跨 Family、算法或查询的绝对可比性。
- **融合职责**：融合算法独立于制品，复用通用实现；Family 提供召回通道、候选和权重。算法专用参数随所选算法配置，
  已声明的相关性准入不因切换融合算法而改变。
- **兼容与接入**：现有业务专用接口继续保留，保持既有请求、响应及默认行为。已接入 Family 的专用接口必须调用
  统一检索方法，通过参数和结果适配保持兼容，与新 HTTP 入口、SDK、`prepare` 共用同一业务检索实现。
  首批覆盖 Topic Memory、Experience 和 Skill；现有 Memory entry 检索保留原入口。Prompt 不纳入，
  Profile、Handoff 不为统一接口补充搜索能力，未提供搜索时明确报错。

Family 契约遵循 [RFC #1549](1549_artifact_family_unification.md)，数据库迁移遵循
[RFC #1771](https://github.com/oceanbase/powercontext/pull/1771)。
[RFC #1803](https://github.com/oceanbase/powercontext/pull/1803) 是相关的检索实现提案，不作为统一接口的前置条件。
Atomic Memory 接入依赖并遵循 [RFC #1809](https://github.com/oceanbase/powercontext/pull/1809)，在该提案采纳且
对应 Family 就绪后接入，不阻塞其他 Family。

# Drawbacks

- 各 Family 的支持范围和默认策略仍有差异，需要持续维护能力说明，调用方也需要了解这些差异。
- 统一评分范围不能消除算法差异；调整检索配置可能影响阈值效果。既有入口的适配也需要处理兼容成本。

# Rationale and alternatives

- 在 `list` 上加入相关性检索，会混合列表浏览与查询检索。独立 `search` 更明确地表达前 N 条相关结果。
- 继续为各 Family 新增独立实现的专用接口，会重复接口适配和检索逻辑；统一入口与统一检索方法降低这种维护成本。
- 要求所有 Family 实现全部能力，会给简单检索增加不必要的索引或模型依赖，因此采用能力声明与按需支持。
- 一次搜索所有制品并强制统一模型重排，会增加跨制品比较和推理成本，多制品编排继续由 `prepare` 承担。

# Prior art

PowerContext 已有 `/v1/memory/search`、`/v1/topic-memory/search` 等专用接口。这些接口为兼容性继续保留；
已接入 Family 的专用接口必须改用新的统一检索方法，适配既有请求和响应，不再维护独立的检索逻辑。

# Unresolved questions

首批各 Family 开放的能力组合、默认策略和专用接口的适配映射，需在方案设计中逐项明确。完整请求与响应结构、参数模型、
评分公式和后端执行方案留在后续方案设计中。

# Future possibilities

后续可增加能力发现接口和更多融合算法，减少调用方了解能力范围的成本。跨制品评分校准可单独研究，
不作为本提案的前置条件。
