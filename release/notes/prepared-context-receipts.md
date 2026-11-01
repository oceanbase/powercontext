# PreparedContext Receipt v1

## English

Prepared context can return an optional diagnostic Receipt with evidence from the
same preparation: retrieval modes and fallbacks, rerank configuration, selected
Artifact revisions and code ranges, omission reasons, stage timings, and query
and content digests. The Receipt contains no query, prompt, or evidence content.

This is a breaking API change for clients upgrading from PowerContext 1.4.1:

- Replace `"explain": true` with `"include_receipt": true` in prepare requests.
  The removed `explain` field is rejected with HTTP 422 (`invalid_request`).
- Replace reads of the removed top-level `reason` with per-search evidence in
  `receipt.retrieval.searches[]`. Use `family`, `round`, `mode`, and
  `fallback_reason` to identify the search and its fallback.
- Check for `receipt` before reading it. If complete evidence is unavailable,
  Receipt construction fails, or its serialized size exceeds 8192 UTF-8 bytes,
  the response omits the entire Receipt and preserves the prepared context.

Omitting `include_receipt`, sending `false`, or sending `null` preserves the
four response fields: `schema`, `status`, `content`, and `content_bytes`.
Automatic host recall continues to use this response without requesting a
Receipt. Use the existing authorized exact-read APIs to retrieve selected
Artifact revisions; a Receipt is not a persisted read key.

## 中文

准备上下文时可以按需返回诊断 Receipt，记录同次准备的检索模式与降级、
重排配置、所选 Artifact 版本与代码行范围、遗漏原因、阶段耗时，以及
查询和内容摘要。Receipt 不包含查询、提示词或证据正文。

对于从 PowerContext 1.4.1 升级的客户端，这是一个不兼容 API 变更：

- 将 prepare 请求中的 `"explain": true` 替换为 `"include_receipt": true`。
  已移除的 `explain` 字段会被拒绝，返回 HTTP 422（`invalid_request`）。
- 将读取已移除的顶层 `reason` 改为读取
  `receipt.retrieval.searches[]` 中的逐次检索证据，结合 `family`、`round`、
  `mode` 和 `fallback_reason` 判断具体检索及其降级情况。
- 读取前检查 `receipt` 是否存在。证据不完整、Receipt 构造失败，或序列化后
  超过 8192 UTF-8 字节时，响应会省略整个 Receipt，并保留原准备结果。

省略 `include_receipt`、发送 `false` 或发送 `null` 时，响应仍只有
`schema`、`status`、`content` 和 `content_bytes` 四个字段。自动 Host 召回
继续使用该响应，不请求 Receipt。查看所选 Artifact 版本时，使用现有授权的
精确读取 API；Receipt 不是持久读取键。
