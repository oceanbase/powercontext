- 提案名称：`bounded_memory_entry_pagination`
- 开始日期：2026-09-28
- 跟踪 Issue：[oceanbase/powercontext#1656](https://github.com/oceanbase/powercontext/issues/1656)
- 关联工作：[#1657](https://github.com/oceanbase/powercontext/issues/1657)、
  [#1709](https://github.com/oceanbase/powercontext/pull/1709) 和
  [#1718](https://github.com/oceanbase/powercontext/issues/1718)
- 状态：核心设计原则已达成共识；实现进行中

# 摘要

PowerContext 新增一个附加的、工作量有界的 Memory entry 目录查询。现有全量
`list_memory_entries` 保持不变。新操作按 `entry_id ASC` 返回精确身份和紧凑元数据，不加载
entry body，并使用签名的不透明 cursor 续页。

首页固定一个不可变 Memory revision，后续页在同一 revision 上查询可重建的 revision-valid 目录索引。
带 tag 过滤的分页还会绑定独立 tag generation；tag 变化后续页显式失效并要求重启。

# Part A：SPEC

## 1. 背景与问题

当前 Memory list 路径会解析最新 Artifact，解码完整 manifest，加载所有 entry-version 行，然后才做展示和 tag 过滤。
即使调用方只需要一小页，数据库、manifest 解码和 entry body 展开工作量仍随整个 Memory 增长。
只在 HTTP 层切片无法解决这个问题。

## 2. 目标行为

新增 `POST /v1/memory/entries/query`，operation ID 为 `query_memory_entries`。

请求包含 `scope_id`、默认为 `false` 的 `include_inactive`、现有可选 `tag_filter`、默认 50 且范围为
`1..100` 的 `limit`，以及可选不透明 `cursor`。

响应包含固定的 `memory_ref`（Scope 无 Memory 时为 `null`）、按 `entry_id ASC` 排序的 `items` 和
`next_cursor`。每项只包含精确 citation（`memory_ref`、`entry_id`、`entry_version_id`）、正整数 `version`、
`kind` 和 `state`；不包含 `text`、`source_refs` 或 `artifact_refs`。body 由现有 exact-detail 操作获取。

现有 `list_memory_entries` 的请求、响应、排序和行为均不改变。

## 3. 主要场景

### 无过滤遍历

1. Server 认证调用方并对 Scope 授权。
2. 首页解析并固定当前 Memory revision；无 Memory 时返回 null reference、空 items 和空 cursor。
3. 从 page key 之后读取符合条件的紧凑目录行，同时限制 item 数和字节数。
4. 存在后续行时，签名 cursor 从最后一个已输出 `entry_id` 继续。
5. 每个后续页都重新授权，并读取同一固定 revision。并发 Memory commit 对本次遍历不可见。

### 生命周期与 tag 过滤

revision 有效性、lifecycle state 和 tag predicate 必须在 keyset paging 和 item limit 之前应用。
tag-filtered 首页捕获当前 tag generation；后续页在有界读取前后都验证 generation。有效 Memory-entry tag 变更
返回 `400 invalid_cursor` + `tag_state_changed`，调用方从首页重启。无操作 replacement 不推进 generation，无 tag cursor
不依赖 generation。这是失效/重启，而不是 tag snapshot 保证。

### 未完成索引迁移

在认证和授权后，新操作返回 `503 memory_query_index_unavailable`，绝不 fallback 到完整 manifest。
旧 list、exact detail、search 和 Memory write 在维护窗口外仍可用。运维者对 SQLite/OceanBase 执行显式离线
`plan`/`apply`/`verify`；只有 verify 成功才标记 ready，apply 中断可恢复且不改写权威 Memory 数据。
在维护窗口外，即使历史 backfill 尚未完成，新 Memory commit 仍维护自身 directory delta；但在完整 verify 成功前，
新 query 始终不可用。

### 页字节上限

Server 以固定 4 MiB 预算计量编码后 directory item payload。如果下一项超过 item limit 或字节预算，则在它之前停止。
合法 v1 字段都已有上限；若损坏数据或未来不兼容形状造成单项无法装入，返回
`413 memory_directory_item_too_large`，不返回部分页，也不推进 cursor。

## 4. 规则与不变量

| ID | 规则或不变量 | 违反时结果 |
|---|---|---|
| MEM-PAGE-01 | 新操作是附加能力；旧全量 list 不变。 | 兼容性回归。 |
| MEM-PAGE-02 | 一次遍历固定在一个不可变 Memory revision。 | cursor 无效或实现缺陷。 |
| MEM-PAGE-03 | 先过滤，再按 `entry_id ASC` 分页。 | 遗漏、重复或顺序回归。 |
| MEM-PAGE-04 | 每项保留精确 Memory/entry/entry-version 身份。 | citation 完整性错误。 |
| MEM-PAGE-05 | directory read 不解码 body，不展开 body reference。 | 有界工作量合同失效。 |
| MEM-PAGE-06 | cursor 绑定 endpoint、Scope、filter、limit、order version、revision 和必要的 tag generation。 | `400 invalid_cursor`。 |
| MEM-PAGE-07 | 每页先授权，后暴露私有存在性或 readiness。 | 访问控制缺陷。 |
| MEM-PAGE-08 | 有效 tag 变更原子推进 generation；无操作变更不推进。 | tag 事务回滚。 |
| MEM-PAGE-09 | tag generation 变更只使 filtered continuation 失效。 | `invalid_cursor` + `tag_state_changed`。 |
| MEM-PAGE-10 | query index 是可重建派生状态；Artifact/manifest/version/tag 仍是权威。 | verify 失败。 |
| MEM-PAGE-11 | directory delta 与 Memory 原子提交，且与 changed entry 数成比例。 | 整个事务回滚。 |
| MEM-PAGE-12 | 未 verify 的 index 不服务且不触发无界 fallback。 | `503 memory_query_index_unavailable`。 |
| MEM-PAGE-13 | 每页最多 100 项、4 MiB encoded item payload。 | 在下一项之前结束。 |
| MEM-PAGE-14 | 单个超预算 item 不能产生部分成功。 | `413 memory_directory_item_too_large`。 |

## 5. 失败、重试和恢复

- 格式错误、篡改、跨 endpoint/Scope 或请求不匹配 cursor 使用现有 `400 invalid_cursor`；过期 cursor 使用
  `410 cursor_expired`。
- `tag_state_changed` 应从首页重启；`memory_query_index_unavailable` 可在 migration verify 后重试。
- 认证/授权错误优先于 readiness 和 Memory 存在性。
- oversized item 不返回 item 和替换 cursor。
- Memory/tag write 与对应 index/generation delta 一起 commit 或 rollback。
- migration apply 保存有界 checkpoint，可恢复；只有 verify 能标记 complete。

## 6. 并发与资源约束

- Memory revision 提供稳定跨页 view；tag 不带 revision，因此采用 invalidation，不声称 snapshot。
- tag query 前后检查 generation；续页边界是最后已输出 `entry_id`，而不是 lookahead row。
- persistence 每页最多 materialize `limit + 1` 个紧凑 eligible row，不反序列化 body JSON。
- 增量计量 item encoding，上限 4 MiB；envelope 和 cursor 另外有界。
- 对 200/1,000/5,000 entry 记录 query plan 和实测工作量；区分 returned/materialized row 与 examined row，不夸大数据库保证。
- Memory commit 只更新 pointer/state 已变的 validity row；未变 search projection 保持 #1709 的 delta 性质。
- backfill 使用正 batch size 和持久 checkpoint；startup 不执行无界 rebuild。

## 7. 非目标

本 PR 不改动 `list_memory_entries`，不在 directory 中返回 body，不实现 #1657 history pagination，不实现
#1321/#1718 的容量、保留、分裂/路由、压缩或物理删除，不 snapshot 全部 tag，不修改 search/exact-detail/citation 语义，
不让派生行成为权威，也不支持离线 migration 窗口中混用旧/新 writer。

## 8. 验收标准

| 义务 | 可观察证据 | 层级 |
|---|---|---|
| 附加合同 | 新 OpenAPI operation 存在；旧 schema 兼容。 | 合同/生成测试 |
| 稳定遍历 | 并发写不遗漏、不重复 pinned row。 | Runtime/HTTP 测试 |
| 紧凑精确身份 | item 可由 exact detail 解析，且无 body field。 | Runtime/mapping 测试 |
| 先过滤后分页 | lifecycle/tag 从 eligible row 填页。 | persistence 测试 |
| tag 重启边界 | 有效变更失效；no-op 和 unfiltered cursor 不失效。 | tag/并发测试 |
| 每页授权 | 权限撤销后续页失败且无信息泄露。 | HTTP access 测试 |
| 有界读和响应 | `limit + 1`、无 body decode、count/byte 上限和原子 413。 | instrumented persistence/HTTP 测试 |
| delta write | 只有 changed entry 修改 directory/search projection。 | persistence 测试 |
| 安全 migration | fresh/incomplete/interrupted/resumed/verified/corrupt 状态符合 gate。 | SQLite/OceanBase migration 测试 |
| 规模行为 | 200/1,000/5,000 证据包含 plan、row、byte、write delta 和 batch work。 | 可复现 report |

# Part B：设计报告

## 1. 最终数据流

```text
OpenAPI query_memory_entries
  -> Server 认证和 Scope 授权
  -> ScopedMemoryApplication.query_directory
  -> relational directory authority
       -> readiness 和 pinned revision 验证
       -> 可选 tag generation 验证
       -> revision-valid filter + keyset + lookahead query
       -> tag generation 再验证
       -> item-byte budget 和 signed continuation
  -> Server response mapping
```

Memory commit 复用现有事务，比较 previous/current manifest map 后只更新 changed entry 的 validity row。
Tag replacement 仍由 `RelationalTagService` 拥有；有效 Memory-entry tag delta 在现有 owner-Artifact 串行化下推进专用 generation。

## 2. Authority 与 seam

| 规则或状态 | Authority | Interface | 证据 |
|---|---|---|---|
| wire 形状 | `openapi/powercontext.yaml` | `query_memory_entries` | schema/generation 测试 |
| 授权顺序 | Server application | 现有 Scope authorization | access 测试 |
| directory 语义和 budget | Memory runtime/persistence | `query_directory` | persistence/runtime 测试 |
| cursor 签名和 TTL | `SignedCursorCodec` | exact-context encode/decode | cursor 测试 |
| revision-valid state | relational Memory persistence | directory validity table | database 测试 |
| exact body/version | Memory Artifact 和 entry-version table | 现有 exact read | citation 测试 |
| 可变 tag | `RelationalTagService` 和 tag table | predicate + generation | tag 测试 |
| readiness/checkpoint | query-index migration module | plan/apply/verify | migration 测试 |
| 错误展示 | Server error mapping | 稳定 status/code/details | HTTP 测试 |

Server 不重新实现 pagination，runtime 不推断 authorization，cursor codec 只认证 context，不拥有 query 语义。

## 3. 持久化机制

新派生 directory table 按 Scope/Memory Artifact 存储 `entry_id`、精确 `entry_version_id`、`state`、包含起点的
`valid_from_revision` 和不包含终点的可空 `valid_to_revision`。索引支持受 revision-valid predicate 限制的
`entry_id` keyset 遍历。`version`/`kind` 从权威 entry-version row 索引 join，不选择或解码 body JSON。

在 revision `R`，changed entry 以 `valid_to_revision = R` 关闭前一行，并以 `valid_from_revision = R`
插入新 pointer/state；new entry 只插入；unchanged entry 不写。

签名 cursor 仅包含 schema/order version、endpoint、Scope、规范化 filter、limit、pinned Memory Artifact ID/revision、
可选 tag generation、exclusive `after_entry_id` 和 expiry。

小型 tag-generation table 为每个 Scope/Memory Artifact 保存单调 generation，仅在 `RelationalTagService.replace`
对 `memory_entry` 产生有效规范化 delta 时推进。

feature-scoped marker 保存 query-index schema version、readiness 和可恢复 checkpoint。新数据库初始为 complete；现有数据库在离线
verify 确认派生覆盖率和精确身份前保持 incomplete。

## 4. 已接受的取舍

- body 需要 exact-detail 往返，以换取真正有界的 directory。
- tag 变化后重启 filtered traversal，不保留无界 snapshot。
- 现有数据库仅为这一新特性显式离线迁移。
- revision-valid row 比 current-head table 多占存储，但避免 per-revision manifest copy。
- 首版排序固定；新排序需要独立版本化 index/cursor 合同。

## 5. 义务到测试映射

| 义务 | 拥有层 | 测试 |
|---|---|---|
| 公开兼容性 | OpenAPI | schema/generation 和 SDK 测试 |
| revision-valid selection | directory persistence | SQLite/OceanBase keyset 测试 |
| 原子 delta maintenance | Memory commit | rollback 和 row-delta 测试 |
| tag generation | tag persistence | effective/no-op/concurrent mutation 测试 |
| cursor binding | cursor/query seam | mismatch/tamper/expiry 测试 |
| 授权先于暴露 | Server | HTTP/MCP access 测试 |
| byte behavior | query authority/Server | boundary 和 413 测试 |
| readiness | migration module | fresh/incomplete/resume/verify/corrupt 测试 |
| 旧行为不变 | legacy list path | 现有 suite + compatibility regression |
| 资源声称 | persistence | 可复现 scale report |

## 6. 验证计划与当前状态

review 前运行定向 OpenAPI、cursor、Memory persistence、tag、migration、Server、access-control 和 client 测试；SQLite 规模测量；
可用时的 OceanBase 证据；generated-code、contract、docs、format、lint、type check；以及 `make check` 和 `make unit-test`。
不可用服务和 skipped check 必须与 pass 分开报告。

当前状态：revision-valid directory、tag generation 失效、有界 runtime query、feature-scoped
migration/readiness gate、运维 CLI 和公开 OpenAPI/Server/SDK/MCP 接口已实现，并通过定向
persistence、contract、access 和 runtime 测试。规模报告、OceanBase migration 证据和全仓检查仍待完成。
