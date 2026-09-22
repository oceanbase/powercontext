---
title: 审核 Dream 修订
description: 使用现有 v1 Dream 接口提出、检查和审核 Prompt 配置与标签变更。
---

# 审核 Dream 修订

最低客户端功能级别为 **Dream contract 2**。配套 Python Client、CLI、MCP bridge 和 Dashboard 自动发送 `X-PowerContext-Dream-Contract: 2`。其他集成先更新 operation 枚举和 proposal 类型，再发送该声明。旧客户端遇到新类型时收到 HTTP 426 `client_upgrade_required`；原有操作仍可用。`GET /v1/capabilities` 列出当前已配置且可执行的操作。

Dream 使用精确目标与证据生成待审 Candidate。生成不会批准、安装、执行或发布内容；已有自动生成流程保持原有策略。审批须由用户明确决定，并携带实际检查过的候选版本。

## Prompt 配置

先读取已注册、支持 custom 模式的 Prompt key（如 `memory.extract`）的当前 ArtifactRef，再选取包含已观察错误及可信预期结果的精确 Source。通过原有 Dream 接口提交：

```python
from powercontext.client import PowerContextClient
from powercontext.http import (
    ArtifactReference, CreateDreamRunRequest, DreamSourceReference,
    GetArtifactCandidateRequest, ApproveArtifactCandidateRequest,
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
        return await client.get_artifact_candidate(GetArtifactCandidateRequest(
            scope_id=scope_id, candidate_id=run.candidate.candidate_id))
```

在 `/dashboard/review` 检查完整提案、目标差异和精确证据。用户明确批准所检查的版本后，调用 `approve_artifact_candidate(ApproveArtifactCandidateRequest(scope_id=scope_id, candidate_id=candidate.candidate_id, expected_version=candidate.version))`。审批同时要求审核权限与 Prompt 写权限。新配置用于之后的推理；已开始的推理继续使用冻结的旧版本。回滚复用已有 Artifact replacement 接口，将选中的历史配置写为更高版本。

CLI 接受相同的 JSON 请求：

```sh
powercontext dream run --scope-id "$SCOPE_ID" --request-file prompt-dream.json
powercontext dream show --scope-id "$SCOPE_ID" "$RUN_ID"
powercontext candidate show --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext candidate revise json --request-file candidate-revision.json
powercontext candidate approve --scope-id "$SCOPE_ID" "$CANDIDATE_ID" --expected-version 2
```

`candidate-revision.json` 为完整 `ReviseArtifactCandidateRequest`，包含 scope、候选标识、expected_version、proposal、精确证据和 target。保存后产生新的待审版本，必须再次检查后才能批准。

## 标签变更

通过 `get_artifact_tags` 或 `get_memory_entry_tags` 读取标签全集与 ETag，同时读取当前正文的精确 ArtifactRef 或 MemoryCitation。仍在同一个 Dream 入口提交 `revise_tags`，以独立 `tag_target` 表达标签和正文双基准：

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

Memory 条目使用 `MemoryEntryTagTarget` 与 `basis_citation`，并在 `memory_citations` 中提供该精确引用。成功生成后 `candidate.kind="catalog_change"`，通过 `get_catalog_candidate(GetCatalogCandidateRequest(...))` 检查，通过 `approve_catalog_candidate(ApproveCatalogCandidateRequest(...))` 决定。独立的 `/v1/catalog-change-candidates/list|get|history|revise|approve|reject` 接口保留标签生命周期，批准时同时校验 ETag 和正文版本，原子替换标签并记录决定，不创建 Artifact Revision。

```sh
powercontext catalog-candidate list --scope-id "$SCOPE_ID"
powercontext catalog-candidate show --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext catalog-candidate history --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext catalog-candidate revise --request-file tag-revision.json
powercontext catalog-candidate approve --scope-id "$SCOPE_ID" "$CANDIDATE_ID" --expected-version 2
```

统一收件箱支持制品与标签候选筛选，两类资源分别分页，显示完整提案、证据、差异和决定历史。候选版本过期、目标变化、证据撤销或 ETag 变化时，候选保持 pending 并返回冲突；重新检查当前状态后再决定。`no_change` 和 `needs_evidence` 不创建候选。模型提案与人工批准均不等于后续任务质量已经提升。
