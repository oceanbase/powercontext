---
title: 审核 Candidate
description: 检查、修订、批准或拒绝待审核的 Experience 和 Skill Candidate。
---

# 审核 Candidate

使用 Review Inbox 判断生成或提交的 Experience、managed Skill 是否应成为 Artifact Revision。批准会写入不可变的
Revision；拒绝会关闭 Candidate，但不会写入 Artifact。

## 开始之前

启动 Server，并确认它已经就绪：

```bash
powercontext ready
```

将 `POWERCONTEXT_SCOPE_ID` 设置为 Candidate 所在的已有 Scope ID。本指南从 Experience 或 Skill 操作已经创建
pending Candidate 后开始。

## 从已有证据发起 Dream

Dream 在后台将选中的 Memory 条目或 Experience Revision 提炼成一个待审核候选；也可以返回 `no_change` 或
`needs_evidence`，不创建候选。先用 `powercontext capabilities` 确认 Server 返回 `artifact_dreaming: true`。
内置模型适配器目前支持 OpenAI 兼容和 Anthropic 生成配置；SDK 自动重试被关闭，由运行记录统一控制调用预算。

将 Memory API 返回的精确条目引用写入 `dream.json`：

```json
{
  "operation": "refine_experience",
  "memory_citations": [{
    "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 3},
    "entry_id": "ENTRY_ID",
    "entry_version_id": "ENTRY_VERSION_ID"
  }],
  "idempotency_key": "review-write-retry-evidence"
}
```

将示例标识替换为同一 Scope 中实际存在的引用，然后提交并查询返回的运行 ID：

```bash
powercontext dream run --scope-id "$POWERCONTEXT_SCOPE_ID" --request-file dream.json
powercontext dream show --scope-id "$POWERCONTEXT_SCOPE_ID" RUN_ID
powercontext dream list --scope-id "$POWERCONTEXT_SCOPE_ID" --status succeeded
```

HTTP 资源为 `/v1/scopes/{scope_id}/dream`：POST 受理运行，GET 列出运行，
GET `/v1/scopes/{scope_id}/dream/{run_id}` 查询详情。POST 对进行中的任务返回 202，重放已经结束的请求返回 200。
相同 key 和规范化后的相同输入返回原运行，修改输入但沿用 key 返回 409。结果为 `proposed` 时，使用其中的
Candidate ID 按下文审核。运行成功不会自动批准或安装制品。

派生 Skill 时，将 `operation` 改为 `derive_skill`，并在 `artifacts` 中放入已批准的 Experience 引用。
直接 Memory 条目引用仅用于 `refine_experience`。要替换已有 Experience，将其当前精确引用同时放入
`artifacts` 和 `target`。

输入去重后包含 1–20 个 Memory 条目和 Experience 引用；加上补充 `sources` 后最多 32 项。Source 使用
`source_type` 和 `source_id`。默认预算为 32 项投影、64 KiB 模型输入、两次模型调用、每次最多 4,096 输出 token，
从首次执行起最多 120 秒。每个 Scope 默认最多容纳 32 个排队或运行中的请求。重启后，Worker 通过租约恢复
已保存的请求，保持原证据快照和执行截止时间。

使用下文的 Candidate 命令或 `POST /v1/artifact-candidates/get` 读取当前版本及 `memory_citations`，
再通过 `POST /v1/memory/entries/get` 和精确 Source／Artifact 读取接口核对引用正文。
Dream 运行详情中的 `input_manifest` 保留生成时的根 Source 分组和独立性说明；同一根来源的重复引用不增加独立观察次数，
无法确认独立性时保留“未知”。审核按当前 Candidate version 及审核者当前权限重新校验证据；条目不可用或已停用时不能批准。
修订省略 `memory_citations` 或设为 null 会保留现有引用，
HTTP revise 请求可以显式替换该集合，`[]` 表示清空。批准后的 Experience Revision 在 lineage 中保存这些引用。

Dashboard 默认关闭，是使用静态 Bearer 鉴权的个人内容查看器。启用后可阅读已批准的 Experience 和 Skill，
并沿精确引用查看历史 Memory 条目；Dream 发起、运行查询和候选审核通过 CLI／Client／HTTP API 完成。
个人启用步骤见[安装和运行](../get-started/install-and-run.md)。

Runtime 启动时创建 `pc_dream_runs`，并为 `pc_artifacts` 和 `pc_artifact_candidate_versions` 增加可空的
`memory_citations` 列；旧行按空引用读取。运行表不复制 Memory 条目正文。部署数据库结构变更前应备份已有数据库。

## 1. 列出待审核 Candidate

```bash
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID"
```

默认 Review Inbox 只列出 `pending` Candidate head。需要时可以按 family 筛选：

```bash
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --family experience
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --family skill
```

响应包含每个 Candidate 的 `candidate_id` 和当前 `version`。如果响应返回 `next_cursor`，通过 `--cursor` 读取下一页。
`--limit` 可设为 1 到 100，默认值为 50。

## 2. 检查一个 Candidate

```bash
powercontext candidate show --scope-id "$POWERCONTEXT_SCOPE_ID" CANDIDATE_ID
```

做决定前，检查以下内容：

- `proposal`，包括全部 Experience 字段，或完整的 Skill 指令与验证项；
- `source_refs`、`artifact_refs`，以及这些精确引用所标识的证据；
- `target`，如果该提案会替换已有 Artifact Revision；
- `reason`、`family`、`status` 和当前 `version`。

证据不能支持的结论不要批准。Skill 指令以后可能被导出，因此包含密钥或不安全指令的 proposal 也应拒绝。批准操作本身
不会安装或执行 Skill。

## 3. 批准、拒绝或修订

将你刚检查的 `version` 作为 `--expected-version`。

### 批准精确版本

```bash
powercontext candidate approve \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --expected-version 1 \
  CANDIDATE_ID
```

响应包含 `status: approved` 和精确的 `result_artifact`。批准会在同一个事务中提交 proposal 并将 Candidate
标记为 approved。此后 Candidate 进入终态。

### 拒绝精确版本

```bash
powercontext candidate reject \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --expected-version 1 \
  --reason "证据不能支持提议的经验结论。" \
  CANDIDATE_ID
```

响应包含 `status: rejected`，拒绝理由保存在 `decision_reason` 中，并且没有 `result_artifact`。拒绝后 Candidate
进入终态。

### 修订 Experience Candidate

修订提交的是完整替代 proposal，不是局部补丁。请同时提供新版本应保留的证据：

```bash
powercontext candidate revise experience \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --expected-version 1 \
  --situation "只测试了一个存储后端。" \
  --action "在两个后端运行相同的验收场景。" \
  --outcome "两个后端都通过了测试。" \
  --lesson "验收行为应与后端无关。" \
  --source-ref content/SOURCE_ID \
  CANDIDATE_ID
```

响应仍为 `pending`，但 `version` 会增加。做下一次决定前，请检查这个新版本。

### 修订 Skill Candidate

较长的指令应放在 UTF-8 文件中。`--instructions` 和 `--instructions-file` 只能选择一个；每个验证项分别使用
一次 `--validation`：

```bash
powercontext candidate revise skill \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --expected-version 1 \
  --name backend-validation \
  --description "以一致方式验证存储后端。" \
  --instructions-file instructions.md \
  --validation "SQLite 通过测试。" \
  --validation "OceanBase 通过测试。" \
  --artifact-ref experience/EXPERIENCE_ID@REVISION \
  CANDIDATE_ID
```

只有替换现有 Artifact 时才使用 `--target FAMILY/ID@REVISION`。CLI 会自动将 target 加入 Candidate 的 Artifact
证据。

## 4. 验证审核结果

再次读取 Candidate：

```bash
powercontext candidate show --scope-id "$POWERCONTEXT_SCOPE_ID" CANDIDATE_ID
```

Candidate 已批准时，记录精确的 `result_artifact`；Candidate 已拒绝时，确认 `decision_reason`。默认 Inbox 不再列出
这两种终态，需要审计时应显式查询：

```bash
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --status approved
powercontext candidate list --scope-id "$POWERCONTEXT_SCOPE_ID" --status rejected
```

如果写操作报告 Candidate 版本过期，请重新显示 Candidate 并审核新版本。不要在未检查替代内容时直接修改
`--expected-version`。进入终态的 Candidate 不能再次批准、拒绝或修订。

HTTP API、Python Client 和 MCP 暴露相同的五个 Review 操作，并使用相同的并发规则。接口契约和可用范围见
[接口](../develop/interfaces.md)。

## 5. 在后续任务中使用 Dream 经验

例如，先记录写请求超时、重放请求以及数据库行数检查的实际结果，再从这些 Source 提取 Memory 条目。
同一次检查被多条记忆转述时，审核页仍应只显示一个根来源；不要将转述次数当成重复验证次数。
将这些条目提交给 Dream，检查候选是否明确区分读取重试、幂等写入和提交状态不明的写入，然后批准精确版本。

在后续重试任务开始时，向 `POST /v1/context/prepare` 提交：

```json
{
  "scope_id": "SCOPE_ID",
  "query": "写请求超时且提交状态不明时，怎样避免重试产生重复记录？"
}
```

检查返回上下文是否包含已批准 Experience 的精确引用，再将其作为历史证据提供给 Agent。
pending 或 rejected Candidate、Dream 运行说明不会作为经验进入该上下文。后续任务仍需实际检查提交状态和
去重结果，并将新的验证结果记录为 Source。若原 Memory 条目已停用，引用它的新 Dream 提交和候选批准都会被阻止。
