---
title: 管理 Prompt
description: 按 Scope 修改操作提示词、检查示例并恢复 Prompt 版本。
---

# 管理 Prompt

Prompt 自定义功能位于当前 `master`。它修改一个 Scope 内的操作提示词，不能改变输出结构、工具可用性或权限。
采集的用户 Prompt 属于 [Source 证据](sources.md)，是另一条工作流。

`profile.generate` 用于自定义自动或手动 flush 时的画像生成指令。自定义内容只影响后续处理窗口；
画像的证据边界、人物归属、敏感属性禁止推断和输出契约仍由服务端固定。

`atomic_memory.reconcile` 用于自定义提取出的 Memory 候选与相关记忆及同一窗口内先前候选的比较方式。
`atomic_memory.extract` 与 `atomic_memory.reconcile` 都为 Auto 时，没有可比较对象的候选按提取结果保留，不发起
reconcile 请求；任一 Prompt 为 Custom 时，每个候选都会经过 reconcile。哪些事实值得保留由提取决定。内置 reconcile
也可能丢弃它认为不值得保留的候选，但只发生在比较过程中；因此窗口内第一个候选在没有相关记忆时不会经过这第二次判断。

## 编辑与验证

1. 通过 `GET /v1/scopes/{scope_id}/prompts/{prompt_key}` 读取当前配置、内置指令及操作状态。
   未启用的操作仍可读取配置，但保存配置不会启用该操作。由外部组件管理的 Prompt 可能没有可用内置指令。
2. 按 Prompt 内容契约设置 Custom 指令和完整 JSON 输入/输出示例；生成的示例应在保存前检查。
3. 通过 Artifact API 创建或条件替换 Prompt。保存成功产生不可变 Revision；遇到版本冲突时重新读取后再协调修改。
4. 再次运行目标操作并检查输出。保存 Prompt 不会重新处理历史 Sources 或重跑先前操作。

下面用 `memory.extract` 演示首次创建和条件替换。可用的 key 包括 `memory.extract`、`memory.rerank`、
`experience.incubate`、`experience.generate`、`skill.generate` 和 `handoff.generate`；先用 GET 检查目标操作是否
支持自定义。

只有当 GET 返回的操作状态表示支持自定义时才继续 POST/PUT。如果状态为 `disabled`、`unsupported`，或响应说明当前
运行时没有可用的 Prompt customization provider，保存会返回 `prompt_customization_unavailable`；此时请继续使用内置
Auto 模式，或先配置支持该操作的运行时，不要把失败响应当作已保存。

首次创建 Custom Prompt：

```bash
export POWERCONTEXT_URL=http://127.0.0.1:8000
export POWERCONTEXT_SCOPE_ID='已有的-scope-id'
curl --fail --request POST \
  --header 'Content-Type: application/json' \
  --data '{"family":"prompt","prompt_key":"memory.extract","content":{"schema_version":"powercontext.prompt.v1","mode":"custom","instructions":"只保存有长期复用价值的决策、约束和事实；每条候选必须引用输入证据。","demonstrations":[]}}' \
  "$POWERCONTEXT_URL/v1/scopes/$POWERCONTEXT_SCOPE_ID/artifacts"
```

再次编辑前读取生效配置和 `artifact_etag`，再把该值作为 `If-Match`：

```bash
prompt_json="$(curl --fail -sS "$POWERCONTEXT_URL/v1/scopes/$POWERCONTEXT_SCOPE_ID/prompts/memory.extract")"
prompt_etag="$(printf '%s' "$prompt_json" | jq -r '.artifact_etag // empty')"
test -n "$prompt_etag"
curl --fail --request PUT \
  --header 'Content-Type: application/json' \
  --header "If-Match: $prompt_etag" \
  --data '{"content":{"schema_version":"powercontext.prompt.v1","mode":"custom","instructions":"只保存经过核对、可长期复用的决策、约束和事实；每条候选必须引用输入证据。","demonstrations":[]}}' \
  "$POWERCONTEXT_URL/v1/scopes/$POWERCONTEXT_SCOPE_ID/artifacts/prompt/memory.extract"
```

首次创建不存在时，GET 的 `artifact_etag` 为 `null`，此时使用 POST；不要用空的 `If-Match` 代替。Auto 模式必须把
`instructions` 和 `demonstrations` 都设为空字符串/数组，使用当前部署的内置指令。保存成功后重新运行目标操作并
检查输出；如果返回 `409` 或 `412`，先重新 GET 再协调修改。

保存 Auto 模式可使用当前部署的内置指令。恢复历史内容时创建新的 Revision，保留中间历史；
恢复 Auto 模式会使用当前内置指令。Dashboard 不提供 Prompt 编辑页面。

在 enforced 模式下，创建、替换、切换 Auto 和恢复都需要当前 `scope.admin` 权限。
Scope 角色被撤销后，拥有 Prompt Artifact 也不能继续修改它。

要在另一个 Scope 中复用指令，请使用已注册的 `prompt_key` 创建或替换目标 Scope 的 Prompt。
通用 Artifact 发布接口会拒绝 Prompt，返回 `422 / artifact_publication_unsupported`，目标 Scope 保持不变。

应用可通过 `GET /v1/scopes/{scope_id}/prompts/{prompt_key}` 读取生效配置，再使用带条件写入的
[Artifact API](artifacts.md)保存版本。支持的 key、请求结构和示例生成见 [HTTP API](../develop/http-api.md)。

## Topic Memory 内容要求

Topic Memory 内置阶段指令使用 `v2`。Auto 和自定义生成都必须基于所提供材料输出具体业务内容，在更新时保留仍然有效的历史信息，
并将不透明 ID 限于路由字段。数量为上限，无需凑满。Probe/global 可以返回空结果，evolve 可以返回 `proposal: null`；
temporary 和 reduction 阶段仍需遵守现有的非空及证据保留契约。

包含可识别占位模板或纯协调说明输出的自定义示例会以 `prompt_definition_incompatible` 被拒绝，
包括在推理前解析已有自定义 revision 的场景。持久化 Prompt revision 不会被改写。
自定义 Prompt 不能关闭运行时内容准入。仅提供指令的自定义 Prompt 仍受支持，其生成结果需通过与 Auto 相同的准入检查。
