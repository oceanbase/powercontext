+ Proposal Name: `scope_source_discovery`
+ Start Date: 2026-09-08
+ RFC PR: [oceanbase/powercontext#1502](https://github.com/oceanbase/powercontext/pull/1502)

# 概要

扩展 `GET /v1/scopes`，增加可选 `query` 参数，按 `scope_id` 的字面子串筛选 Scope descriptor；新增
`GET /v1/scopes/{scope_id}/sources`，分页读取一个 Scope 持有的公开 Source。

两项能力复用现有 Scope 目录、Source journal、公开 `SourceRecord` 序列化、签名游标和授权边界，不增加
数据表，不执行生成，也不推进任何领域 consumer cursor。

# 动机

调用方经常只知道仓库类 Scope ID 的一部分。当前 Scope 集合返回所有 descriptor，客户端只能下载全量后
自行过滤。选择 Scope 后，调用方能够创建 Source 或按完整身份读取 Source，但无法发现该 Scope 已有的 Source。

Scope ID 匹配仍是现有 Scope 集合的投影，因此应扩展 `GET /v1/scopes`，而不是增加独立搜索动作。Source
发现是读取既有子资源集合，因此在现有 `/v1/scopes/{scope_id}/sources` 上增加 GET。

# 使用说明

## 按 ID 查找 Scope

```http
GET /v1/scopes?query=powercontext
```

```json
{
  "items": [
    {
      "scope_id": "git:github.com/oceanbase/powercontext",
      "title": "PowerContext",
      "summary": "PowerContext repository development",
      "parent_scope_id": null,
      "context_references": [],
      "external_references": [],
      "version": 1
    }
  ]
}
```

响应继续使用完整 `ScopePage`，不增加只返回 ID 的简化结构。不传 `query`，或传空字符串、纯空白字符串时，
保持现有全量行为；没有匹配项时成功返回空 `items`。

## 遍历 Scope 的公开 Source

```http
GET /v1/scopes/git%3Agithub.com%2Foceanbase%2Fpowercontext/sources?limit=50
```

```json
{
  "items": [
    {
      "scope_id": "git:github.com/oceanbase/powercontext",
      "source_type": "content",
      "source_id": "src_example_0001",
      "content": {"decision": "Keep the public API stable."},
      "position": 1,
      "content_digest": "sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    }
  ],
  "next_cursor": null
}
```

每个 item 复用精确 Source Get 的 `SourceRecord`，包括适用的 `receipt_identity` 等可选字段。集合只包含公开
`content` Source，包括可读取的 `lineage_only` Source；不公开内部 Source 类型，也不递归带入 Parent、
Context Reference 或 Subject Scope 中的 Source。

`next_cursor` 非空时，调用方将其作为不透明值传入下一次请求，并保持相同 `limit`。`scope_id` 必须整体编码
为一个 path segment。

# 参考级说明

## Scope query 合同

`GET /v1/scopes` 接受一个可选 `query` 字符串，最长 256 个 Unicode 字符。服务端去除首尾空白后：

- 只匹配 `scope_id`，不匹配 title、summary、引用、Source 或 Artifact 内容；
- 区分大小写，执行连续字面子串匹配；
- `%`、`_`、`*`、反斜线和正则字符均按普通字符处理；
- 保持既有 `scope_id` 确定性排序；
- 返回全部匹配 descriptor，不增加分页。

缺省、空字符串或纯空白字符串表示不筛选。重复 `query` 参数无效。为避免现有无参客户端被静默截断，本期
不修改 ScopePage，也不增加 Scope 分页；未来的 Scope 分页迁移需要独立兼容性设计。

operationId 继续为 `list_scopes`，响应继续为 `ScopePage`，鉴权继续使用 `server.observe`。

## Source List 合同

| 字段 | 值 |
| --- | --- |
| 方法和路径 | `GET /v1/scopes/{scope_id}/sources` |
| operationId | `list_sources` |
| 鉴权 | 既有 `path_scope_read_access` / `scope.read` |
| 响应 | `SourcePage` |

GET 没有 request body。Query 参数为：

| 参数 | 规则 |
| --- | --- |
| `limit` | 可选整数，默认 50，范围 1–100 |
| `cursor` | 可选不透明签名字符串，最长 4096 字符 |

`SourcePage` 包含必填的 `items: SourceRecord[]` 和 `next_cursor: string|null`。已存在 Scope 没有公开 Source
时返回 `200`、空数组和 null cursor。Scope 不存在或不可见时沿用 403/404 策略，不伪装成空集合。

只有已建立公开读取合同的 Source 类型进入集合；本期与精确 Source Get 一致，仅包含 `content`。列出
`lineage_only` Content Source 不会赋予生成资格，既有 `source_not_eligible` 不变量保持不变。

## 排序与快照边界

Source 按 `pc_sources.journal_position` 升序返回，对应 API 的 `position`。第一页记录 Scope 已提交的 Source
journal 高水位，后续页只读取：

```text
last_position < journal_position <= first_page_high_watermark
```

第一页之后追加的 Source 不进入本轮遍历，重新从第一页读取时才可见。这样无需跨 HTTP 请求持有数据库事务，
也能获得追加隔离。

签名 cursor 绑定 operation、Scope ID、公开 Source 类型集合、排序、limit、调用方身份、首次高水位、最后返回
位置和过期时间，不能跨用户、Scope、接口或 limit 复用。生命周期复用 Server 已配置的 record cursor TTL。
篡改或上下文不匹配返回 `400 invalid_cursor`，过期返回 `410 cursor_expired`。

每页最多返回 `limit` 条，普通页 UTF-8 JSON 内容预算为 4 MiB。达到预算时允许提前结束本页，但不截断单条
Source；若第一条本身超过预算，则在既有 Source 大小限制内完整返回这一条。

## 持久化与复用

不需要 schema migration：

| 行为 | 既有权威数据或能力 |
| --- | --- |
| Scope descriptor 与顺序 | `pc_scopes`、`pc_scope_context_references`、`pc_scope_external_references` |
| Source 身份与内容 | `pc_sources` 和已注册 Source adapter |
| Source 顺序与高水位 | `pc_sources.journal_position` 和既有 Source repository |
| Cursor 完整性 | 既有 HMAC cursor codec 与部署 secret |
| 鉴权 | 既有 Scope list 与 path Scope read resolver |

Scope 筛选扩展 `ScopeApplication` 及其 repository，不使用由 Source/Artifact 表拼装的内部 `ScopeSummaryPage`。
Source List 复用 Create 和精确 Get 的 adapter-aware 转换，不直接返回数据库 payload。

该接口完全只读：不复制 Source，不推进 `pc_source_cursors`，不调用模型，不创建 Artifact 或 Candidate。

## OpenAPI 与客户端

`openapi/powercontext.yaml` 继续是事实来源。合同为 `list_scopes` 增加 `query`，新增 `list_sources`、
`ListScopesRequest`、`ListSourcesRequest` 和 `SourcePage`，并重新生成 Python/TypeScript 操作元数据。手写 Python
客户端让 `list_scopes` 的 `query` 保持可选，从而兼容原无参调用，同时增加类型化 `list_sources()`。

## 错误

| 状态码 | 含义 |
| --- | --- |
| 200 | 查询成功，包括合法空集合 |
| 400 | Source cursor 无效、被篡改或上下文不匹配 |
| 401 | 缺少或无效凭证 |
| 403/404 | 沿用既有授权和资源可见性策略 |
| 410 | Source cursor 过期 |
| 422 | query、path、limit、重复参数或 cursor 语法不合法 |
| 503 | 必要持久化或运行时能力不可用 |
| 500 | 未预期错误，使用既有错误 envelope |

# 缺点

字面子串匹配通常不能利用普通 B-tree 前缀索引；为兼容现有客户端，Scope 接口仍然无界。返回完整 Source
content 比摘要列表开销更大。快照分页和字节预算也增加了 cursor 与响应组装复杂度。

# 设计选择与替代方案

- 不增加独立 Scope Search：本需求只是过滤同一个授权集合，并复用同一响应类型。
- 不增加 `/sources/{source_type}`：调用方按所属 Scope 发现 Source，且当前公开类型只有 `content`。
- 不无界返回全部 Source：大量或较大的 content 可能耗尽内存和响应预算。
- 不只返回摘要：否则需要 N+1 次精确 Get，不能满足读取全部 Source 信息的目标。
- 不公开全部内部 Source 类型：其 payload 没有稳定公开 schema，也可能包含仅供实现使用的数据。

# 既有设计

[RFC 1437](1437_source_artifact_rest_api.md) 定义 Source 身份、公开 Content Source、精确 Get 和
`lineage_only` 语义。本文只增加集合发现，不改变这些语义。Artifact 与 Artifact Revision List 已有的签名
cursor 约定由本接口复用。

# 未决问题

本期没有未决问题。增加公开 Source 类型、Scope 分页、元数据搜索和面向导出的接口需要独立设计。

# 未来可能

未来可通过 RFC 增加兼容的 Scope 分页、title/summary 搜索、Source 类型筛选、摘要投影或异步导出。任何扩展
都必须与精确 Get 保持鉴权一致，并避免泄漏内部 payload。
