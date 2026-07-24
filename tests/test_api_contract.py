from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

import powercontext
from powercontext.api import (
    ArtifactReference,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    GetMemoryEntryRequest,
    SearchMemoryRequest,
)
from powercontext.api.generated.operations import (
    CAPTURE_CONTENT_SOURCE,
    FLUSH_MEMORY,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    REMEMBER_MEMORY,
    RETIRE_MEMORY_ENTRY,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
)
from powercontext.server.app import create_app
from powercontext.server.runtime import create_server_app

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "openapi" / "powercontext.yaml"


def test_capabilities_report_semantics_without_runtime_tuning_values() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    properties = schemas["Capabilities"]["properties"]

    assert set(properties) == {
        "source_types",
        "artifact_families",
        "memory_extraction",
        "search_modes",
    }
    assert "CapabilityLimit" not in schemas


def test_readiness_operation_declares_the_unavailable_response() -> None:
    assert 503 in GET_READINESS.responses


def test_capture_operation_declares_its_typed_accepted_exchange() -> None:
    assert CAPTURE_CONTENT_SOURCE.request_type is CaptureContentSourceRequest
    assert CAPTURE_CONTENT_SOURCE.response_type is CaptureContentSourceResponse
    assert CAPTURE_CONTENT_SOURCE.success_status == 202


def test_memory_operations_use_family_prefixed_paths_and_typed_requests() -> None:
    memory_operations = (
        FLUSH_MEMORY,
        REMEMBER_MEMORY,
        SEARCH_MEMORY,
        LIST_MEMORY_ENTRIES,
        GET_MEMORY_ENTRY,
        REVISE_MEMORY_ENTRY,
        RETIRE_MEMORY_ENTRY,
        LIST_MEMORY_CHANGES,
    )

    assert all(operation.path.startswith("/v1/memory/") for operation in memory_operations)
    assert all(operation.request_type is not None for operation in memory_operations)
    assert SEARCH_MEMORY.request_type is SearchMemoryRequest


def test_source_reference_keeps_name_as_the_source_type() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    properties = contract["components"]["schemas"]["SourceReference"]["properties"]

    assert set(properties) == {"name", "source_id"}


def test_memory_transport_has_one_reference_shape_and_nested_citations() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    assert "MemoryReference" not in schemas
    assert schemas["MemoryCitation"]["properties"]["memory_ref"] == {"$ref": "#/components/schemas/ArtifactReference"}
    for name in ("GetMemoryEntryRequest", "ReviseMemoryEntryRequest", "RetireMemoryEntryRequest"):
        properties = schemas[name]["properties"]
        assert properties["citation"] == {"$ref": "#/components/schemas/MemoryCitation"}
        assert "memory_id" not in properties
        assert "expected_revision" not in properties


@pytest.mark.parametrize(
    ("model", "value"),
    [
        (ArtifactReference, {"artifact_id": "memory-1", "revision": 0}),
        (ArtifactReference, {"artifact_id": "memory-1", "revision": "1"}),
        (ArtifactReference, {"artifact_id": "memory with spaces", "revision": 1}),
        (SearchMemoryRequest, {"scope_id": "scope", "query": "query", "limit": True}),
        (
            GetMemoryEntryRequest,
            {
                "scope_id": "scope",
                "citation": {
                    "memory_ref": {"artifact_id": "memory-1", "revision": 1},
                    "entry_id": "记忆",
                    "entry_version_id": "version-1",
                },
            },
        ),
    ],
)
def test_generated_transport_rejects_values_outside_openapi(
    model: type[BaseModel],
    value: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        model.model_validate(value)


def test_server_capabilities_stay_out_of_the_core_api() -> None:
    assert not hasattr(powercontext, "Capabilities")


def test_server_publishes_the_canonical_openapi_schema() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    assert create_app().openapi() == contract
    assert create_server_app().openapi() == contract
