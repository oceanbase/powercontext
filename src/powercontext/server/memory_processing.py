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

"""Read processing history through the owning runtime and its authorization."""

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, Path, Query, Request

from powercontext.artifacts import ArtifactRef
from powercontext.builtin.records import BaseOperationNotSupportedError, InvalidBaseAccessRequestError
from powercontext.builtin.runtime.atomic_memory import deferred_decision_audit
from powercontext.http._generated import models, operations
from powercontext.sources import SourceRef

ProcessingStatus = Literal["started", "succeeded", "failed", "cancelled", "conflict"]


def _check_query(request: Request, allowed: set[str]) -> None:
    if set(request.query_params) - allowed or any(len(request.query_params.getlist(key)) > 1 for key in allowed):
        raise InvalidBaseAccessRequestError("query", "contains unknown or repeated parameters")


def _reader(application):
    reader = getattr(application, "read_snapshot", None)
    if reader is None:
        raise BaseOperationNotSupportedError("artifact_family", "atomic-memory", "processing history")
    return reader


def add_memory_processing_routes(app, *, application_dependency, add_route, execution_context):
    async def read(request, application, scope_id, limit, cursor, operation, **selector):
        context = execution_context(request, operation)
        async with deferred_decision_audit(context), _reader(application)(scope_id) as snapshot:
            return await snapshot.memory_processing(limit=limit, cursor=cursor, context=context, **selector)

    async def scope_history(
        scope_id: Annotated[str, Path(min_length=1, max_length=256)],
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
        cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
        status: Annotated[ProcessingStatus | None, Query()] = None,
        since: Annotated[datetime | None, Query()] = None,
        until: Annotated[datetime | None, Query()] = None,
    ) -> models.MemoryProcessingPage:
        _check_query(http_request, {"limit", "cursor", "status", "since", "until"})
        context = execution_context(http_request, "list_scope_memory_processing")
        async with deferred_decision_audit(context), _reader(application)(scope_id) as snapshot:
            value = await snapshot.processing_attempts(
                limit=limit,
                cursor=cursor,
                status=status,
                since=since,
                until=until,
                context=context,
            )
        return models.MemoryProcessingPage.model_validate(value)

    async def scope_summary(
        scope_id: Annotated[str, Path(min_length=1, max_length=256)],
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
        since: Annotated[datetime | None, Query()] = None,
        until: Annotated[datetime | None, Query()] = None,
    ) -> models.MemoryProcessingSummary:
        _check_query(http_request, {"since", "until"})
        context = execution_context(http_request, "get_scope_memory_processing_summary")
        async with deferred_decision_audit(context), _reader(application)(scope_id) as snapshot:
            value = await snapshot.processing_summary(since=since, until=until, context=context)
        return models.MemoryProcessingSummary.model_validate(value)

    async def source_history(
        scope_id: Annotated[str, Path(min_length=1, max_length=256)],
        source_type: Literal["content"],
        source_id: Annotated[str, Path(min_length=1, max_length=256)],
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
        cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
    ) -> models.MemoryProcessingPage:
        _check_query(http_request, {"limit", "cursor"})
        value = await read(
            http_request,
            application,
            scope_id,
            limit,
            cursor,
            "list_source_memory_processing",
            source=SourceRef(source_type=source_type, source_id=source_id),
        )
        return models.MemoryProcessingPage.model_validate(value)

    async def revision_history(
        scope_id: Annotated[str, Path(min_length=1, max_length=256)],
        artifact_id: Annotated[str, Path(min_length=1, max_length=128)],
        revision: Annotated[int, Path(ge=1)],
        http_request: Request,
        application: Annotated[Any, Depends(application_dependency)],
        limit: Annotated[int, Query(ge=1, le=20)] = 10,
        cursor: Annotated[str | None, Query(min_length=1, max_length=4096)] = None,
    ) -> models.MemoryProcessingPage:
        _check_query(http_request, {"limit", "cursor"})
        value = await read(
            http_request,
            application,
            scope_id,
            limit,
            cursor,
            "list_memory_revision_processing",
            artifact=ArtifactRef(family="atomic-memory", artifact_id=artifact_id, revision=revision),
        )
        return models.MemoryProcessingPage.model_validate(value)

    add_route(app, operations.GET_SCOPE_MEMORY_PROCESSING_SUMMARY, scope_summary)
    add_route(app, operations.LIST_SCOPE_MEMORY_PROCESSING, scope_history)
    add_route(app, operations.LIST_SOURCE_MEMORY_PROCESSING, source_history)
    add_route(app, operations.LIST_MEMORY_REVISION_PROCESSING, revision_history)
