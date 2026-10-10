# Copyright (c) 2026 OceanBase.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""HTTP adapters for the Atomic Memory Runtime application."""

from typing import Annotated, Any

from fastapi import Depends, Header, Path, Request, Response

from powercontext.artifacts import ArtifactLineage, ArtifactRef
from powercontext.builtin.artifacts.atomic_memory import AtomicMemoryContent, AtomicMemoryRead, AtomicMemoryStateValue
from powercontext.builtin.records import BaseOperationNotSupportedError, InvalidBaseAccessRequestError
from powercontext.builtin.tags import TagFilter
from powercontext.http._generated import models, operations
from powercontext.sources import SourceRef


def record_response(record) -> models.AtomicMemoryRecord:
    return models.AtomicMemoryRecord.model_validate({
        "artifact": record.ref.model_dump(mode="json"),
        "kind": record.artifact.content.kind,
        "text": record.artifact.content.text,
        "state": record.state.state.value,
        "state_version": record.state.state_version,
        "merged_into_id": record.state.merged_into_id,
    })


def _tag_filter(request):
    if request.tags is None:
        if request.tag_match is not None:
            raise InvalidBaseAccessRequestError("tag_match", "requires tags")
        return None
    return TagFilter(tags=tuple(request.tags), match="all" if request.tag_match is None else request.tag_match.value)


def _scoped(application, scope_id):
    atomic = getattr(application, "atomic_memory", None)
    if atomic is None:
        raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "runtime application")
    return atomic.for_scope(scope_id)


def add_atomic_memory_routes(  # noqa: C901
    app,
    *,
    application_dependency,
    add_route,
    execution_context,
):
    async def get_atomic_memory_state(
        scope_id: Annotated[str, Path(min_length=1, max_length=256)],
        artifact_id: Annotated[str, Path(min_length=1, max_length=128)],
        http_request: Request,
        response: Response,
        application: Annotated[Any, Depends(application_dependency)],
        if_none_match: Annotated[str | None, Header(alias="If-None-Match")] = None,
    ) -> models.AtomicMemoryStateResponse | Response:
        record = await _scoped(application, scope_id).get(
            artifact_id, context=execution_context(http_request, "get_atomic_memory_state")
        )
        etag = f'"revision:{record.artifact.revision}:state:{record.state.state_version}"'
        if if_none_match is not None and any(
            item.strip().removeprefix("W/") in {etag, "*"} for item in if_none_match.split(",")
        ):
            return Response(status_code=304, headers={"ETag": etag})
        response.headers["ETag"] = etag
        return models.AtomicMemoryStateResponse.model_validate({
            "artifact": record.ref.model_dump(mode="json"),
            **record.state.model_dump(mode="json"),
        })

    async def list_atomic_memories(
        request: models.ListAtomicMemoryRequest,
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
    ) -> models.ListAtomicMemoryResponse:
        result = await _scoped(application, request.scope_id).list(
            states=tuple(state.value for state in request.states) if request.states is not None else ("active",),
            kind=request.kind,
            tag_filter=_tag_filter(request),
            limit=request.limit,
            cursor=request.cursor,
            context=execution_context(http_request, "list_atomic_memories"),
        )
        return models.ListAtomicMemoryResponse(
            items=[record_response(item) for item in result.items], next_cursor=result.next_cursor
        )

    async def search_atomic_memory(
        request: models.SearchAtomicMemoryRequest,
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
    ) -> models.SearchAtomicMemoryResponse:
        result = await _scoped(application, request.scope_id).search(
            request.query,
            mode=request.mode.value,
            limit=request.limit,
            kind=request.kind,
            tag_filter=_tag_filter(request),
            context=execution_context(http_request, "search_atomic_memory"),
        )
        return models.SearchAtomicMemoryResponse.model_validate({
            "mode": result.mode,
            "hits": [
                {
                    "memory": {
                        "artifact": item.hit.artifact_ref.model_dump(mode="json"),
                        "kind": item.hit.kind,
                        "text": item.hit.text,
                        "state": "active",
                        "state_version": item.hit.state_version,
                        "merged_into_id": None,
                    },
                    "score": float(item.hit.score),
                    "matched_by": list(item.matched_by),
                }
                for item in result.hits
            ],
        })

    async def merge_atomic_memories(
        request: models.MergeAtomicMemoryRequest,
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
    ) -> models.AtomicMemoryMutationResponse:
        inputs = tuple(
            AtomicMemoryRead(
                ref=ArtifactRef.model_validate(item.artifact.model_dump(mode="json")),
                state=AtomicMemoryStateValue.ACTIVE,
                state_version=item.state_version,
            )
            for item in request.inputs
        )
        artifact_refs = (
            *[item.ref for item in inputs],
            *[ArtifactRef.model_validate(item.model_dump(mode="json")) for item in request.artifact_refs or ()],
        )
        lineage = ArtifactLineage(
            sources=tuple(SourceRef.model_validate(item.model_dump(mode="json")) for item in request.source_refs or ()),
            artifacts=tuple({(ref.family, ref.artifact_id, ref.revision): ref for ref in artifact_refs}.values()),
        )
        result = await _scoped(application, request.scope_id).merge(
            inputs,
            AtomicMemoryContent.model_validate(request.content.model_dump(mode="json", by_alias=True)),
            lineage=lineage,
            context=execution_context(http_request, "merge_atomic_memories"),
        )
        return models.AtomicMemoryMutationResponse(
            changed=result.changed, records=[record_response(item) for item in result.records]
        )

    async def change_atomic_memory_lifecycle(
        request: models.AtomicMemoryLifecycleRequest,
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
    ) -> models.AtomicMemoryMutationResponse:
        if request.target.artifact.family != "atomic-memory":
            raise InvalidBaseAccessRequestError("target.artifact.family", "must be atomic-memory")
        result = await _scoped(application, request.scope_id).forget(
            request.target.artifact.artifact_id,
            expected_revision=request.target.artifact.revision,
            expected_state_version=request.target.state_version,
            context=execution_context(http_request, "change_atomic_memory_lifecycle"),
        )
        return models.AtomicMemoryMutationResponse(
            changed=result.changed, records=[record_response(item) for item in result.records]
        )

    async def preview_atomic_memory_restoration(
        request: models.AtomicMemoryRestorationPreviewRequest,
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
    ) -> models.AtomicMemoryRestorationPreview:
        result = await _scoped(application, request.scope_id).preview_restoration(
            request.target.artifact_id,
            operation=request.operation.value,
            revision=request.target.revision,
            context=execution_context(http_request, "preview_atomic_memory_restoration"),
        )
        return models.AtomicMemoryRestorationPreview.model_validate({
            "preview_token": result.preview_token,
            "expires_at": result.expires_at,
            "endpoint": {
                "artifact_id": result.endpoint.ref.artifact_id,
                "revision": result.endpoint.ref.revision,
                "state_version": result.endpoint.state_version,
            },
            "restore": [item.model_dump(mode="json") for item in result.restore],
            "retire": [ref.model_dump(mode="json") for ref in result.retire],
            "undo_merge_results": list(result.undo_merge_results),
        })

    async def restore_atomic_memory(
        request: models.AtomicMemoryRestorationRequest,
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
    ) -> models.AtomicMemoryRestorationResponse:
        result = await _scoped(application, request.scope_id).restore(
            request.target.artifact_id,
            operation=request.operation.value,
            revision=request.target.revision,
            preview_token=request.preview_token,
            context=execution_context(http_request, "restore_atomic_memory"),
        )
        return models.AtomicMemoryRestorationResponse.model_validate({
            "changed": result.changed,
            "restored": [ref.model_dump(mode="json") for ref in result.restored],
            "retired": [ref.model_dump(mode="json") for ref in result.retired],
            "undo_merge_results": list(result.undo_merge_results),
        })

    for operation, endpoint in (
        (operations.GET_ATOMIC_MEMORY_STATE, get_atomic_memory_state),
        (operations.LIST_ATOMIC_MEMORIES, list_atomic_memories),
        (operations.SEARCH_ATOMIC_MEMORY, search_atomic_memory),
        (operations.MERGE_ATOMIC_MEMORIES, merge_atomic_memories),
        (operations.CHANGE_ATOMIC_MEMORY_LIFECYCLE, change_atomic_memory_lifecycle),
        (operations.PREVIEW_ATOMIC_MEMORY_RESTORATION, preview_atomic_memory_restoration),
        (operations.RESTORE_ATOMIC_MEMORY, restore_atomic_memory),
    ):
        add_route(app, operation, endpoint)
