---
title: 创建并审核 Experience
description: 根据精确证据生成 Experience Candidate，完成审核，并验证 approved Revision。
---

# 创建并审核 Experience

当精确任务证据中包含可复用的 situation、action、outcome 和 lesson 时，可以生成 Experience Candidate。批准后，当前
Experience Revision 可以参与同一 scope 的 `PreparedContext` 召回。

## 开始之前

配置 generation model 并启动 Server，然后确认 Experience generation 已启用：

```bash
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

在另一个终端中运行：

```bash
powercontext capabilities
```

输出应包含 `Experience generation: enabled`。目标 scope 中还需要至少一个精确 Source 或 Artifact 引用。Provider
凭据由所选 inference provider 读取。

## 1. 生成 Candidate

传入能够支撑 Experience proposal 的精确证据：

```bash
powercontext experience generate \
  --scope-id project:example \
  --source-ref content/SOURCE_ID \
  --reason "从已完成任务中提取可复用经验。"
```

Proposal 需要更多证据时，可以重复使用 `--source-ref` 或 `--artifact-ref`。响应有两种结果：

- `status: pending`，并返回需要审核的 Candidate；
- `status: no_op`，表示模型没有提出内容，因此没有 Candidate。

Generation 不会批准 proposal，也不会让它进入召回。

## 2. 检查并审核 Candidate

收到 pending 结果后，复制其中的 `candidate_id` 和 `version`，然后检查内容：

```bash
powercontext candidate show --scope-id project:example CANDIDATE_ID
```

检查全部四个 Experience 字段及其精确证据。只批准你已经检查过的 version：

```bash
powercontext candidate approve \
  --scope-id project:example \
  --expected-version 1 \
  CANDIDATE_ID
```

响应应包含 `status: approved` 和精确的 Experience `result_artifact`。需要修订或拒绝时，按照
[审核 Candidate](review-candidates.md) 操作。

## 3. 验证 approved Revision

再次读取 Candidate，并记录 `result_artifact`：

```bash
powercontext candidate show --scope-id project:example CANDIDATE_ID
```

Approved current head 现在可以参与同 scope 的 `PreparedContext` 召回。Runtime 仍会根据 query 和共享输出预算选择内容，
因此符合召回条件不代表一定被选中。Python Client 和 HTTP API 支持精确读取 Experience。

## 替换已有 Experience

基于精确的 current Revision 生成 replacement，并引用支撑这次修改的证据：

```bash
powercontext experience generate \
  --scope-id project:example \
  --target experience/EXPERIENCE_ID@REVISION \
  --source-ref content/NEW_SOURCE_ID \
  --reason "根据已经验证的后续结果更新经验。"
```

CLI 会自动把 target 加入 Artifact 证据。之后照常审核新 Candidate。如果其他批准已经推进 Artifact head，批准请求会返回
Artifact conflict，并让这个 Candidate 保持 pending。

## 定时孵化 Experience

需要定期检查已完成的任务结果时，配置独立 interval 并重启 Server：

```bash
export POWERCONTEXT_SERVER_RUNTIME_EXPERIENCE_SCHEDULE_SECONDS=30
export POWERCONTEXT_SERVER_INFERENCE_GENERATION_MODEL=provider:model-name
powercontext server run
```

该 job 处理有界的 Content Source window，并且只考虑 metadata 包含 `"kind": "task-outcome"` 的 Source。每次
activation 最多检查 32 条 Source，普通 Prompt Source 会被忽略。

定时孵化只会创建 pending Experience Candidate。它不会自动批准，也不会把 pending 内容放入 `PreparedContext`。
每个 Candidate 都需要通过 Review Inbox 决定。

配置默认值和 provider 设置见[配置](../reference/configuration.md)。
