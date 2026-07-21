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
    },
    "components": {
        "schemas": {
            "CapabilityLimit": {
                "properties": {"name": {"type": "string"}, "value": {"type": "integer"}},
                "additionalProperties": False,
                "type": "object",
                "required": ["name", "value"],
            },
            "Capabilities": {
                "properties": {
                    "source_types": {"items": {"type": "string"}, "type": "array"},
                    "artifact_families": {"items": {"type": "string"}, "type": "array"},
                    "search_modes": {"items": {"type": "string"}, "type": "array"},
                    "limits": {"items": {"$ref": "#/components/schemas/CapabilityLimit"}, "type": "array"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["source_types", "artifact_families", "search_modes", "limits"],
            },
            "HealthResponse": {
                "properties": {"status": {"type": "string"}},
                "additionalProperties": False,
                "type": "object",
                "required": ["status"],
            },
            "ReadinessStatus": {"type": "string", "enum": ["ready", "not_ready"]},
            "ReadinessResponse": {
                "properties": {
                    "status": {"$ref": "#/components/schemas/ReadinessStatus"},
                    "checks": {"additionalProperties": {"type": "string"}, "type": "object"},
                },
                "additionalProperties": False,
                "type": "object",
                "required": ["status", "checks"],
            },
        },
        "headers": {
            "RequestId": {"description": "Opaque identifier for correlating one request.", "schema": {"type": "string"}}
        },
    },
}
