# RFC 1429：有界 Scope 启动上下文

- 状态：已实现
- Issue：[#1429](https://github.com/oceanbase/powercontext/issues/1429)
- 相关 RFC：[Scope 组织与 Agent 集成](1345_scope_organization_and_agent_integration.md)、[Memory 层](0014_memory_layer_design.md)、[Handoff Artifact](0048_handoff_artifact.md)、[Artifact 标签](1467_artifact_tags.md)

## 摘要

PowerContext 可以在受支持的 Agent 生命周期第一次真实查询之前，提供一个小型、显式策展的上下文包。该能力默认关闭；输出确定、按 UTF-8 字节限额、引用不可变内容，并始终作为不可信历史处理。当宿主提供稳定生命周期标识时，同一边界最多交付一次；任何失败都不得阻止 Agent 启动。

Issue #1429 使用了 Project 和 Workstream 术语。后续 RFC 1345 已确定 Scope 是唯一的所有权与路由边界，直接 Context Reference 是授权读取集合。因此本 RFC 在一个已解析的当前 Scope 及其直接 Context Reference 上实现需求，不重新引入 Project 或 Workstream 目录。

## 内容资格

首个画像版本为 `powercontext.scope-bootstrap.v1`，只允许：

1. 最多六条当前有效、且被显式标记 `bootstrap-context` 的 Memory 条目。先选择当前 Scope，再按 Scope 的规范顺序选择最多八个直接 Context Reference；每个参与 Scope 最多检查 32 条带标签的条目。
2. 调用方在当前 Scope 中显式指定的一条精确、已提交 Handoff Revision。服务端不会隐式选择“最新 Handoff”。

标签表示运维者已经审核其适合自动注入，但不是授权机制。不得标记凭证、秘密、个人数据、未经审核的生成指令或其他不应自动注入的内容。Runtime 还会排除 kind 表明为 secret、credential、password、token 或 private-key 的条目。每个参与 Scope 仍须通过当前授权检查。

完整 transcript、全部 Memory、待审核候选、原始 Source 窗口、生成中的 Profile 以及任意其他 Artifact Family 均不符合资格。选择过程不调用 embedding、rerank、生成或扩展。

## API 与交付收据

`POST /v1/context/bootstrap` 接收已解析的 `scope_id`、显式开关、画像、生命周期、集成名称、可选稳定 `event_id`、字节上限和可选精确 Handoff 引用。返回 `powercontext.bootstrap-context.v1`，状态为 `ready`、`empty` 或 `skipped`，并携带有界正文、包摘要、精确引用、截断信息和交付收据。

`ready` 响应的收据为 `pending`。宿主必须先通过 `POST /v1/context/bootstrap/receipts` 一次性认领 `pending`→`injected` 状态迁移，成功后才能输出 `additionalContext`；第二次认领即使已存状态是 `injected` 也会被拒绝。确认失败时不注入。宿主也可记录 `failed`。关闭和无合格内容会产生 `skipped` 收据；终态事件重试只返回不含正文的 `skipped` 响应。

收据不保存查询、正文、transcript 路径、原始宿主事件 ID 或宿主错误文本，只保存有界身份元数据、精确内容引用与摘要、字节数和状态。事件 ID 在持久化前与 Scope、集成及生命周期一同哈希。稳定事件重试在 `pending` 时重建同一精确包，终态后返回 `skipped`；若待交付期间关闭开关，收据会直接终结为跳过且不读取 Context Reference。宿主不提供稳定事件标识时，不宣称具备跨重启幂等性。

`PrepareContextRequest` 可携带 `bootstrap_receipt_id`。只有当收据属于相同 Scope 且状态为 `injected` 时，普通查询召回才去掉收据中完全相同的 Memory 条目版本；后续修订版仍可召回。缺失、过期、跨 Scope 或非注入收据均被忽略，保持 fail-open。

## 限额与信任边界

默认输出 4,096 字节，硬上限 8,192 字节；最多一条 Handoff 加六条 Memory。参与 Scope 数量、带标签条目扫描数、选中项数、单项正文和最终输出均有固定上限。单项正文最多 2,000 字节，只在 Unicode 字符边界截断，Scope、Artifact Revision、Memory 条目与版本、内容摘要及截断标志必须完整保留。无法在剩余预算中同时保留有意义正文与引用的项目会被省略。

输出明确声明为不可信历史数据。当前 system/developer 指令、用户请求、仓库规则和实时验证始终优先；历史 Markdown 以引用数据呈现，不成为新的指令层。

## 宿主集成与失败策略

Codex 和 Claude Code 复用现有 Scope 绑定，在 `SessionStart` 请求启动包，并保留 `startup`、`resume`、`clear`、`compact`、`restore` 和 `fork` 的独立来源语义。两端均默认关闭，使用统一的绝对 HTTP 时间预算，严格校验响应，只在插件持久数据目录保存最近的收据 ID，并在超时、鉴权失败、无权限、服务不可用、旧版本服务或非法响应时不阻断宿主。失败与空结果使用不含正文的诊断事件区分。

随后第一次 `UserPromptSubmit` 仍执行普通基于查询的召回，只把该收据 ID 作为精确版本去重提示；现有 prompt 捕获行为不变。

## 验收

契约与 Runtime 测试必须覆盖：默认关闭、全部生命周期、精确 Handoff、策展 Memory 顺序、Unicode 字节限额、秘密 kind 排除、Context Reference 授权、收据重试与状态迁移、首查询精确版本去重，以及数据库不保存正文。Codex 与 Claude Code 测试必须覆盖成功注入、关闭/空结果、向首查询传递收据、非法响应、超时和 HTTP 失败均不阻塞宿主。
