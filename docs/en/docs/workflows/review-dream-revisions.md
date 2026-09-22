---
title: Review Dream revisions
description: Propose and review Prompt configuration and Tag changes using the existing v1 Dream API.
---

# Review Dream revisions

Use a coordinated Client supporting **Dream contract 2**. The Python Client and CLI send `X-PowerContext-Dream-Contract: 2` automatically. External integrations must send the header after updating their closed operation enums and proposal types. A legacy Client receives HTTP 426 `client_upgrade_required` when it encounters a new type. `GET /v1/capabilities` advertises the enabled operations; unconfigured operations reject admission.

Dream selects exact evidence and creates a pending Candidate. Generation does not approve, install, execute, or publish anything. Existing automatic generation retains its own policy. Approval is an explicit human decision against the exact current Candidate version.

## Prompt configuration

Read the current Prompt Artifact revision for a registered custom-capable key, such as `memory.extract`, and choose an exact Source describing an observed error and its expected output. The same `/v1/scopes/{scope_id}/dream` route handles the request:

```python
from powercontext.client import PowerContextClient
from powercontext.http import (
    ArtifactReference, CreateDreamRunRequest, DreamSourceReference,
    GetArtifactCandidateRequest, ApproveArtifactCandidateRequest,
)

async def propose_prompt(client: PowerContextClient, scope_id: str,
                         current: ArtifactReference, evidence_source_id: str):
    # current names the existing Prompt key and its exact current revision.
    request = CreateDreamRunRequest(
        operation="revise_prompt", target=current, artifacts=[current],
        sources=[DreamSourceReference(source_type="content", source_id=evidence_source_id)],
        idempotency_key="prompt-correction-2026-09-22",
    )
    return await client.create_dream_run(scope_id, request)

async def inspect_prompt(client: PowerContextClient, scope_id: str, run_id: str):
    run = await client.get_dream_run(scope_id, run_id)
    if run.status.value == "succeeded" and run.candidate is not None:
        return await client.get_artifact_candidate(GetArtifactCandidateRequest(
            scope_id=scope_id, candidate_id=run.candidate.candidate_id))
```

Read the proposal, reason, exact evidence, and diff in `/dashboard/review`. Only after a human approves the inspected version, call `approve_artifact_candidate(ApproveArtifactCandidateRequest(scope_id=scope_id, candidate_id=candidate.candidate_id, expected_version=candidate.version))`. Approval requires review authority and Prompt write authority. It publishes the configuration for future inferences; in-flight inference retains its frozen revision. Rollback uses the existing Artifact replacement API to write the selected earlier configuration as a higher revision.

The CLI accepts the same JSON request:

```sh
powercontext dream run --scope-id "$SCOPE_ID" --request-file prompt-dream.json
powercontext dream show --scope-id "$SCOPE_ID" "$RUN_ID"
powercontext candidate show --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext candidate revise json --request-file candidate-revision.json
powercontext candidate approve --scope-id "$SCOPE_ID" "$CANDIDATE_ID" --expected-version 2
```

`candidate-revision.json` is a complete `ReviseArtifactCandidateRequest`, including scope, identity, expected version, proposal, exact evidence, and target. Saving a revision creates a new pending version requiring review.

## Tag metadata

Read the current target Tag set and its ETag with `get_artifact_tags` or `get_memory_entry_tags`, and read the exact Artifact revision or Memory citation that provides the content baseline. Submit `revise_tags` on the same Dream route. Its `tag_target` is separate from the Artifact `target`:

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

For an entry target use `MemoryEntryTagTarget` and `basis_citation`, and include that citation in `memory_citations`. The resulting run has `candidate.kind="catalog_change"`. Inspect it using `get_catalog_candidate(GetCatalogCandidateRequest(...))`; approval uses `approve_catalog_candidate(ApproveCatalogCandidateRequest(...))`. The dedicated `/v1/catalog-change-candidates/list|get|history|revise|approve|reject` interfaces preserve Tag's distinct lifecycle. Approval checks both the Tag ETag and content baseline atomically. The result contains the complete tags and new ETag, with no Artifact revision.

```sh
powercontext catalog-candidate list --scope-id "$SCOPE_ID"
powercontext catalog-candidate show --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext catalog-candidate history --scope-id "$SCOPE_ID" "$CANDIDATE_ID"
powercontext catalog-candidate revise --request-file tag-revision.json
powercontext catalog-candidate approve --scope-id "$SCOPE_ID" "$CANDIDATE_ID" --expected-version 2
```

The unified Dashboard filters Artifact and Catalog Change resources and shows their independent paginated sections. A stale version, changed target, revoked evidence, or changed Tag ETag leaves the candidate pending with a conflict. Inspect current state before submitting another decision. `no_change` and `needs_evidence` create no candidate. Neither a model proposal nor approval proves improvement on later tasks.
