from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from powercontext.http import (
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactReference,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    GetMemoryEntryRequest,
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    PreparedContext,
    ProposeExperienceRequest,
    ReviseArtifactCandidateRequest,
    SearchMemoryRequest,
)
from powercontext.http._generated.operations import (
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    FLUSH_MEMORY,
    GET_ARTIFACT_CANDIDATE,
    GET_EXPERIENCE,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    LIST_ARTIFACT_CANDIDATES,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    PREPARE_CONTEXT,
    PROPOSE_EXPERIENCE,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    SEARCH_MEMORY,
)
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app

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
        "context_versions",
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


def test_prepared_context_is_a_generic_typed_operation_outside_the_mcp_memory_tools() -> None:
    assert PREPARE_CONTEXT.path == "/v1/context/prepare"
    assert PREPARE_CONTEXT.request_type is PrepareContextRequest
    assert PREPARE_CONTEXT.response_type is PreparedContext
    assert PREPARE_CONTEXT.success_status == 200

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    assert set(schemas["PrepareContextRequest"]["properties"]) == {"scope_id", "query", "max_bytes"}
    assert set(schemas["PreparedContext"]["properties"]) == {"schema", "status", "content", "content_bytes"}
    assert not {"memory", "mode", "selection"} & set(schemas["PreparedContext"]["properties"])


def test_experience_and_review_operations_are_typed_and_family_routed() -> None:
    review_operations = (
        LIST_ARTIFACT_CANDIDATES,
        GET_ARTIFACT_CANDIDATE,
        APPROVE_ARTIFACT_CANDIDATE,
        REJECT_ARTIFACT_CANDIDATE,
        REVISE_ARTIFACT_CANDIDATE,
    )

    assert PROPOSE_EXPERIENCE.path == "/v1/experience/propose"
    assert PROPOSE_EXPERIENCE.request_type is ProposeExperienceRequest
    assert PROPOSE_EXPERIENCE.response_type is ArtifactCandidate
    assert PROPOSE_EXPERIENCE.success_status == 201
    assert GET_EXPERIENCE.path == "/v1/experience/get"
    assert all(operation.path.startswith("/v1/artifact-candidates/") for operation in review_operations)
    assert APPROVE_ARTIFACT_CANDIDATE.request_type is ApproveArtifactCandidateRequest

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    assert set(schemas["ExperienceProposal"]["properties"]) == {"situation", "action", "outcome", "lesson"}
    assert schemas["ListArtifactCandidatesRequest"]["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 50,
    }
    for schema_name in ("ArtifactCandidate", "ProposeExperienceRequest", "ReviseArtifactCandidateRequest"):
        properties = schemas[schema_name]["properties"]
        assert properties["source_refs"]["maxItems"] == 32
        assert properties["artifact_refs"]["maxItems"] == 32
        assert "combined maximum of 32" in properties["source_refs"]["description"]
        assert "combined maximum of 32" in properties["artifact_refs"]["description"]


def test_candidate_transport_rejects_combined_evidence_over_limit() -> None:
    source = {"name": "content", "source_id": "task-1"}
    artifact = {"family": "experience", "artifact_id": "exp-1", "revision": 1}
    proposal = {
        "situation": "OpenAPI changed.",
        "action": "Regenerate the Client.",
        "outcome": "Transport stays aligned.",
        "lesson": "Keep contract tests green.",
    }
    over_limit = {
        "scope_id": "project",
        "proposal": proposal,
        "source_refs": [source] * 20,
        "artifact_refs": [artifact] * 13,
    }

    with pytest.raises(ValidationError, match="together must not exceed 32"):
        ProposeExperienceRequest.model_validate(over_limit)
    with pytest.raises(ValidationError, match="together must not exceed 32"):
        ReviseArtifactCandidateRequest.model_validate({
            **over_limit,
            "candidate_id": "cand-1",
            "expected_version": 1,
        })


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


def test_entry_list_hides_inactive_entries_unless_explicitly_requested() -> None:
    default_request = ListMemoryEntriesRequest(scope_id="scope")
    audit_request = ListMemoryEntriesRequest(scope_id="scope", include_inactive=True)

    assert default_request.include_inactive is False
    assert audit_request.include_inactive is True

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    include_inactive = contract["components"]["schemas"]["ListMemoryEntriesRequest"]["properties"]["include_inactive"]
    assert include_inactive["default"] is False


@pytest.mark.parametrize(
    ("model", "value"),
    [
        (ArtifactReference, {"family": "memory", "artifact_id": "memory-1", "revision": 0}),
        (ArtifactReference, {"family": "memory", "artifact_id": "memory-1", "revision": "1"}),
        (ArtifactReference, {"family": "memory", "artifact_id": "memory with spaces", "revision": 1}),
        (SearchMemoryRequest, {"scope_id": "scope", "query": "query", "limit": True}),
        (ListMemoryEntriesRequest, {"scope_id": "scope", "include_inactive": 1}),
        (
            GetMemoryEntryRequest,
            {
                "scope_id": "scope",
                "citation": {
                    "memory_ref": {"family": "memory", "artifact_id": "memory-1", "revision": 1},
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


def test_server_publishes_the_canonical_openapi_schema() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    assert create_app().openapi() == contract
    assert create_server_app().openapi() == contract
