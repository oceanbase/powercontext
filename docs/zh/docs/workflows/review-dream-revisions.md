---
title: 审核 Dream 修订
description: 使用现有 v1 Dream 接口提出、检查和审核 Prompt 配置与标签变更。
---

# 审核 Dream 修订

最低客户端功能级别为 **Dream contract 2**。配套 Python Client、CLI、MCP bridge 和 Dashboard 自动发送 `X-PowerContext-Dream-Contract: 2`。其他集成先更新 operation 枚举和 proposal 类型，再发送该声明。旧客户端遇到新类型时收到 HTTP 426 `client_upgrade_required`；原有操作仍可用。`GET /v1/capabilities` 列出当前已配置且可执行的操作。

Dream 使用精确目标与证据生成待审 Candidate。生成不会批准、安装、执行或发布内容；已有自动生成流程保持原有策略。审批须由用户明确决定，并携带实际检查过的候选版本。

## Profile 与 Handoff 生效边界

Profile Dream 在接收请求时冻结影响生成的策略配置。普通 Source 处理、待审候选指针变化和仅控制 Source 的激活配置不会使提案失效。生成配置改变需要重新发起 Dream；Profile head 改变则独立触发目标冲突。批准不消费 Source 游标，也不清除普通待审候选。

Handoff 批准即发布新版本，后续显式 Continue 按既有 latest 规则可选中新版本；批准本身不会调用 Continue、执行任务或记录接收确认。接收方仍须核验 live state、capability 和 authorization。`ActivateHandoff` 用于生成草稿，不需要用它激活已经批准的版本。

## 协调升级

统一 Candidate 接口与物理表改名一起发布，仅启用 A0/A1、关闭 Tag 的部署也需升级。已有数据库通过[统一迁移框架（RFC #1771）](https://github.com/oceanbase/powercontext/pull/1771)显式维护：停止写入，执行选定的备份策略，迁移并验证后启动匹配版本的 Server，在恢复流量前同步升级 Client、SDK、CLI、MCP 和集成。Dream 启动只检查所需存储，不改名、补列或创建去重索引。移除的 `/v1/artifact-candidates/*` 不保留别名或兼容过滤，直接下线是本次发布明确跳过通常弃用期的例外；数据迁移不等于兼容旧客户端。后续启用 Tag 不需要再次改名候选表。

停止全部写入前，先停止新增 Dream 请求，并由旧程序将 queued/running Dream Run 处理到终态。未完成任务会使迁移返回 `dream_tasks_require_drain`；迁移不改写冻结的提示词、模型配置、重试预算或截止时间。已完成 Run 和 pending Candidate 保留，升级后可继续读取与审核。Handoff/Prompt 的 Processing 绑定也由显式迁移更新，普通启动不修补 manifest；不支持的 manifest 返回 `unknown_processing_manifest` 并阻止迁移。

[完整 Server 迁移 bundle](../operate/database-migrations.md) 为无向量、Code 关闭的配置登记了完整 1.3.0 基线、revision 链和历史任务检查。维护前须停止写入，并排空 queued 或 running 的旧 Dream 任务。启动时校验完整受管数据库，对未完成的 revision 返回 `migration_required`；手工 stamp 或创建空替代表不能建立就绪状态。局部 Artifact bundle 仍返回 `server_ready=false`。具体版本已验证的升级支持范围以安装指南为准。已登记旧表与恢复对象按统一框架的保留策略管理。

## Prompt 配置

先读取已注册、支持 custom 模式的 Prompt key（如 `atomic_memory.extract`）的当前 ArtifactRef，再选取包含已观察错误及可信预期结果的精确 Source。通过原有 Dream 接口提交：

```python
from powercontext.client import PowerContextClient
from powercontext.http import (
    ArtifactReference, CreateDreamRunRequest, DreamSourceReference,
    GetCandidateRequest, ApproveCandidateRequest,
)

async def propose_prompt(client: PowerContextClient, scope_id: str,
                         current: ArtifactReference, evidence_source_id: str):
    # current 必须指向已存在的 Prompt key 及其当前精确版本。
    return await client.create_dream_run(scope_id, CreateDreamRunRequest(
        operation="revise_prompt", target=current, artifacts=[current],
        sources=[DreamSourceReference(source_type="content", source_id=evidence_source_id)],
        idempotency_key="prompt-correction-2026-09-22",
    ))

async def inspect_prompt(client, scope_id, run_id):
    run = await client.get_dream_run(scope_id, run_id)
    if run.status.value == "succeeded" and run.candidate is not None:
        return await client.get_candidate(GetCandidateRequest(
            scope_id=scope_id, candidate_id=run.candidate.candidate_id))
```

在 `/dashboard/review` 检查完整提案、目标差异和精确证据。用户明确批准所检查的版本后，调用 `approve_candidate(ApproveCandidateRequest(scope_id=scope_id, candidate_id=candidate.candidate_id, expected_version=candidate.version))`。审批同时要求审核权限与 Prompt 写权限。新配置用于之后的推理；已开始的推理继续使用冻结的旧版本。回滚复用已有 Artifact replacement 接口，将选中的历史配置写为更高版本。

CLI 接受相同的 JSON 请求：

```sh
powercontext dream run --scope-id "$SCOPE_ID" --request-file prompt-dream.json
powercontext dream show --scope-id "$SCOPE_ID" "$RUN_ID"
powercontext candidate show --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext candidate revise json --request-file candidate-revision.json
powercontext candidate approve --scope-id "$SCOPE_ID" "$CANDIDATE_ID" --expected-version 2
```

`candidate-revision.json` 为完整 `ReviseCandidateRequest`，包含 scope、候选标识、expected_version、proposal、精确证据和 target。保存后产生新的待审版本，必须再次检查后才能批准。

## 标签变更

通过 `get_artifact_tags` 读取标签全集与 ETag，同时读取当前正文的精确 ArtifactRef；Atomic Memory 也使用同一套引用。仍在同一个 Dream 入口提交 `revise_tags`，以独立 `tag_target` 表达标签和正文双基准：

```python
from powercontext.http import TagDreamTarget, ArtifactTagTarget

async def propose_tags(client, scope_id, current, tags_etag, evidence_source_id):
    return await client.create_dream_run(scope_id, CreateDreamRunRequest(
        operation="revise_tags",
        tag_target=TagDreamTarget(
            target=ArtifactTagTarget(type="artifact", family=current.family,
                                     artifact_id=current.artifact_id),
            expected_etag=tags_etag, basis_ref=current),
        artifacts=[current],
        sources=[DreamSourceReference(source_type="content", source_id=evidence_source_id)],
        idempotency_key="tag-correction-2026-09-22",
    ))
```

Atomic Memory 与其他制品统一使用 `ArtifactTagTarget(family="atomic-memory", ...)` 和精确的 `basis_ref`。目标只作为比较上下文，不能作为自身 Tag 改动的依据；必须提供独立证据。成功生成后 `candidate.kind="tag"`，通过 `get_candidate(GetCandidateRequest(...))` 检查，通过 `approve_candidate(ApproveCandidateRequest(...))` 决定。统一的 `/v1/candidates/list|get|history|revise|approve|reject` 接口保留标签生命周期，批准时同时校验 ETag 和正文版本，原子替换标签并记录决定，不创建 Artifact Revision。

```sh
powercontext candidate list --candidate-kind tag --scope-id "$SCOPE_ID"
powercontext candidate show --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext candidate history --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext candidate revise json --request-file tag-revision.json
powercontext candidate approve --scope-id "$SCOPE_ID" "$CANDIDATE_ID" --expected-version 2
```

标签候选保留选中的支持制品引用，并在批准时重新检查其访问权限。修订提交完整 proposal；不能修改原始 target、expected_etag、正文基准和 before_tags 来绕过冲突。省略证据集合会保留已有证据，显式数组会替换它。

统一收件箱支持制品与标签候选筛选，两类候选共用稳定排序和分页游标，显示完整提案、证据、差异和决定历史。候选版本过期、目标变化、证据撤销或 ETag 变化时，候选保持 pending 并返回冲突；重新检查当前状态后再决定。`no_change` 和 `needs_evidence` 不创建候选。模型提案与人工批准均不等于后续任务质量已经提升。

## 修订一条 Atomic Memory

使用 `revise_memory`，在 `target` 和 `artifacts` 中提供同一个精确的 `ArtifactRef(family="atomic-memory", ...)`，并选择独立的 Source 或 Artifact 作为依据。候选正文包含 `kind` 和 `text`。生成阶段只创建待审核 Candidate；批准时重新校验目标版本、活动状态、证据与当前权限，在同一事务内发布新版本、更新索引并记录审核决定。目标已变化或被遗忘时，候选保持待审核。其他 Memory 与既有自动提取流程保持独立。
