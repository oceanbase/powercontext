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
                "security": [],
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
                "security": [],
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
                    },
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/context/prepare": {
            "post": {
                "tags": ["context"],
                "summary": "Prepare bounded context for an Agent turn",
                "description": "Prepare final, ephemeral context from "
                "Runtime-owned sources without "
                "persisting or injecting it.",
                "operationId": "prepare_context",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PrepareContextRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "Final context ready for direct injection, or a normal empty result.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PreparedContext"}}},
                    },
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/handoff/activate": {
            "post": {
                "tags": ["handoff"],
                "summary": "Activate Handoff generation at a Source boundary",
                "description": "Evaluate the standard Handoff Trigger "
                "and synchronously execute any emitted "
                "PrepareHandoff Action.",
                "operationId": "activate_handoff",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ActivateHandoffRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "A generated "
                        "inspectable "
                        "Draft, or an "
                        "ignored "
                        "boundary that "
                        "was already "
                        "consumed.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HandoffActivation"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/handoff/prepare": {
            "post": {
                "tags": ["handoff"],
                "summary": "Generate an inspectable Handoff Draft",
                "operationId": "prepare_handoff",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PrepareHandoffRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "An uncommitted Draft generated from the selected exact evidence.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HandoffDraft"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/handoff/finalize": {
            "post": {
                "tags": ["handoff"],
                "summary": "Finalize an inspected Handoff Draft",
                "operationId": "finalize_handoff",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/FinalizeHandoffRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "A temporary Handoff ready for direct transfer or explicit commit.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/PreparedHandoff"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/handoff/commit": {
            "post": {
                "tags": ["handoff"],
                "summary": "Commit an explicit Handoff milestone",
                "operationId": "commit_handoff",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CommitHandoffRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The committed immutable Handoff Revision.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/CommittedHandoff"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/handoff/continue": {
            "post": {
                "tags": ["handoff"],
                "summary": "Resolve a Handoff as untrusted historical input",
                "operationId": "continue_handoff",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ContinueHandoffRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "Resolved content and per-statement evidence availability.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/HandoffResolution"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                "description": "Read active entries from the "
                "current Memory head. Inactive "
                "entries are available only when "
                "explicitly requested for audit.",
                "operationId": "list_memory_entries",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ListMemoryEntriesRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The selected entries from the current Memory head.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ListMemoryEntriesResponse"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
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
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/experience/propose": {
            "post": {
                "tags": ["experience"],
                "summary": "Propose Experience content",
                "description": "Persist a pending Experience Candidate without creating an Artifact Revision.",
                "operationId": "propose_experience",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ProposeExperienceRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "201": {
                        "description": "The pending Experience Candidate.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ArtifactCandidate"}}},
                    },
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/experience/generate": {
            "post": {
                "tags": ["experience"],
                "summary": "Generate an Experience Candidate",
                "description": "Use the configured model and "
                "caller-selected exact evidence; "
                "persist only a schema-valid "
                "pending Candidate.",
                "operationId": "generate_experience",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/GenerateExperienceRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "A pending Candidate or an explicit semantic no-op.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/GeneratedCandidateResponse"}}
                        },
                    },
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/experience/get": {
            "post": {
                "tags": ["experience"],
                "summary": "Get an exact Experience Revision",
                "description": "Read approved Experience content and its exact direct evidence.",
                "operationId": "get_experience",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GetExperienceRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The exact approved Experience Revision.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ExperienceArtifact"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/skill/propose": {
            "post": {
                "tags": ["skill"],
                "summary": "Propose managed Skill content",
                "description": "Persist a pending managed Skill Candidate without creating an Artifact Revision.",
                "operationId": "propose_skill",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ProposeSkillRequest"}}},
                    "required": True,
                },
                "responses": {
                    "201": {
                        "description": "The pending managed Skill Candidate.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ArtifactCandidate"}}},
                    },
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/skill/generate": {
            "post": {
                "tags": ["skill"],
                "summary": "Generate a managed Skill Candidate",
                "description": "Use the configured model with an "
                "explicit provenance shape; persist only "
                "a schema-valid pending Candidate.",
                "operationId": "generate_skill",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GenerateSkillRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "A pending Candidate or an explicit semantic no-op.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/GeneratedCandidateResponse"}}
                        },
                    },
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/skill/get": {
            "post": {
                "tags": ["skill"],
                "summary": "Get an exact managed Skill Revision",
                "description": "Read approved managed Skill content and its exact direct evidence.",
                "operationId": "get_skill",
                "requestBody": {
                    "content": {"application/json": {"schema": {"$ref": "#/components/schemas/GetSkillRequest"}}},
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The exact approved managed Skill Revision.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/SkillArtifact"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/external-skills/scan": {
            "post": {
                "tags": ["skill"],
                "summary": "Scan configured external Skill roots",
                "description": "Replace the current host-local "
                "Registry projection without "
                "copying or rewriting package "
                "content.",
                "operationId": "scan_external_skills",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ScanExternalSkillsRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The rebuildable provider snapshot.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ScanExternalSkillsResponse"}}
                        },
                    },
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/external-skills/list": {
            "post": {
                "tags": ["skill"],
                "summary": "List external Skills visible on this host",
                "description": "Return live local resolutions; "
                "unavailable registrations are "
                "omitted unless explicitly "
                "requested.",
                "operationId": "list_external_skills",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ListExternalSkillsRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "External "
                        "Skills "
                        "resolved "
                        "against the "
                        "current "
                        "Agent, "
                        "host, "
                        "scope, and "
                        "fingerprint.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ListExternalSkillsResponse"}}
                        },
                    },
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/external-skills/resolve": {
            "post": {
                "tags": ["skill"],
                "summary": "Resolve an exact external Skill fingerprint",
                "description": "Resolve only the registered "
                "local package version "
                "requested by the caller; never "
                "install or fall back.",
                "operationId": "resolve_external_skill",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ResolveExternalSkillRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The live exact-resolution result, which may be unavailable.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ExternalSkillResolution"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/external-skills/import": {
            "post": {
                "tags": ["skill"],
                "summary": "Import or fork an external Skill into Review",
                "description": "Capture one exact local "
                "snapshot and use the configured "
                "model to propose a new managed "
                "Skill Candidate.",
                "operationId": "import_external_skill",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ImportExternalSkillRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "A pending managed Skill Candidate or an explicit semantic no-op.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/GeneratedCandidateResponse"}}
                        },
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/artifact-candidates/list": {
            "post": {
                "tags": ["review"],
                "summary": "List Artifact Candidates",
                "description": "Page current Candidate heads; pending is the default Review Inbox view.",
                "operationId": "list_artifact_candidates",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ListArtifactCandidatesRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The selected current Candidate heads.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {
                            "application/json": {"schema": {"$ref": "#/components/schemas/ArtifactCandidatePage"}}
                        },
                    },
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/artifact-candidates/get": {
            "post": {
                "tags": ["review"],
                "summary": "Get an Artifact Candidate",
                "description": "Read the current head and exact immutable proposal version.",
                "operationId": "get_artifact_candidate",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/GetArtifactCandidateRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The current Candidate head.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ArtifactCandidate"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/artifact-candidates/approve": {
            "post": {
                "tags": ["review"],
                "summary": "Approve an Artifact Candidate",
                "description": "Commit the reviewed proposal and mark the Candidate approved in one transaction.",
                "operationId": "approve_artifact_candidate",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ApproveArtifactCandidateRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The approved Candidate and exact result Artifact.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ArtifactCandidate"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/artifact-candidates/reject": {
            "post": {
                "tags": ["review"],
                "summary": "Reject an Artifact Candidate",
                "description": "Move the exact pending "
                "version to its rejected "
                "terminal state without "
                "writing an Artifact.",
                "operationId": "reject_artifact_candidate",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/RejectArtifactCandidateRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The rejected Candidate.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ArtifactCandidate"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
        "/v1/artifact-candidates/revise": {
            "post": {
                "tags": ["review"],
                "summary": "Revise an Artifact Candidate",
                "description": "Append a complete replacement proposal as the next immutable pending version.",
                "operationId": "revise_artifact_candidate",
                "requestBody": {
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/ReviseArtifactCandidateRequest"}}
                    },
                    "required": True,
                },
                "responses": {
                    "200": {
                        "description": "The next pending Candidate version.",
                        "headers": {"X-Request-ID": {"$ref": "#/components/headers/RequestId"}},
                        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ArtifactCandidate"}}},
                    },
                    "404": {"$ref": "#/components/responses/NotFound"},
                    "409": {"$ref": "#/components/responses/Conflict"},
                    "401": {"$ref": "#/components/responses/Unauthorized"},
                    "422": {"$ref": "#/components/responses/InvalidRequest"},
                    "503": {"$ref": "#/components/responses/Unavailable"},
                    "500": {"$ref": "#/components/responses/InternalError"},
                },
            }
        },
    },
    "components": {
        "schemas": {
            "ActivateHandoffRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "boundary_source": {"$ref": "#/components/schemas/SourceReference"},
                    "objective": {"type": "string", "maxLength": 8192, "minLength": 1, "pattern": ".*\\S.*"},
                    "evidence": {
                        "items": {"$ref": "#/components/schemas/HandoffCitation"},
                        "type": "array",
                        "maxItems": 32,
                        "default": [],
                    },
                    "max_bytes": {"type": "integer", "maximum": 32768.0, "minimum": 512.0, "default": 8000},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "boundary_source", "objective"],
            },
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
            "ArtifactCandidate": {
                "properties": {
                    "candidate_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "version": {"type": "integer", "minimum": 1.0},
                    "family": {"$ref": "#/components/schemas/CandidateFamily"},
                    "status": {"$ref": "#/components/schemas/CandidateStatus"},
                    "proposal": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/ExperienceProposal"},
                            {"$ref": "#/components/schemas/SkillProposal"},
                        ]
                    },
                    "source_refs": {
                        "items": {"$ref": "#/components/schemas/SourceReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Source "
                        "evidence. "
                        "Counted "
                        "with "
                        "artifact_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "artifact_refs": {
                        "items": {"$ref": "#/components/schemas/ArtifactReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Artifact "
                        "evidence. "
                        "Counted "
                        "with "
                        "source_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "target": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                    "result_artifact": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "decision_reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": [
                    "candidate_id",
                    "version",
                    "family",
                    "status",
                    "proposal",
                    "source_refs",
                    "artifact_refs",
                    "target",
                    "reason",
                    "result_artifact",
                    "decision_reason",
                ],
            },
            "ArtifactCandidatePage": {
                "properties": {
                    "candidates": {"items": {"$ref": "#/components/schemas/ArtifactCandidate"}, "type": "array"},
                    "next_cursor": {"type": "string", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["candidates", "next_cursor"],
            },
            "ApproveArtifactCandidateRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "candidate_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "expected_version": {"type": "integer", "minimum": 1.0},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "candidate_id", "expected_version"],
            },
            "Capabilities": {
                "properties": {
                    "source_types": {"items": {"type": "string"}, "type": "array"},
                    "artifact_families": {"items": {"type": "string"}, "type": "array"},
                    "memory_extraction": {
                        "type": "boolean",
                        "description": "Whether pending Sources can be extracted into Memory.",
                    },
                    "experience_generation": {
                        "type": "boolean",
                        "description": "Whether the configured model can generate reviewed Experience Candidates.",
                        "default": False,
                    },
                    "managed_skill_generation": {
                        "type": "boolean",
                        "description": "Whether the configured model can generate reviewed managed Skill Candidates.",
                        "default": False,
                    },
                    "external_skill_registry": {
                        "type": "boolean",
                        "description": "Whether "
                        "host-local "
                        "external "
                        "Skill "
                        "discovery "
                        "and "
                        "exact "
                        "resolution "
                        "are "
                        "configured.",
                        "default": False,
                    },
                    "handoff_generation": {
                        "type": "boolean",
                        "description": "Whether exact evidence can be generated into an inspectable Handoff Draft.",
                    },
                    "search_modes": {"items": {"$ref": "#/components/schemas/MemorySearchMode"}, "type": "array"},
                    "context_versions": {
                        "items": {"$ref": "#/components/schemas/PreparedContextSchema"},
                        "type": "array",
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": [
                    "source_types",
                    "artifact_families",
                    "memory_extraction",
                    "handoff_generation",
                    "search_modes",
                    "context_versions",
                ],
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
            "CommitHandoffRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "handoff": {"$ref": "#/components/schemas/PreparedHandoff"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "handoff"],
            },
            "CommittedHandoff": {
                "properties": {
                    "reference": {"$ref": "#/components/schemas/ArtifactReference"},
                    "content": {"$ref": "#/components/schemas/HandoffContent"},
                    "source_refs": {"items": {"$ref": "#/components/schemas/SourceReference"}, "type": "array"},
                    "artifact_refs": {"items": {"$ref": "#/components/schemas/ArtifactReference"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["reference", "content", "source_refs", "artifact_refs"],
            },
            "ContinueHandoffRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "selection": {"$ref": "#/components/schemas/HandoffSelection"},
                    "prepared": {"$ref": "#/components/schemas/PreparedHandoff", "nullable": True},
                    "revision": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "selection"],
            },
            "FinalizeHandoffRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "draft": {"$ref": "#/components/schemas/HandoffDraft"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "draft"],
            },
            "HandoffArtifactCitation": {
                "properties": {
                    "kind": {"type": "string", "enum": ["artifact"]},
                    "artifact_ref": {"$ref": "#/components/schemas/ArtifactReference"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["kind", "artifact_ref"],
            },
            "HandoffActivation": {
                "properties": {
                    "status": {"$ref": "#/components/schemas/HandoffActivationStatus"},
                    "boundary_source": {"$ref": "#/components/schemas/SourceReference"},
                    "previous_position": {"type": "integer", "minimum": 0.0},
                    "current_position": {"type": "integer", "minimum": 0.0},
                    "draft": {"$ref": "#/components/schemas/HandoffDraft", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["status", "boundary_source", "previous_position", "current_position", "draft"],
            },
            "HandoffCitation": {
                "oneOf": [
                    {"$ref": "#/components/schemas/HandoffSourceCitation"},
                    {"$ref": "#/components/schemas/HandoffArtifactCitation"},
                    {"$ref": "#/components/schemas/HandoffMemoryCitation"},
                ],
                "discriminator": {
                    "propertyName": "kind",
                    "mapping": {
                        "source": "#/components/schemas/HandoffSourceCitation",
                        "artifact": "#/components/schemas/HandoffArtifactCitation",
                        "memory": "#/components/schemas/HandoffMemoryCitation",
                    },
                },
            },
            "HandoffContent": {
                "properties": {
                    "schema": {"$ref": "#/components/schemas/HandoffSchema"},
                    "objective": {"type": "string", "maxLength": 8192, "minLength": 1, "pattern": ".*\\S.*"},
                    "state": {
                        "items": {"$ref": "#/components/schemas/HandoffStatement"},
                        "type": "array",
                        "maxItems": 64,
                        "minItems": 1,
                    },
                    "disposition": {"$ref": "#/components/schemas/HandoffDisposition"},
                    "next_action": {"$ref": "#/components/schemas/HandoffStatement", "nullable": True},
                    "omissions": {
                        "items": {"$ref": "#/components/schemas/HandoffOmission"},
                        "type": "array",
                        "maxItems": 64,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["schema", "objective", "state", "disposition", "next_action", "omissions"],
            },
            "HandoffDraft": {
                "properties": {
                    "objective": {"type": "string", "maxLength": 8192, "minLength": 1, "pattern": ".*\\S.*"},
                    "state": {
                        "items": {"$ref": "#/components/schemas/HandoffStatement"},
                        "type": "array",
                        "maxItems": 64,
                        "minItems": 1,
                    },
                    "disposition": {"$ref": "#/components/schemas/HandoffDisposition"},
                    "next_action": {"$ref": "#/components/schemas/HandoffStatement", "nullable": True},
                    "omissions": {
                        "items": {"$ref": "#/components/schemas/HandoffOmission"},
                        "type": "array",
                        "maxItems": 64,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["objective", "state", "disposition", "next_action", "omissions"],
            },
            "HandoffEvidenceCheck": {
                "properties": {
                    "claim": {"$ref": "#/components/schemas/HandoffClaim"},
                    "state_index": {"type": "integer", "minimum": 0.0, "nullable": True},
                    "status": {"$ref": "#/components/schemas/HandoffEvidenceStatus"},
                    "unavailable_evidence": {
                        "items": {"$ref": "#/components/schemas/HandoffCitation"},
                        "type": "array",
                        "maxItems": 32,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["claim", "state_index", "status", "unavailable_evidence"],
            },
            "HandoffMemoryCitation": {
                "properties": {
                    "kind": {"type": "string", "enum": ["memory"]},
                    "memory_citation": {"$ref": "#/components/schemas/MemoryCitation"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["kind", "memory_citation"],
            },
            "HandoffOmission": {
                "properties": {
                    "text": {"type": "string", "maxLength": 8192, "minLength": 1, "pattern": ".*\\S.*"},
                    "citation": {"$ref": "#/components/schemas/HandoffCitation", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["text", "citation"],
            },
            "HandoffResolution": {
                "properties": {
                    "trust": {"type": "string", "enum": ["untrusted_history"]},
                    "status": {"$ref": "#/components/schemas/HandoffResolutionStatus"},
                    "scope_id": {"type": "string"},
                    "content": {"$ref": "#/components/schemas/HandoffContent", "nullable": True},
                    "selection": {"$ref": "#/components/schemas/HandoffSelection", "nullable": True},
                    "selected_revision": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "current_revision": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "evidence_checks": {
                        "items": {"$ref": "#/components/schemas/HandoffEvidenceCheck"},
                        "type": "array",
                        "maxItems": 65,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": [
                    "trust",
                    "status",
                    "scope_id",
                    "content",
                    "selection",
                    "selected_revision",
                    "current_revision",
                    "evidence_checks",
                ],
            },
            "HandoffSourceCitation": {
                "properties": {
                    "kind": {"type": "string", "enum": ["source"]},
                    "source_ref": {"$ref": "#/components/schemas/SourceReference"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["kind", "source_ref"],
            },
            "HandoffStatement": {
                "properties": {
                    "text": {"type": "string", "maxLength": 8192, "minLength": 1, "pattern": ".*\\S.*"},
                    "citations": {
                        "items": {"$ref": "#/components/schemas/HandoffCitation"},
                        "type": "array",
                        "maxItems": 32,
                        "minItems": 1,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["text", "citations"],
            },
            "PrepareHandoffRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "objective": {"type": "string", "maxLength": 8192, "minLength": 1, "pattern": ".*\\S.*"},
                    "evidence": {
                        "items": {"$ref": "#/components/schemas/HandoffCitation"},
                        "type": "array",
                        "maxItems": 32,
                        "minItems": 1,
                    },
                    "max_bytes": {"type": "integer", "maximum": 32768.0, "minimum": 512.0, "default": 8000},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "objective", "evidence"],
            },
            "PreparedHandoff": {
                "properties": {
                    "schema": {"$ref": "#/components/schemas/PreparedHandoffSchema"},
                    "scope_id": {"type": "string"},
                    "base": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "content": {"$ref": "#/components/schemas/HandoffContent"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["schema", "scope_id", "base", "content"],
            },
            "PreparedContext": {
                "properties": {
                    "schema": {"$ref": "#/components/schemas/PreparedContextSchema"},
                    "status": {"$ref": "#/components/schemas/PreparedContextStatus"},
                    "content": {"type": "string", "nullable": True},
                    "content_bytes": {"type": "integer", "minimum": 0.0},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["schema", "status", "content", "content_bytes"],
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
            "ExperienceArtifact": {
                "properties": {
                    "artifact": {"$ref": "#/components/schemas/ArtifactReference"},
                    "content": {"$ref": "#/components/schemas/ExperienceProposal"},
                    "source_refs": {"items": {"$ref": "#/components/schemas/SourceReference"}, "type": "array"},
                    "artifact_refs": {"items": {"$ref": "#/components/schemas/ArtifactReference"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["artifact", "content", "source_refs", "artifact_refs"],
            },
            "ExperienceProposal": {
                "properties": {
                    "situation": {"type": "string", "maxLength": 8000, "minLength": 1, "pattern": ".*\\S.*"},
                    "action": {"type": "string", "maxLength": 8000, "minLength": 1, "pattern": ".*\\S.*"},
                    "outcome": {"type": "string", "maxLength": 8000, "minLength": 1, "pattern": ".*\\S.*"},
                    "lesson": {"type": "string", "maxLength": 8000, "minLength": 1, "pattern": ".*\\S.*"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["situation", "action", "outcome", "lesson"],
            },
            "SkillArtifact": {
                "properties": {
                    "artifact": {"$ref": "#/components/schemas/ArtifactReference"},
                    "content": {"$ref": "#/components/schemas/SkillProposal"},
                    "source_refs": {"items": {"$ref": "#/components/schemas/SourceReference"}, "type": "array"},
                    "artifact_refs": {"items": {"$ref": "#/components/schemas/ArtifactReference"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["artifact", "content", "source_refs", "artifact_refs"],
            },
            "SkillProposal": {
                "properties": {
                    "name": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^\\S(?:.*\\S)?$"},
                    "description": {"type": "string", "maxLength": 2000, "minLength": 1, "pattern": "^\\S(?:.*\\S)?$"},
                    "instructions": {"type": "string", "maxLength": 32000, "minLength": 1, "pattern": ".*\\S.*"},
                    "validation": {
                        "items": {"$ref": "#/components/schemas/SkillValidationItem"},
                        "type": "array",
                        "maxItems": 32,
                        "minItems": 1,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["name", "description", "instructions", "validation"],
            },
            "SkillValidationItem": {"type": "string", "maxLength": 2000, "minLength": 1, "pattern": "^\\S(?:.*\\S)?$"},
            "ExternalSkillRegistration": {
                "properties": {
                    "external_skill_id": {
                        "type": "string",
                        "maxLength": 128,
                        "minLength": 1,
                        "pattern": "^[\\x21-\\x7E]+$",
                    },
                    "provider": {"type": "string", "enum": ["codex"]},
                    "agent_kind": {"type": "string", "enum": ["codex"]},
                    "host_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^\\S(?:.*\\S)?$"},
                    "installation_scope": {"$ref": "#/components/schemas/ExternalSkillInstallationScope"},
                    "locator": {
                        "type": "string",
                        "maxLength": 2000,
                        "minLength": 1,
                        "pattern": "^\\S(?:.*\\S)?$",
                        "description": "Host-local locator; not a cross-Agent or cross-host contract.",
                    },
                    "fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                    "name": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^\\S(?:.*\\S)?$"},
                    "description": {"type": "string", "maxLength": 2000, "minLength": 1, "pattern": "^\\S(?:.*\\S)?$"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": [
                    "external_skill_id",
                    "provider",
                    "agent_kind",
                    "host_id",
                    "installation_scope",
                    "locator",
                    "fingerprint",
                    "name",
                    "description",
                ],
            },
            "ExternalSkillResolution": {
                "properties": {
                    "registration": {"$ref": "#/components/schemas/ExternalSkillRegistration"},
                    "status": {"$ref": "#/components/schemas/ExternalSkillResolutionStatus"},
                    "entrypoint": {
                        "type": "string",
                        "description": "Host-local "
                        "SKILL.md "
                        "path; "
                        "present "
                        "only "
                        "when "
                        "the "
                        "exact "
                        "fingerprint "
                        "is "
                        "available.",
                        "nullable": True,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["registration", "status", "entrypoint"],
            },
            "ScanExternalSkillsResponse": {
                "properties": {
                    "registrations": {
                        "items": {"$ref": "#/components/schemas/ExternalSkillRegistration"},
                        "type": "array",
                    },
                    "skipped": {"type": "integer", "minimum": 0.0},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["registrations", "skipped"],
            },
            "ListExternalSkillsResponse": {
                "properties": {
                    "skills": {"items": {"$ref": "#/components/schemas/ExternalSkillResolution"}, "type": "array"}
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["skills"],
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
                    "memory": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
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
            "GetArtifactCandidateRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "candidate_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "candidate_id"],
            },
            "GetExperienceRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "artifact": {"$ref": "#/components/schemas/ArtifactReference"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "artifact"],
            },
            "GetSkillRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "artifact": {"$ref": "#/components/schemas/ArtifactReference"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "artifact"],
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
                    "memory": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "revisions": {"items": {"$ref": "#/components/schemas/MemoryRevisionChanges"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["revisions"],
            },
            "ListMemoryEntriesRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "include_inactive": {
                        "type": "boolean",
                        "description": "Include inactive entries from the current Memory head for explicit audit.",
                        "default": False,
                    },
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id"],
            },
            "ListMemoryEntriesResponse": {
                "properties": {
                    "memory": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "entries": {"items": {"$ref": "#/components/schemas/MemoryEntry"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["entries"],
            },
            "ListArtifactCandidatesRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "status": {"$ref": "#/components/schemas/CandidateStatus", "default": "pending"},
                    "family": {"$ref": "#/components/schemas/CandidateFamily", "nullable": True},
                    "cursor": {"type": "string", "maxLength": 128, "minLength": 1, "nullable": True},
                    "limit": {"type": "integer", "maximum": 100.0, "minimum": 1.0, "default": 50},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id"],
            },
            "ListExternalSkillsRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "include_unavailable": {"type": "boolean", "default": False},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id"],
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
                    "entry": {"$ref": "#/components/schemas/MemoryEntry", "nullable": True},
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
            "PrepareContextRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "query": {"type": "string", "maxLength": 8192, "minLength": 1, "pattern": ".*\\S.*"},
                    "max_bytes": {"type": "integer", "maximum": 32768.0, "minimum": 512.0, "default": 8000},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "query"],
            },
            "ProposeExperienceRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "proposal": {"$ref": "#/components/schemas/ExperienceProposal"},
                    "source_refs": {
                        "items": {"$ref": "#/components/schemas/SourceReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Source "
                        "evidence. "
                        "Counted "
                        "with "
                        "artifact_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "artifact_refs": {
                        "items": {"$ref": "#/components/schemas/ArtifactReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Artifact "
                        "evidence. "
                        "Counted "
                        "with "
                        "source_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "target": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "proposal", "source_refs", "artifact_refs"],
            },
            "GenerateExperienceRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "source_refs": {
                        "items": {"$ref": "#/components/schemas/SourceReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Source "
                        "evidence. "
                        "Counted "
                        "with "
                        "artifact_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "artifact_refs": {
                        "items": {"$ref": "#/components/schemas/ArtifactReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Artifact "
                        "evidence. "
                        "Counted "
                        "with "
                        "source_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "target": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "source_refs", "artifact_refs"],
            },
            "ProposeSkillRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "proposal": {"$ref": "#/components/schemas/SkillProposal"},
                    "source_refs": {
                        "items": {"$ref": "#/components/schemas/SourceReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Source "
                        "evidence. "
                        "Counted "
                        "with "
                        "artifact_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "artifact_refs": {
                        "items": {"$ref": "#/components/schemas/ArtifactReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Artifact "
                        "evidence. "
                        "Counted "
                        "with "
                        "source_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "target": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "proposal", "source_refs", "artifact_refs"],
            },
            "SkillGenerationOrigin": {
                "type": "string",
                "enum": ["experience", "source", "usage"],
                "description": "The operation-specific direct provenance shape required for managed Skill generation.",
            },
            "GenerateSkillRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "origin": {"$ref": "#/components/schemas/SkillGenerationOrigin"},
                    "source_refs": {
                        "items": {"$ref": "#/components/schemas/SourceReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Source "
                        "evidence. "
                        "Counted "
                        "with "
                        "artifact_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "artifact_refs": {
                        "items": {"$ref": "#/components/schemas/ArtifactReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Artifact "
                        "evidence. "
                        "Counted "
                        "with "
                        "source_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "target": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "origin", "source_refs", "artifact_refs"],
            },
            "GeneratedCandidateStatus": {"type": "string", "enum": ["pending", "no_op"]},
            "GeneratedCandidateResponse": {
                "properties": {
                    "status": {"$ref": "#/components/schemas/GeneratedCandidateStatus"},
                    "candidate": {"$ref": "#/components/schemas/ArtifactCandidate", "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["status", "candidate"],
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
            "RejectArtifactCandidateRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "candidate_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "expected_version": {"type": "integer", "minimum": 1.0},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "pattern": ".*\\S.*"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "candidate_id", "expected_version", "reason"],
            },
            "ReviseArtifactCandidateRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "candidate_id": {"type": "string", "maxLength": 128, "minLength": 1, "pattern": "^[\\x21-\\x7E]+$"},
                    "expected_version": {"type": "integer", "minimum": 1.0},
                    "proposal": {
                        "oneOf": [
                            {"$ref": "#/components/schemas/ExperienceProposal"},
                            {"$ref": "#/components/schemas/SkillProposal"},
                        ]
                    },
                    "source_refs": {
                        "items": {"$ref": "#/components/schemas/SourceReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Source "
                        "evidence. "
                        "Counted "
                        "with "
                        "artifact_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "artifact_refs": {
                        "items": {"$ref": "#/components/schemas/ArtifactReference"},
                        "type": "array",
                        "maxItems": 32,
                        "description": "Exact "
                        "Artifact "
                        "evidence. "
                        "Counted "
                        "with "
                        "source_refs "
                        "toward "
                        "a "
                        "combined "
                        "maximum "
                        "of "
                        "32 "
                        "references.",
                    },
                    "target": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": [
                    "scope_id",
                    "candidate_id",
                    "expected_version",
                    "proposal",
                    "source_refs",
                    "artifact_refs",
                ],
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
            "ScanExternalSkillsRequest": {
                "properties": {"scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"}},
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id"],
            },
            "ResolveExternalSkillRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "external_skill_id": {
                        "type": "string",
                        "maxLength": 128,
                        "minLength": 1,
                        "pattern": "^[\\x21-\\x7E]+$",
                    },
                    "fingerprint": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "external_skill_id", "fingerprint"],
            },
            "ExternalSkillImportMode": {"type": "string", "enum": ["import", "fork"]},
            "ImportExternalSkillRequest": {
                "properties": {
                    "scope_id": {"type": "string", "maxLength": 256, "minLength": 1, "pattern": ".*\\S.*"},
                    "external_skill_id": {
                        "type": "string",
                        "maxLength": 128,
                        "minLength": 1,
                        "pattern": "^[\\x21-\\x7E]+$",
                    },
                    "fingerprint": {
                        "type": "string",
                        "pattern": "^[0-9a-f]{64}$",
                        "description": "Exact package fingerprint captured into Source lineage.",
                    },
                    "mode": {"$ref": "#/components/schemas/ExternalSkillImportMode"},
                    "reason": {"type": "string", "maxLength": 2000, "minLength": 1, "nullable": True},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["scope_id", "external_skill_id", "fingerprint", "mode"],
            },
            "SearchMemoryResponse": {
                "properties": {
                    "memory": {"$ref": "#/components/schemas/ArtifactReference", "nullable": True},
                    "mode": {"$ref": "#/components/schemas/MemoryUsedSearchMode", "nullable": True},
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
            "CandidateFamily": {"type": "string", "enum": ["experience", "skill"]},
            "ExternalSkillInstallationScope": {"type": "string", "enum": ["user", "project", "plugin"]},
            "ExternalSkillResolutionStatus": {"type": "string", "enum": ["available", "unavailable"]},
            "CandidateStatus": {"type": "string", "enum": ["pending", "approved", "rejected"]},
            "PreparedContextSchema": {"type": "string", "enum": ["powercontext.prepared-context.v1"]},
            "PreparedContextStatus": {"type": "string", "enum": ["ready", "empty"]},
            "EntryChangeOperation": {"type": "string", "enum": ["add", "revise", "deactivate", "reactivate"]},
            "FlushStatus": {"type": "string", "enum": ["idle", "processed"]},
            "MemoryEntryState": {"type": "string", "enum": ["active", "inactive"]},
            "MemoryMatchedBy": {"type": "string", "enum": ["fts", "vector"]},
            "MemorySearchMode": {"type": "string", "enum": ["auto", "fts", "vector", "hybrid"]},
            "MemoryUsedSearchMode": {"type": "string", "enum": ["fts", "vector", "hybrid"]},
            "HandoffClaim": {"type": "string", "enum": ["state", "next_action"]},
            "HandoffActivationStatus": {"type": "string", "enum": ["generated", "ignored"]},
            "HandoffDisposition": {"type": "string", "enum": ["continuable", "blocked", "complete"]},
            "HandoffEvidenceStatus": {"type": "string", "enum": ["available", "unavailable"]},
            "HandoffResolutionStatus": {"type": "string", "enum": ["empty", "resolved"]},
            "HandoffSchema": {"type": "string", "enum": ["powercontext.handoff.v1"]},
            "HandoffSelection": {"type": "string", "enum": ["prepared", "exact", "latest"]},
            "PreparedHandoffSchema": {"type": "string", "enum": ["powercontext.prepared-handoff.v1"]},
        },
        "responses": {
            "Unauthorized": {
                "description": "A valid bearer token is required by this Server deployment.",
                "headers": {
                    "WWW-Authenticate": {"$ref": "#/components/headers/BearerChallenge"},
                    "X-Request-ID": {"$ref": "#/components/headers/RequestId"},
                },
                "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
            },
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
            "BearerChallenge": {
                "description": "Authentication scheme required by the Server.",
                "schema": {"type": "string", "example": "Bearer"},
            },
            "RequestId": {
                "description": "Opaque identifier for correlating one request.",
                "schema": {"type": "string"},
            },
        },
        "securitySchemes": {
            "BearerAuth": {
                "type": "http",
                "description": "Static bearer token used when local Server authentication is enabled.",
                "scheme": "bearer",
            }
        },
    },
    "security": [{"BearerAuth": []}, {}],
}
