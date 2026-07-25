# generated from openapi/powercontext.yaml; do not edit.

from pydantic import JsonValue

OPENAPI_SCHEMA: dict[str, JsonValue] = {
    "openapi": "3.0.3",
    "info": {
        "title": "PowerContext API",
        "description": "Remote PowerContext transport. Runtime behavior is reported by /v1/capabilities.",
        "version": "0.0.1",
    },
    "paths": {
        "/health/live": {
            "get": {
                "tags": ["health"],
                "summary": "Get process liveness",
                "operationId": "get_liveness",
                "responses": {
                    "200": {
                        "description": "The API process is alive.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HealthResponse"}}},
                    }
                },
            }
        },
        "/health/ready": {
            "get": {
                "tags": ["health"],
                "summary": "Get deployment readiness",
                "operationId": "get_readiness",
                "responses": {
                    "200": {
                        "description": "Required Server bindings are ready.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReadinessResponse"}}},
                    },
                    "503": {
                        "description": "Required Server bindings are not ready.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ReadinessResponse"}}},
                    },
                },
            }
        },
        "/v1/capabilities": {
            "get": {
                "tags": ["capabilities"],
                "summary": "Get runtime capabilities",
                "operationId": "get_capabilities",
                "responses": {
                    "200": {
                        "description": "Behavior enabled by the assembled runtime.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/Capabilities"}}},
                    }
                },
            }
        },
        "/v1/sources/content": {
            "post": {
                "tags": ["sources"],
                "summary": "Capture durable ContentSource evidence",
                "description": "Accept raw content as an idempotent Source without synchronously deriving Artifacts.",
                "operationId": "capture_content_source",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/CaptureContentSourceRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "202": {
                        "description": "The Source is durably stored for later processing.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {
                                "schema": {"$ref": "#/components/schemas/CaptureContentSourceResponse"}
                            }
                        },
                    },
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/flush": {
            "post": {
                "tags": ["memory"],
                "summary": "Process the pending Source window into Memory",
                "description": "Run one bounded Source-to-Memory activation for operational control and testing.",
                "operationId": "flush_memory",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/FlushMemoryRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The activation completed or found no pending Sources.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/FlushMemoryResponse"}}
                        },
                    },
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/remember": {
            "post": {
                "tags": ["memory"],
                "summary": "Remember explicit Memory content",
                "description": "Save one already-curated Memory entry "
                "without creating a Source or invoking "
                "extraction.",
                "operationId": "remember_memory",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/RememberMemoryRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The explicit Memory mutation completed.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/MemoryMutationResponse"}}
                        },
                    },
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/search": {
            "post": {
                "tags": ["memory"],
                "summary": "Search active Memory entries",
                "description": "Retrieve relevant active Memory entries within one explicit application scope.",
                "operationId": "search_memory",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SearchMemoryRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "Matching Memory entries, or an empty result when the scope has no Memory.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/SearchMemoryResponse"}}
                        },
                    },
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/entries/list": {
            "post": {
                "tags": ["memory"],
                "summary": "List Memory entries",
                "description": "Read the complete entry snapshot from the current Memory head.",
                "operationId": "list_memory_entries",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ListMemoryEntriesRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The current Memory entry snapshot.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ListMemoryEntriesResponse"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/entries/get": {
            "post": {
                "tags": ["memory"],
                "summary": "Get an exact Memory entry version",
                "description": "Resolve an immutable entry citation within one Memory Revision.",
                "operationId": "get_memory_entry",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GetMemoryEntryRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The exact Memory entry version.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/MemoryEntry"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/entries/revise": {
            "post": {
                "tags": ["memory"],
                "summary": "Revise an exact Memory entry",
                "description": "Replace active entry content against an explicit current Memory Revision.",
                "operationId": "revise_memory_entry",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ReviseMemoryEntryRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The Memory entry revision completed.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/MemoryMutationResponse"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/entries/retire": {
            "post": {
                "tags": ["memory"],
                "summary": "Retire an exact Memory entry",
                "description": "Deactivate an entry against an "
                "explicit current Memory Revision "
                "without deleting history.",
                "operationId": "retire_memory_entry",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/RetireMemoryEntryRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The Memory entry retirement completed.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/MemoryMutationResponse"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/memory/changes": {
            "post": {
                "tags": ["memory"],
                "summary": "List Memory Revision changes",
                "description": "Read compact entry changes without expanding entry bodies.",
                "operationId": "list_memory_changes",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ListMemoryChangesRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "Compact changes through the selected Memory Revision.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ListMemoryChangesResponse"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
    },
    "components": {
        "schemas": {
            "ArtifactReference": {
                "properties": {
                    "family": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "artifact_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "revision": {"type": "integer", "minimum": 1.0},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["family", "artifact_id", "revision"],
            },
            "Capabilities": {
                "properties": {
                    "source_types": {"items": {"type": "string"}, "type": "array"},
                    "artifact_families": {"items": {"type": "string"}, "type": "array"},
                    "memory_extraction": {
                        "type": "boolean",
                        "description": "Whether pending Sources can be extracted into Memory.",
                    },
                    "search_modes": {"items": {"$ref": "#/components/schemas/MemorySearchMode"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["source_types", "artifact_families", "memory_extraction", "search_modes"],
            },
            "CaptureContentSourceRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "source_id": {"type": "string", "maxLength": 256, "minLength": 1},
                    "content": {"type": "string", "maxLength": 200000, "minLength": 1},
                    "metadata": {"additionalProperties": True, "type": "object", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "source_id", "content"],
            },
            "CaptureContentSourceResponse": {
                "properties": {
                    "status": {"$ref": "#/components/schemas/CaptureStatus"},
                    "source": {"$ref": "#/components/schemas/SourceReference"},
                    "position": {"type": "integer", "minimum": 1.0},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["status", "source", "position"],
            },
            "EntryChange": {
                "properties": {
                    "op": {"$ref": "#/components/schemas/EntryChangeOperation"},
                    "entry_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "from_entry_version_id": {
                        "type": "string",
                        "maxLength": 128,
                        "minLength": 1,
                        "pattern": "^[\\x21-\\x7E]+$",
                        "nullable": True,
                    },
                    "to_entry_version_id": {
                        "type": "string",
                        "maxLength": 128,
                        "minLength": 1,
                        "pattern": "^[\\x21-\\x7E]+$",
                        "nullable": True,
                    },
                    "reason": {"type": "string", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["op", "entry_id", "from_entry_version_id", "to_entry_version_id", "reason"],
            },
            "ErrorDetail": {
                "properties": {
                    "code": {"type": "string"},
                    "message": {"type": "string"},
                    "details": {"additionalProperties": True, "type": "object", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["code", "message", "details"],
            },
            "ErrorResponse": {
                "properties": {"error": {"$ref": "#/components/schemas/ErrorDetail"}},
                "additionalProperties": False,
                "type": "object",
                "required": ["error"],
            },
            "FlushMemoryRequest": {
                "properties": {"scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"}},
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id"],
            },
            "FlushMemoryResponse": {
                "properties": {
                    "status": {"$ref": "#/components/schemas/FlushStatus"},
                    "previous_cursor": {"type": "integer", "minimum": 0.0},
                    "current_cursor": {"type": "integer", "minimum": 0.0},
                    "high_watermark": {"type": "integer", "minimum": 0.0},
                    "processed_source_count": {"type": "integer", "minimum": 0.0},
                    "memory": {"$ref": "#/components/schemas/ArtifactReference"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["status", "previous_cursor", "current_cursor", "high_watermark", "processed_source_count"],
            },
            "GetMemoryEntryRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "citation": {"$ref": "#/components/schemas/MemoryCitation"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "citation"],
            },
            "HealthResponse": {
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
                "type": "object",
                "required": ["status"],
            },
            "ListMemoryChangesRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "since_revision": {
                        "type": "integer",
                        "minimum": 0.0,
                        "description": "Exclusive "
                        "lower "
                        "bound; "
                        "0 "
                        "requests "
                        "complete "
                        "history "
                        "from "
                        "Revision "
                        "1. "
                        "Positive "
                        "nonexistent "
                        "revisions "
                        "are "
                        "errors.",
                        "nullable": True,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id"],
            },
            "ListMemoryChangesResponse": {
                "properties": {
                    "memory": {"$ref": "#/components/schemas/ArtifactReference"},
                    "revisions": {"items": {"$ref": "#/components/schemas/MemoryRevisionChanges"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["revisions"],
            },
            "ListMemoryEntriesRequest": {
                "properties": {"scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"}},
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id"],
            },
            "ListMemoryEntriesResponse": {
                "properties": {
                    "memory": {"$ref": "#/components/schemas/ArtifactReference"},
                    "entries": {"items": {"$ref": "#/components/schemas/MemoryEntry"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["entries"],
            },
            "MemoryEntry": {
                "properties": {
                    "citation": {"$ref": "#/components/schemas/MemoryCitation"},
                    "version": {"type": "integer", "minimum": 1.0},
                    "kind": {"type": "string"},
                    "text": {"type": "string"},
                    "state": {"$ref": "#/components/schemas/MemoryEntryState"},
                    "source_refs": {"items": {"$ref": "#/components/schemas/SourceReference"}, "type": "array"},
                    "artifact_refs": {"items": {"$ref": "#/components/schemas/ArtifactReference"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["citation", "version", "kind", "text", "state", "source_refs", "artifact_refs"],
            },
            "MemoryMutationResponse": {
                "properties": {
                    "memory": {"$ref": "#/components/schemas/ArtifactReference"},
                    "entry": {"$ref": "#/components/schemas/MemoryEntry"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["memory"],
            },
            "MemoryCitation": {
                "properties": {
                    "memory_ref": {"$ref": "#/components/schemas/ArtifactReference"},
                    "entry_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "entry_version_id": {
                        "type": "string",
                        "maxLength": 128,
                        "minLength": 1,
                        "pattern": "^[\\x21-\\x7E]+$",
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["memory_ref", "entry_id", "entry_version_id"],
            },
            "MemoryRevisionChanges": {
                "properties": {
                    "memory_ref": {"$ref": "#/components/schemas/ArtifactReference"},
                    "changes": {"items": {"$ref": "#/components/schemas/EntryChange"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["memory_ref", "changes"],
            },
            "ReadinessResponse": {
                "properties": {
                    "status": {"$ref": "#/components/schemas/ReadinessStatus"},
                    "checks": {"additionalProperties": {"type": "string"}, "type": "object"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["status", "checks"],
            },
            "ReadinessStatus": {"type": "string", "enum": ["ready", "not_ready"]},
            "RememberMemoryRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "kind": {"type": "string", "maxLength": 128, "minLength": 1},
                    "text": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Must not exceed 8192 UTF-8 bytes after normalization.",
                    },
                    "reason": {"type": "string", "maxLength": 512, "nullable": True},
                    "expected_revision": {"type": "integer", "minimum": 1.0, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "kind", "text"],
            },
            "RetireMemoryEntryRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "citation": {"$ref": "#/components/schemas/MemoryCitation"},
                    "reason": {"type": "string", "maxLength": 512, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "citation"],
            },
            "ReviseMemoryEntryRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "citation": {"$ref": "#/components/schemas/MemoryCitation"},
                    "kind": {"type": "string", "maxLength": 128, "minLength": 1},
                    "text": {
                        "type": "string",
                        "minLength": 1,
                        "description": "Must not exceed 8192 UTF-8 bytes after normalization.",
                    },
                    "reason": {"type": "string", "maxLength": 512, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "citation", "kind", "text"],
            },
            "SearchMemoryHit": {
                "properties": {
                    "citation": {"$ref": "#/components/schemas/MemoryCitation"},
                    "text": {"type": "string"},
                    "score": {"type": "number", "maximum": 1.0, "minimum": 0.0},
                    "matched_by": {"items": {"$ref": "#/components/schemas/MemoryMatchedBy"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["citation", "text", "score", "matched_by"],
            },
            "SearchMemoryRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "query": {"type": "string", "maxLength": 8192, "minLength": 1},
                    "limit": {"type": "integer", "maximum": 50.0, "minimum": 1.0, "default": 10},
                    "mode": {"$ref": "#/components/schemas/MemorySearchMode", "default": "auto"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "query"],
            },
            "SearchMemoryResponse": {
                "properties": {
                    "memory": {"$ref": "#/components/schemas/ArtifactReference"},
                    "mode": {"$ref": "#/components/schemas/MemoryUsedSearchMode"},
                    "hits": {"items": {"$ref": "#/components/schemas/SearchMemoryHit"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["hits"],
            },
            "SourceReference": {
                "properties": {
                    "name": {"type": "string", "description": "Stable Source type."},
                    "source_id": {"type": "string"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["name", "source_id"],
            },
            "CaptureStatus": {"type": "string", "enum": ["accepted"]},
            "EntryChangeOperation": {"type": "string", "enum": ["add", "revise", "deactivate", "reactivate"]},
            "FlushStatus": {"type": "string", "enum": ["idle", "processed"]},
            "MemoryEntryState": {"type": "string", "enum": ["active", "inactive"]},
            "MemoryMatchedBy": {"type": "string", "enum": ["fts", "vector"]},
            "MemorySearchMode": {"type": "string", "enum": ["auto", "fts", "vector", "hybrid"]},
            "MemoryUsedSearchMode": {"type": "string", "enum": ["fts", "vector", "hybrid"]},
        },
        "responses": {
            "Conflict": {
                "description": "The command conflicts with current immutable state.",
                "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
            },
            "InvalidRequest": {
                "description": "The request violates the transport or application contract.",
                "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
            },
            "NotFound": {
                "description": "The requested immutable Memory value was not found.",
                "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
            },
            "Unavailable": {
                "description": "A required Runtime binding or dependency is unavailable.",
                "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
            },
            "InternalError": {
                "description": "The Server failed without exposing internal details.",
                "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
            },
        },
        "headers": {
            "RequestId": {"description": "Opaque identifier for correlating one request.", "schema": {"type": "string"}}
        },
    },
}
