---
title: 跨 Scope 查询
description: 解析有界结构范围，并在多个已授权来源中检索同一个 Artifact 家族。
---

# 跨 Scope 查询

普通[Artifact 检索](search-artifacts.md)只读取一个 Scope。项目经常需要它自己拥有的多个 Scope 中的材料，或由他方
维护的团队库。本页说明两个受支持的部分：解析**有界结构范围**，以及在一个家族中**检索多个显式声明的来源**，
并如实报告实际检索了多少范围。

两者都不授予权限。每个来源都会按其当前的 `scope.read` 单独授权，属于某个范围并不意味着可以读取该 Scope 的内容。

## 解析结构范围

`parent_scope_id` 定义了单父 Scope 树。`POST /v1/scopes/selection/resolve` 已经返回选择命中的描述符。结构成员就是
这些父边，仅此而已：

- 一个 Scope 到另一个 Scope 的 **Context Reference** 不会加入被引用的 Scope，因此引用团队运行手册的项目不会
  因此把该运行手册纳入自己的范围。
- 发布副本、Artifact lineage 和证据链接都不会扩展范围。如果只想指定某一个精确修订，请使用 `ArtifactAddress`。
- `mode: "exact"` 只包含点名的 Scope；`mode: "subtree"` 是根及其结构后代；`mode: "all"` 属于全量观察，
  不是结构范围。

运行时解析器还会执行显式的 `max_nodes` 和 `max_depth` 预算。范围超出预算时，服务会**拒绝**，而不是返回被截断的集合，
这样调用方就不会把部分成员当作完整成员。解析只读取该分支的结构边，而不是所有 Scope，因此工作量与传输行数取决于分支
规模；`parent_scope_id` 没有专用索引，所以剩余延迟仍会随存储的 Scope 总量增长。需要更宽范围时应收窄根，而不是提高预算。解析结果会给出每个成员的元数据
`version` 和一个成员摘要；只要有成员被挂接、移动或更新，该摘要就会变化——这正是生命周期操作在确认动作前检测集合
变化所依赖的依据。

## 检索已声明的来源

使用 `POST /v1/scopes/{scope_id}/artifacts/search:cross-scope`。路径中的 Scope 是调用方自己的工作 Scope，
`scopes` 中的每一项是一个来源。

```http
POST /v1/scopes/orders/artifacts/search:cross-scope
Content-Type: application/json

{
  "family": "experience",
  "payload": {"query": "release rollback", "limit": 10},
  "scopes": ["scp_interface", "scp_operations"],
  "coverage": "complete"
}
```

每个来源都会单独授权。结果按**每个来源自身的排名**合并，并以声明的来源顺序作为稳定的并列顺序。`retrieval_score`
只属于它自己的来源，不能跨来源比较。每条命中都带有完整的 `ArtifactAddress`，因此在两个 Scope 中本地 ID 相同的
Artifact 仍然是两个不同的输入。

`payload` 是所选家族自身的检索请求，原样转发。本操作不解释它：由家族负责校验、维护默认值并报告自身错误。

## 预算与覆盖

| 字段 | 类型与默认值 | 含义 |
| --- | --- | --- |
| `limit` | 整数，默认 `10` | 所有来源合并后的最大结果数。 |
| `max_scopes` | 整数，默认 `8` | 本请求接受的最大来源数量。 |
| `max_candidates` | 整数，默认 `64` | 本操作注入给家族的每来源窗口，按声明的来源均分。它是家族有效窗口的上限，绝不会扩大已声明或默认的 `limit`。 |
| `coverage` | `complete` 或 `partial`，默认 `complete` | 必需的来源失败时是否让整个查询失败。 |

`max_candidates` 只是上限，因此不会把已声明的 top-1 请求变成更宽的请求，也不会把无效原值替换成有效值。如果预算不足以
覆盖家族的有效窗口，查询会被**拒绝**，而不是静默地少检索。

它约束的是交给每个来源的窗口，而不是家族内部的检索工作量。启用重排的家族可能检查比该窗口更多的候选，
覆盖信息仍会如实报告窗口被收窄，因此收窄后的检索不会被当作完整覆盖。

由于预算按来源均分，可用的来源数量取决于所请求的窗口。使用默认值（`limit` 10、`max_candidates` 64）时最多只能完整
检索**六个**来源；两个来源上各 10 条的窗口需要 `max_candidates` 至少为 20，而七个及以上来源在提高
`max_candidates`、降低每来源窗口或接受 `coverage: partial` 之前都会被拒绝。

响应会说明哪些来源作出了应答：

| 结果 | 含义 |
| --- | --- |
| `coverage.complete: true`、`reason: complete` | 每个声明的来源都被完整检索。 |
| `reason: origin_failure` | 至少一个必需来源被拒绝、不可用或不存在；`failed_origins` 会列出它们。当 `coverage: complete` 时，整个查询直接失败。 |
| `reason: candidate_budget` | 某个来源在收窄的窗口下被检索；`bounded_origins` 会列出它们，因此结果不会被当作完整覆盖。 |

## 精确打开一条命中

打开命中有两种读取精确修订的方式。

`GET /v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}` 是普通的 HTTP 精确修订读取。
对于正文由其 Artifact 拥有的家族，它要求该逻辑 Artifact 上的 `artifact.read`，并不要求所在 Scope 上的
`scope.read`。只有由 Scope 拥有的 Topic Memory 家族才改为从 Scope 读取，因此 `scope.read` 就是它的全部要求。

`open_hit` 是 Runtime 查询对象上的方法，不是 `PowerContextClient` 的 HTTP 方法。它的要求更高而不是更低：
保留来源上的 `scope.read` 作为前置条件，随后要求同一个精确 Artifact 上的 `artifact.read`。因此，只有
单个 Artifact 授权而没有任何 Scope 绑定的调用方可以通过 HTTP 读取该修订，但会被 `open_hit` 拒绝。
两条路径中正文由 Artifact 拥有的家族都是 `experience`、`skill`、`profile`、`handoff`、`prompt` 和
`atomic-memory`；Topic Memory 由 Scope 拥有。
两条路径都不会回退到当前 head，因此即使来源发布了更新的修订，早期命中仍然指向当初找到的那个修订。
读取结论与读取原始证据是相互独立的权限：可以使用某个 Scope，并不意味着可以读取其中 Artifact 的正文，
而持有某个 Artifact 的授权也并不意味着可以检索该 Scope。

## 限制

- 限定到某个 Artifact 或修订（`restrict_to`）会被拒绝，因为可检索投影只索引当前 head，候选无法在打分前被限制。
- 本接口只读取；它不生成、不评审、不发布任何内容，也不跨 Scope 维护结论。
- 跨 Scope 检索可通过 HTTP 和 Python SDK 使用。它不是 MCP 工具；MCP 保留用于跟进命中的精确读取工具。
