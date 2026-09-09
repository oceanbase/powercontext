+ Proposal Name: `scope_source_discovery`
+ Start Date: 2026-09-08
+ RFC PR: [oceanbase/powercontext#1502](https://github.com/oceanbase/powercontext/pull/1502)

# 概要

扩展 `GET /v1/scopes`，提供 SQL 层的 Scope 发现和兼容分页。调用方通过 `query` 提供子串，并通过
`query_field` 明确指定一个搜索字段；可选精确过滤参数覆盖父 Scope、External Reference kind 以及 Binding
integration/kind。新增 `GET /v1/scopes/{scope_id}/sources`，分页读取一个 Scope 持有的公开 Source。

Scope discovery 可以选择 `scope_id`、`title`、`summary`、External Reference `value` 或 Binding `external_id`。
服务端持久化由应用统一规范化的搜索投影，再使用数据库方言对应的字面包含表达式，保证 SQLite 与 OceanBase
具有相同的 Unicode、大小写和特殊字符行为。

# 动机

`ScopeApplication` 当前生成不透明的 `scp_...` ID。调用方通常知道标题、仓库 URL 或工作区 Binding，而不是
随机 ID 的片段，因此只搜索 `scope_id` 无法通过仓库信息发现新 Scope。

让一个 `query` 隐式搜索全部字段也不合适：调用方无法判断命中原因，新增搜索字段会改变已有请求结果，关系
字段的授权与查询成本也不同。要求 `query_field` 可以稳定请求语义和 SQL 执行范围，同时保留统一集合接口。

选择 Scope 后，调用方能够创建 Source 或按完整身份读取 Source，但无法发现该 Scope 已有的 Source。Source
发现是读取既有子资源集合，因此在 `/v1/scopes/{scope_id}/sources` 上增加 GET。

# 使用说明

## 发现 Scope

假设一个不透明 Scope 的标题为 `PowerContext`，并具有 value 为
`https://github.com/oceanbase/powercontext` 的 `repository` External Reference。按标题搜索：

```http
GET /v1/scopes?query=powercontext&query_field=title&limit=50
```

```json
{
  "items": [
    {
      "scope_id": "scp_01hzy8m6yq8j3h7m3v5w2r9k1p",
      "title": "PowerContext",
      "summary": "PowerContext repository development",
      "parent_scope_id": null,
      "context_references": [],
      "external_references": [
        {
          "kind": "repository",
          "value": "https://github.com/oceanbase/powercontext"
        }
      ],
      "version": 1
    }
  ],
  "next_cursor": null
}
```

也可以明确搜索仓库引用：

```http
GET /v1/scopes?query=oceanbase%2Fpowercontext&query_field=external_reference_value&external_reference_kind=repository&limit=20
```

所有过滤条件按 AND 组合，`query` 只匹配 `query_field` 指定的一个字段。目标是 External Reference 或 Binding
时，文本条件和精确类型条件必须由同一条关系记录满足。

无参数 `GET /v1/scopes` 保留现有全量列表行为。提供搜索、关系过滤、`limit` 或 `cursor` 后进入分页模式。
`next_cursor` 非空时，调用方将其原样传入下一次请求，并保持相同 query、field、过滤参数和 limit。

## 遍历 Scope 的公开 Source

```http
GET /v1/scopes/scp_01hzy8m6yq8j3h7m3v5w2r9k1p/sources?limit=50
```

```json
{
  "items": [
    {
      "scope_id": "scp_01hzy8m6yq8j3h7m3v5w2r9k1p",
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
`content` Source，包括可读取的 `lineage_only` Source；不公开内部类型，也不带入 Parent、Context Reference
或 Subject Scope 中的 Source。

# 参考级说明

## Scope discovery 合同

| 参数 | 类型 | 必填 | 规则 | 示例 |
| --- | --- | --- | --- | --- |
| `query` | string | 条件必填 | 与 `query_field` 同时提供；最长 256 个 Unicode 字符 | `powercontext` |
| `query_field` | string enum | 条件必填 | 与非空 `query` 同时提供；一次选择一个字段 | `title` |
| `parent_scope_id` | string | 否 | 精确匹配直接父 Scope；不递归 | `scp_01hzy8m6yq8j3h7m3v5w2r9k1p` |
| `external_reference_kind` | string | 否 | Scope 存在该 kind 的 External Reference | `repository` |
| `binding_integration` | string | 否 | Scope 存在该 integration 的 Binding | `codex` |
| `binding_kind` | string | 否 | Scope 存在该 kind 的 Binding | `workspace` |
| `limit` | integer | 否 | 分页默认 50，范围 1–100 | `50` |
| `cursor` | string | 否 | 首次省略；随后传服务端不透明值 | `eyJ2ZXJzaW9uIjoxLC4uLn0` |

`query_field` 是封闭枚举：

- `scope_id`
- `title`
- `summary`
- `external_reference_value`
- `binding_external_id`

`query` 与 `query_field` 必须成对出现。缺少任一参数、枚举值未知、参数重复，或 `query_field` 配合空/纯空白
query，均返回 422。本期不提供 `any`，也不支持多字段 OR 搜索。

服务端去除 query 首尾空白，再执行 NFKC 和 Unicode casefold 后的连续字面子串匹配；不分词、不纠错、不做
语义搜索。`%`、`_`、`*`、反斜线和正则字符均按普通字符处理。

文本匹配与精确过滤按 AND 组合。External Reference 和 Binding 使用相关 `EXISTS`，避免一对多关系产生重复
Scope。`query_field=external_reference_value` 且提供 `external_reference_kind` 时，同一条 External Reference
必须同时满足 value 和 kind；Binding 文本与 integration/kind 同理。

## Scope 分页与兼容性

Scope 按 `scope_id` 升序执行 keyset 分页。无任何 query 参数的 `GET /v1/scopes` 保留现有无界结果；提供有效
query pair、精确过滤、`limit` 或 `cursor` 时进入分页模式，默认 50、最大 100。

`ScopePage` 增加可选 `next_cursor: string|null`。分页响应总是返回该字段；旧全量响应可以省略或返回 null。
签名 cursor 绑定 operation、调用方稳定身份、规范化 query、query field、精确过滤、limit、排序、最后一个
Scope ID 和过期时间，复用部署的 cursor secret 与 record-cursor TTL。cursor 不能跨调用方、字段、过滤条件、
limit 或接口使用；不合法返回 `400 invalid_cursor`，过期返回 `410 cursor_expired`。

Scope ID 随机生成且可搜索元数据可更新。本期提供 keyset consistency，不提供跨请求快照。数据与权限不变时，
匹配 Scope 恰好出现一次；并发创建或元数据更新可能改变后续页，需要最新目录的调用方应从第一页重新开始。

## SQL 实现与存储兼容

Scope repository 接收 query pair、精确过滤、keyset 边界和 `limit + 1`。过滤、排序和限制必须在 SQL 中完成，
不能先加载无界 Scope 列表再由 Python 过滤或分页。

应用定义唯一规范化函数：NFKC 后执行 Unicode `casefold()`，不分词、不删除标点。Scope、External Reference
和 Binding 写入路径持久化规范化结果，启动 migration 对历史记录回填：

| 表 | 原字段 | 搜索投影 |
| --- | --- | --- |
| `pc_scopes` | `scope_id` | `scope_id_search` |
| `pc_scopes` | `title` | `title_search` |
| `pc_scopes` | `summary` | `summary_search` |
| `pc_scope_external_references` | `value` | `value_search` |
| `pc_scope_bindings` | `external_id` | `external_id_search` |

原字段仍是事实来源，投影不进入公开响应。由应用生成投影可以避免数据库 generated column、`LOWER()` 和默认
collation 的差异。

SQL 方言 helper 对 SQLite 生成 `instr(normalized_column, :query) > 0`，对 OceanBase/MySQL 模式生成
`locate(:query, normalized_column) > 0`，两者都把 SQL 通配符当作普通字符。query 始终使用绑定参数。如果最终
使用 LIKE，必须由公共 helper 统一转义，并在两种存储合同测试中证明等价。

Repository 只构造 `query_field` 选择的一个谓词，不生成覆盖全部投影的固定 OR。主表字段使用直接条件，关系
字段使用相关 `EXISTS`，类型限制进入同一个子查询。选出一页 Scope 后，复用现有批量 loader 装配 Context
References 和 External References，不能产生 N+1。

字面包含通常无法使用普通 B-tree 前缀索引。SQL 下推仍能限制数据库到应用的传输和应用内存。只有查询计划
证明需要时，才增加以 `scope_id` 开头的跨存储 Binding 索引。

## Source List 合同

| 字段 | 值 |
| --- | --- |
| 方法和路径 | `GET /v1/scopes/{scope_id}/sources` |
| operationId | `list_sources` |
| 鉴权 | 既有 `path_scope_read_access` / `scope.read` |
| 响应 | `SourcePage` |

Query 参数为可选 `limit`（默认 50，范围 1–100）和不透明签名 `cursor`。`SourcePage` 包含必填的
`items: SourceRecord[]` 和 `next_cursor: string|null`。已存在 Scope 没有公开 Source 时返回空数组和 null；
Scope 不存在或不可见时沿用 403/404 策略。

只有已建立公开读取合同的 Source 类型进入集合；本期与精确 Get 一致，仅包含 `content`。列出
`lineage_only` Source 不会赋予生成资格。

Source 按 journal position 升序返回。第一页记录已提交高水位，后续页读取
`last_position < journal_position <= high_watermark`，新追加 Source 在新一轮遍历中可见。cursor 绑定 operation、
Scope、公开类型、排序、limit、调用方、高水位、最后位置和过期时间。

每页最多返回 limit 条，普通页 UTF-8 JSON 内容预算为 4 MiB。服务端不截断 Source；第一条超过预算时，可以在
既有 Source 大小限制内完整返回这一条。

## 授权与错误

Scope discovery 保留 `server.observe`，授权完成后才搜索元数据或 Binding，响应只包含 ScopeDescriptor。Source
List 保留 `scope.read` 和精确 Get 的可见性规则。每页重新鉴权，cursor 不能绕过授权变化。

| 状态码 | 含义 |
| --- | --- |
| 200 | 成功，包括合法空集合 |
| 400 | cursor 不合法、被篡改或上下文不匹配 |
| 401 | 缺少或无效凭证 |
| 403/404 | 既有授权和资源可见性策略 |
| 410 | cursor 过期 |
| 422 | query/field 未配对、过滤、limit、path 或重复参数不合法 |
| 503 | 必要持久化或运行时能力不可用 |
| 500 | 使用既有错误 envelope 的未预期错误 |

## OpenAPI 与客户端

`openapi/powercontext.yaml` 继续是事实来源。`list_scopes` 增加可选 `query`、`query_field`、
`parent_scope_id`、`external_reference_kind`、`binding_integration`、`binding_kind`、`limit` 和 `cursor`。
`query_field` 使用封闭枚举，请求模型执行与 query 的配对校验。`ScopePage` 增加可选 `next_cursor`；同时定义
`list_sources`、`ListSourcesRequest` 和 `SourcePage`。

重新生成 Python/TypeScript bindings；手写 Python client 保留无参 `list_scopes()` 并暴露 discovery 参数。
接口不写 Source、不推进 consumer cursor、不调用模型，也不创建 Artifact 或 Candidate。

# 缺点

规范化投影及启动 migration 增加存储和写入维护成本；字面包含仍可能扫描记录。旧无参 Scope 列表仍然无界，
Scope keyset 分页不是跨请求快照。返回完整 Source content 比摘要集合开销更大。

# 设计选择与替代方案

- 不固定只搜索 `scope_id`：当前不透明 ID 不承载仓库身份。
- 不让一个 query 默认搜索全部字段：命中来源和已有请求行为不稳定。
- 不为每个字段定义独立 query 参数：参数面较大，多字段同时出现还需要额外 AND/OR 规则；当前单字段场景用
  `query + query_field` 更清晰。
- 不在 Python 中过滤：会加载全量集合，也无法正确下推分页。
- 不依赖数据库 `LOWER()` 或默认 collation：SQLite 与 OceanBase 的 Unicode 行为不同。
- 不直接 JOIN 关系表：会产生重复 Scope 并破坏分页边界。
- 不增加独立 Scope Search：本需求过滤同一个授权集合，并复用同一响应模型。

# 既有设计

[RFC 1437](1437_source_artifact_rest_api.md) 定义 Source 身份、公开 Content Source、精确 Get 和
`lineage_only` 语义。本文增加 Scope 与 Source 集合发现，不改变这些语义。

# 未决问题

本期没有未决问题。跨字段相关性、拼写纠错、全文索引和返回 Binding 身份需要独立设计及更细授权评审。

# 未来可能

未来可以迁移旧无界 Scope 列表，增加根/子树或更新时间过滤、External Reference value 精确匹配，或者引入
专用全文索引。任何扩展都必须保持 cursor 上下文、授权以及 SQLite/OceanBase 语义一致。
