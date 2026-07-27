# generated from openapi/powercontext.yaml; do not edit.

from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, JsonValue

from powercontext.http._generated.models import (
    Capabilities,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    FlushMemoryRequest,
    FlushMemoryResponse,
    GetMemoryEntryRequest,
    HealthResponse,
    ListMemoryChangesRequest,
    ListMemoryChangesResponse,
    ListMemoryEntriesRequest,
    ListMemoryEntriesResponse,
    MemoryEntry,
    MemoryMutationResponse,
    ReadinessResponse,
    RememberMemoryRequest,
    RetireMemoryEntryRequest,
    ReviseMemoryEntryRequest,
    SearchMemoryRequest,
    SearchMemoryResponse,
)

OPENAPI_VERSION = "3.0.3"
API_TITLE = "PowerContext API"
API_DESCRIPTION = "Remote PowerContext transport. Runtime behavior is reported by /v1/capabilities."
API_VERSION = "0.0.1"

RequestT = TypeVar("RequestT")
ResponseT = TypeVar("ResponseT")


class Operation(BaseModel, Generic[RequestT, ResponseT]):
    method: str
    path: str
    operation_id: str
    request_type: type[RequestT] | None
    response_type: type[ResponseT]
    success_status: int
    summary: str
    tags: tuple[str, ...]
    responses: dict[int | str, dict[str, JsonValue]]


GET_LIVENESS = Operation[None, HealthResponse](
    method="GET",
    path="/health/live",
    operation_id="get_liveness",
    request_type=None,
    response_type=HealthResponse,
    success_status=200,
    summary="Get process liveness",
    tags=("health",),
    responses={
        200: {
            "description": "The API process is alive.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        }
    },
)

GET_READINESS = Operation[None, ReadinessResponse](
    method="GET",
    path="/health/ready",
    operation_id="get_readiness",
    request_type=None,
    response_type=ReadinessResponse,
    success_status=200,
    summary="Get deployment readiness",
    tags=("health",),
    responses={
        200: {
            "description": "Required Server bindings are ready.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        503: {
            "description": "Required Server bindings are not ready.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
    },
)

GET_CAPABILITIES = Operation[None, Capabilities](
    method="GET",
    path="/v1/capabilities",
    operation_id="get_capabilities",
    request_type=None,
    response_type=Capabilities,
    success_status=200,
    summary="Get runtime capabilities",
    tags=("capabilities",),
    responses={
        200: {
            "description": "Behavior enabled by the assembled runtime.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        }
    },
)

CAPTURE_CONTENT_SOURCE = Operation[CaptureContentSourceRequest, CaptureContentSourceResponse](
    method="POST",
    path="/v1/sources/content",
    operation_id="capture_content_source",
    request_type=CaptureContentSourceRequest,
    response_type=CaptureContentSourceResponse,
    success_status=202,
    summary="Capture durable ContentSource evidence",
    tags=("sources",),
    responses={
        202: {
            "description": "The Source is durably stored for later processing.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

FLUSH_MEMORY = Operation[FlushMemoryRequest, FlushMemoryResponse](
    method="POST",
    path="/v1/memory/flush",
    operation_id="flush_memory",
    request_type=FlushMemoryRequest,
    response_type=FlushMemoryResponse,
    success_status=200,
    summary="Process the pending Source window into Memory",
    tags=("memory",),
    responses={
        200: {
            "description": "The activation completed or found no pending Sources.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REMEMBER_MEMORY = Operation[RememberMemoryRequest, MemoryMutationResponse](
    method="POST",
    path="/v1/memory/remember",
    operation_id="remember_memory",
    request_type=RememberMemoryRequest,
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Remember explicit Memory content",
    tags=("memory",),
    responses={
        200: {
            "description": "The explicit Memory mutation completed.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

SEARCH_MEMORY = Operation[SearchMemoryRequest, SearchMemoryResponse](
    method="POST",
    path="/v1/memory/search",
    operation_id="search_memory",
    request_type=SearchMemoryRequest,
    response_type=SearchMemoryResponse,
    success_status=200,
    summary="Search active Memory entries",
    tags=("memory",),
    responses={
        200: {
            "description": "Matching Memory entries, or an empty result when the scope has no Memory.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_MEMORY_ENTRIES = Operation[ListMemoryEntriesRequest, ListMemoryEntriesResponse](
    method="POST",
    path="/v1/memory/entries/list",
    operation_id="list_memory_entries",
    request_type=ListMemoryEntriesRequest,
    response_type=ListMemoryEntriesResponse,
    success_status=200,
    summary="List Memory entries",
    tags=("memory",),
    responses={
        200: {
            "description": "The current Memory entry snapshot.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

GET_MEMORY_ENTRY = Operation[GetMemoryEntryRequest, MemoryEntry](
    method="POST",
    path="/v1/memory/entries/get",
    operation_id="get_memory_entry",
    request_type=GetMemoryEntryRequest,
    response_type=MemoryEntry,
    success_status=200,
    summary="Get an exact Memory entry version",
    tags=("memory",),
    responses={
        200: {
            "description": "The exact Memory entry version.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

REVISE_MEMORY_ENTRY = Operation[ReviseMemoryEntryRequest, MemoryMutationResponse](
    method="POST",
    path="/v1/memory/entries/revise",
    operation_id="revise_memory_entry",
    request_type=ReviseMemoryEntryRequest,
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Revise an exact Memory entry",
    tags=("memory",),
    responses={
        200: {
            "description": "The Memory entry revision completed.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

RETIRE_MEMORY_ENTRY = Operation[RetireMemoryEntryRequest, MemoryMutationResponse](
    method="POST",
    path="/v1/memory/entries/retire",
    operation_id="retire_memory_entry",
    request_type=RetireMemoryEntryRequest,
    response_type=MemoryMutationResponse,
    success_status=200,
    summary="Retire an exact Memory entry",
    tags=("memory",),
    responses={
        200: {
            "description": "The Memory entry retirement completed.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        409: {"$ref": "#/components/responses/Conflict"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)

LIST_MEMORY_CHANGES = Operation[ListMemoryChangesRequest, ListMemoryChangesResponse](
    method="POST",
    path="/v1/memory/changes",
    operation_id="list_memory_changes",
    request_type=ListMemoryChangesRequest,
    response_type=ListMemoryChangesResponse,
    success_status=200,
    summary="List Memory Revision changes",
    tags=("memory",),
    responses={
        200: {
            "description": "Compact changes through the selected Memory Revision.",
            "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
        },
        404: {"$ref": "#/components/responses/NotFound"},
        422: {"$ref": "#/components/responses/InvalidRequest"},
        503: {"$ref": "#/components/responses/Unavailable"},
        500: {"$ref": "#/components/responses/InternalError"},
    },
)
