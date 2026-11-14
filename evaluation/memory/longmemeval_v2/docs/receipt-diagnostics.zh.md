# PreparedContext Receipt 诊断

Receipt 诊断只消费提供 Reader context 的同一次公开 `/v1/context/prepare`
响应，是 LongMemEval-V2 评估包的显式选项（[issue #2436](https://github.com/oceanbase/powercontext/issues/2436)）。
Server、生产召回策略、Reader 和 Judge prompt 均不因此改变。

## 配置与 Server 身份

`run_retrieval_smoke`、`run_smoke` 接受 `receipt_diagnostics: bool = False`
和 `receipt_server_revision: str | None = None`；直接使用 harness adapter 时，
在 `memory_params` 中提供相同字段。布尔值严格校验：JSON `false` 合法，字符串
`"false"`、整数和 null 均不合法。启用时，`arm.retrieval_strategy` 必须为
`prepared-context`（adapter 对应 `query_strategy`），revision 必须完整匹配
`[0-9a-f]{40}`。关闭时不要求也不使用 revision。

操作者负责部署对应源码 SHA 的 Server，并验证该构建支持 `include_receipt: true`。
填写 SHA 是操作者声明，不是 Server 自证或部署身份验证。原有 readiness 和
PreparedContext capability 检查继续执行，但 `/v1/capabilities.context_versions`
不能证明 Receipt 支持；不会新增 capability、version API、兼容性探针或自动协商。

在两个 CLI 的原有必填参数之外，追加相同选项：

```bash
uv run --project evaluation longmemeval-v2 retrieval-smoke \
  ... \
  --experiment-arm query-time-compact-v1 \
  --receipt-diagnostics \
  --receipt-server-revision "$RECEIPT_SERVER_REVISION" \
  --output-dir /path/to/new-receipt-retrieval

uv run --project evaluation longmemeval-v2 run-smoke \
  ... \
  --experiment-arm query-time-compact-v1 \
  --receipt-diagnostics \
  --receipt-server-revision "$RECEIPT_SERVER_REVISION" \
  --skip-reader \
  --output-dir /path/to/new-receipt-run
```

`RECEIPT_SERVER_REVISION` 应为已验证部署的完整小写 SHA，与本地 harness 的
`--powercontext-revision`、`--integration-revision` 分别记录。
配置错误会在 runtime HTTP、阶段执行和输出写入之前拒绝；`--skip-reader` 同样适用。
仅启用时，retrieval manifest 和外层 `run-manifest.json` 增加本地配置块：

```json
{
  "diagnostics": {
    "receipt_diagnostics": true,
    "receipt_server_revision": "0123456789abcdef0123456789abcdef01234567",
    "server_revision_basis": "operator_attested"
  }
}
```

上述 SHA 为纯合成示例，不代表真实部署。revision 和声明依据均不进入 prepare
HTTP 请求。直接使用 harness 时，在 `memory_params` 设置 `query_strategy`、
`receipt_diagnostics` 和 `receipt_server_revision`，并在自己的本地 run manifest
中保留上述声明。retrieval-only 诊断无需额外模型凭据。

## 请求、版本与状态边界

| 模式 | Prepare 请求 | Audit schema | Context items |
| --- | --- | --- | --- |
| 默认关闭 | 原有字段，无 `include_receipt` | `powercontext.longmemeval-v2-memory-audit.v1` | 原有文本项 |
| 显式启用 | 同一次调用增加 `include_receipt: true` | `powercontext.longmemeval-v2-memory-audit.v2` | 原有文本项 |

不自动启用 `include_reuse`。每个 query 仍然只有一次 prepare；不会额外 search/get
或再次 prepare 来重建选择证据。旧 Server 返回 422、网络错误和 prepare 失败保留原有
失败路径，不去掉标记自动重试，不伪装成空 context、零命中或 Receipt 缺失。

v2 保留 ingest/query 原有字段，仅 query 增加 `prepared_context_diagnostics`，
通过现有 `query_invocation_id` 与结果、timing 关联。query metadata hook 维持基线形状，
诊断不进入 retrieval results、Reader 文本项或 prompt。每个 adapter 实例持有自己的版本；
打开 run 时发现已有不同 audit 版本会在写入前拒绝，需使用新目录。报告支持 v1/v2，拒绝
未知或混用 schema。v1 显示 `not_collected`，诊断统计为 null，不当成零。

先执行原有 PreparedContext schema、status、UTF-8 字节数与预算校验。通过这些校验的
context 不因诊断不完整而改变：

| Receipt 情况 | 诊断状态 | 完整性与归因 |
| --- | --- | --- |
| 支持且校验通过 | `available` | `complete`，可汇总公开事实 |
| 缺失或 null | `unavailable` | `incomplete`，原因未知，不能区分服务端超限与构建失败 |
| 畸形、不支持、完整性不符或本地超限 | `invalid` | `incomplete`，不参与可信归因 |
| HTTP 或原有 prepare 校验失败 | query 失败 | 请求失败，不是 unavailable 或空成功 |

本地 `validation_failure` 包括 `invalid_receipt`、`unsupported_schema`、
`used_bytes_mismatch`、`budget_mismatch`、`content_digest_mismatch`、
`truncation_mismatch`、`identity_mismatch`、`evidence_mismatch`、`local_size_limit`。
有效 Receipt 必须满足严格类型、列表边界及对应的非负/正计数约束；`used_bytes` 等于同响应
`content_bytes`，预算等于请求预算，非空 content digest 为原始 UTF-8 的 SHA-256，
带 `sha256:` 前缀。空 context 使用公开契约的 null digest。selected 身份形状合法，
顶层与条目截断标志一致。报告独立保留请求失败计数。

## 有界投影与解读

诊断契约为 `powercontext.longmemeval-v2-prepared-context-diagnostics.v1`，
公开 Receipt schema/policy 为 `powercontext.prepared-context-receipt.v1`。
新增字段只保留明确 allowlist：schema、状态、校验结果、完整性、Receipt/policy 身份、
content digest 与字节预算、保序 selected 精确身份及 rendered bytes/截断、omitted 分组、
公开 round/search/rerank、config/prompt digest、非确定性标志和 stage timing。
不复制响应正文、query 文本、rerank 原始 `effective_settings`、prompt 载荷、code 路径或未知字段。
未知公开字段由 Pydantic 默认行为忽略，也不会通过显式投影复制到 audit。

诊断投影序列化后最多 **8,192 UTF-8 bytes**，与 context 内容预算分开计算。超限返回
`invalid` / `local_size_limit`，不截取半份对象再声称 available。

`candidate_count` 始终为 `null`，`candidate_count_basis` 为 `not_exposed`。
公开 HTTP 不提供 `RecallEffort.candidates_by_round`；不会读取 sink、日聚合表、进程内部
对象或新增 API。`selected_count=0` 和 `not_retrieved` 都不能证明原始候选数为零。

| 维度 | 含义与计量单位 |
| --- | --- |
| selected / 成功截断 | 选中条目数，精确身份和字节贡献保留在本地 |
| omitted | 按 family/reason 独立累计条目；`not_retrieved` 单独不能证明 family 未启用或零候选 |
| `below_min_bytes`、`no_fitting_truncation`、`family_limit`、`entry_limit` | 预算/配额舍弃条目数 |
| `rerank_not_selected` | 选前舍弃条目数，不是 Reader/模型失败 |
| search `fallback_reason` | 实际搜索执行次数，区分 timeout、unavailable 和 reused fallback |
| rerank 分组 | 按 round/outcome/fallback 累计执行次数 |
| `expansion_failed` | 扩展失败轮数，可与其他维度并存 |
| stage timing | 毫秒及执行次数 |

这些维度不互斥，不能合成唯一失败类别，也不能把执行次数与舍弃条目数相加。
只有 available Receipt 提供可信归因；unavailable、invalid 和请求失败分别记录。

精确 Scope/Artifact 地址和既有 query hash 属于本地受控 audit。可分享诊断汇总只增加公开
分组与总量，省略精确身份、query hash、query 和正文；哈希不能称为匿名化。报告本来已有
的 run 标识仍须按分享规则检查。Prepared context、实际发送、host 注入、模型引用、模型使用、
答案正确、材料有用是不同事实；Receipt 不证明这些下游事实。

## 合成示例与离线验证

固定示例不含真实用户内容：

- [本地 manifest 与 fixture 来源](examples/receipt-diagnostics-v2.manifest.json)：
  source/config/build 身份明确标为合成；Source reference 是示例设置来源，不表示原生 Memory lineage。
- [v2 query 事件](examples/receipt-diagnostics-v2.query.jsonl)：包含 selected 地址、截断、
  预算/rerank 舍弃、搜索降级与扩展失败。digest 对应 160 个合成 ASCII `x` 字节，audit 不保留正文。
- [可分享诊断聚合](examples/receipt-diagnostics-v2.aggregate.json)：对应报告中的
  `prepared_context_diagnostics` 块，保留单位与候选数未知，不包含 source/Artifact 精确身份。

默认测试使用离线合成 runtime 和阶段/模型替身，不访问远端 Server 或模型。
[英文说明](receipt-diagnostics.md#synthetic-examples-and-offline-verification) 列出 focused pytest
与 Ruff 命令。真实 TCP HTTP/SQLite 验收使用仓库根目录的 Server 环境，启动一次性 Server，
不调用模型；未设置开关时跳过。在仓库根目录执行：

```bash
POWERCONTEXT_TEST_RECEIPT_HTTP=1 \
PYTHONPATH=evaluation/memory/longmemeval_v2/src \
uv run pytest evaluation/memory/longmemeval_v2/tests/test_longmemeval_v2_receipt_http.py -q
```

验收使用的操作者声明源码基线为 `923e3d4cec8038d0799f6893b0ec8e746c870434`，
不是 Server 自证或远端部署验证。真实同响应验收与离线合成测试必须分别报告。mock 只验证
本地处理；模拟 `HTTPError(422)` 的 fixture 只验证旧 Server 失败路径，不代表运行过历史构建。
