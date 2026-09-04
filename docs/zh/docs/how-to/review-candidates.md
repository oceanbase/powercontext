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
[接口](../reference/interfaces.md)。
