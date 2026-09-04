---
title: 创建并导出 managed Skill
description: 根据精确证据生成 managed Skill Candidate，批准后将一个 Revision 导出给 Codex。
---

# 创建并导出 managed Skill

当经过审核的证据能够支撑可复用 instructions 和 validation checks 时，可以生成 managed Skill。批准会创建不可变的
Skill Revision；另一次显式导出才会让 Codex 使用某个精确 Revision。

## 开始之前

配置 generation model 并启动 Server，然后确认 managed Skill generation 已启用：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

在另一个终端中运行：

```bash
powercontext capabilities
```

输出应包含 `Managed Skill generation: enabled`。将 `POWERCONTEXT_SCOPE_ID` 设置为 `create_scope` 返回的已有
ID，然后选择一种 provenance origin：

| Origin | 适用情况 |
| --- | --- |
| `experience` | 一个或多个 approved Experience Revision 能够支撑新 Skill |
| `source` | 精确的官方或人工 Source 能够直接支撑新 Skill |
| `usage` | Usage Source 能够支撑对某个精确现有 Skill Revision 的更新 |

## 1. 生成 Candidate

根据 approved Experience 生成：

```bash
powercontext skill generate \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --origin experience \
  --artifact-ref experience/EXPERIENCE_ID@REVISION \
  --reason "把经过审核的经验转为可复用指令。"
```

也可以直接根据精确 Source 生成：

```bash
powercontext skill generate \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --origin source \
  --source-ref content/SOURCE_ID \
  --reason "根据已批准的操作规程创建 Skill。"
```

响应包含 `status: pending` 和 Candidate，或者返回 `status: no_op` 且没有 Candidate。Generation 不会批准、安装、
导出或执行 proposal。

## 2. 检查并批准 Candidate

检查返回的 Candidate：

```bash
powercontext candidate show --scope-id "$POWERCONTEXT_SCOPE_ID" CANDIDATE_ID
```

检查名称、用于发现的描述、完整 instructions、validation checks 和精确 lineage。然后批准你检查过的 version：

```bash
powercontext candidate approve \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --expected-version 1 \
  CANDIDATE_ID
```

响应应包含 `status: approved` 和精确的 Skill `result_artifact`。批准不会安装 Skill，也不会授予执行权限。需要修订或
拒绝 proposal 时，按照[审核 Candidate](review-candidates.md) 操作。

## 3. 读取精确 Skill Revision

使用 `result_artifact` 中的 Artifact ID 和 Revision：

```bash
powercontext skill show \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --revision 1 \
  SKILL_ID
```

响应包含 approved content 及其精确 Source、Artifact lineage。Managed Skill 不会进入 `PreparedContext`，只能通过
exact read 和显式导出使用。

## 4. 将 Revision 导出给 Codex

导出给 Codex 时，Skill name 最多包含 64 个小写字母、数字和单连字符；description 最多包含 1,024 个字符，并且不能有
尖括号。目标目录名必须与 Skill `name` 一致。导出为代码库级 Codex Skill：

```bash
powercontext skill export \
  --target codex \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --revision 1 \
  --destination .agents/skills/backend-validation \
  SKILL_ID
```

该命令创建一个新目录：

```text
.agents/skills/backend-validation/
├── SKILL.md
└── powercontext.json
```

`powercontext.json` 记录精确 Artifact 引用和渲染内容哈希。目标目录已存在时，命令会拒绝覆盖。Managed Skill
Revision 始终是内容权威，目录只是 host-local projection。

Codex 会自动检测导出的代码库级 Skill。如果没有出现，再重启 Codex。

## 根据 usage 演进 Skill

使用 `usage` origin，并提供精确 current Skill Revision 和记录实际使用结果的 Source：

```bash
powercontext skill generate \
  --scope-id "$POWERCONTEXT_SCOPE_ID" \
  --origin usage \
  --target skill/SKILL_ID@REVISION \
  --source-ref content/USAGE_SOURCE_ID \
  --reason "根据实际使用结果更新验证步骤。"
```

CLI 会自动把 target 加入 Artifact 证据。审核并批准 replacement Candidate 后，才能导出新 Revision。如果导出目录已经
存在，应先明确检查和处理该 projection，再重新导出；命令不会覆盖已有目录。

外部 Agent-native Skill 的发现和导入契约见[接口](../reference/interfaces.md)。
