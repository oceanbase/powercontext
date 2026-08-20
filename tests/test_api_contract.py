from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from powercontext.http import (
    ActivateHandoffRequest,
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactReference,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CommitHandoffRequest,
    CommittedHandoff,
    ContinueHandoffRequest,
    ExternalSkillResolution,
    FinalizeHandoffRequest,
    GeneratedCandidateResponse,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetMemoryEntryRequest,
    GetStatsRequest,
    HandoffActivation,
    HandoffDraft,
    HandoffResolution,
    ImportExternalSkillRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    PreparedContext,
    PreparedHandoff,
    PrepareHandoffRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    ResolveExternalSkillRequest,
    ReviseArtifactCandidateRequest,
    ScanExternalSkillsRequest,
    ScanExternalSkillsResponse,
    ScopedStats,
    SearchMemoryRequest,
    SkillProposal,
    SkillValidationItem,
    StatsPeriod,
)
from powercontext.http._generated.operations import (
    ACTIVATE_HANDOFF,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    FINALIZE_HANDOFF,
    FLUSH_MEMORY,
    GENERATE_EXPERIENCE,
    GENERATE_SKILL,
    GET_ARTIFACT_CANDIDATE,
    GET_EXPERIENCE,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    GET_SKILL,
    GET_STATS,
    IMPORT_EXTERNAL_SKILL,
    LIST_ARTIFACT_CANDIDATES,
    LIST_EXTERNAL_SKILLS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    PREPARE_CONTEXT,
    PREPARE_HANDOFF,
    PROPOSE_EXPERIENCE,
    PROPOSE_SKILL,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RESOLVE_EXTERNAL_SKILL,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    SCAN_EXTERNAL_SKILLS,
    SEARCH_MEMORY,
)
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import HandoffReportConfig, ServerSettings

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "openapi" / "powercontext.yaml"


def test_contract_uses_the_namespaced_request_id_header() -> None:
    contract = CONTRACT_PATH.read_text()

    assert "X-PowerContext-Request-ID" in contract
    assert "X-Request-ID" not in contract


def test_contract_declares_optional_bearer_authentication() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    assert contract["security"] == [{"BearerAuth": []}, {}]
    assert contract["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Static bearer token used when local Server authentication is enabled.",
    }
    for path, path_item in contract["paths"].items():
        operation = next(iter(path_item.values()))
        if path.startswith("/health/"):
            assert operation["security"] == []
        else:
            assert operation["responses"]["401"] == {"$ref": "#/components/responses/Unauthorized"}


def test_capabilities_report_semantics_without_runtime_tuning_values() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    properties = schemas["Capabilities"]["properties"]

    assert set(properties) == {
        "source_types",
        "artifact_families",
        "memory_extraction",
        "experience_generation",
        "managed_skill_generation",
        "external_skill_registry",
        "handoff_generation",
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


def test_stats_operation_exposes_dashboard_ready_scoped_values() -> None:
    assert GET_STATS.method == "GET"
    assert GET_STATS.path == "/v1/stats"
    assert GET_STATS.request_type is GetStatsRequest
    assert GET_STATS.request_location == "query"
    assert GET_STATS.response_type is ScopedStats
    assert GET_STATS.success_status == 200
    assert GetStatsRequest(scope_id="project").period is StatsPeriod.FIELD_30D

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    stats = schemas["ScopedStats"]
    usage = schemas["UsageStatistics"]
    usage_value = schemas["ModelUsageValue"]
    recall = schemas["RecallTokenStatistics"]

    operation = contract["paths"]["/v1/stats"]["get"]
    assert "requestBody" not in operation
    assert [parameter["name"] for parameter in operation["parameters"]] == ["scope_id", "period"]
    assert set(stats["properties"]) == {"scope_id", "as_of", "inventory", "usage", "recall"}
    assert usage["properties"]["by_purpose"]["maxItems"] == 16
    assert usage["properties"]["daily"]["maxItems"] == 30
    assert usage_value["properties"]["input_tokens"]["nullable"] is True
    assert usage_value["properties"]["output_tokens"]["nullable"] is True
    assert recall["properties"]["estimator"]["nullable"] is True
    assert recall["properties"]["daily"]["maxItems"] == 30


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


def test_memory_search_declares_the_revision_conflict_response() -> None:
    assert SEARCH_MEMORY.responses[409] == {"$ref": "#/components/responses/Conflict"}


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


def test_experience_skill_and_review_operations_are_typed_and_family_routed() -> None:
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
    assert GENERATE_EXPERIENCE.path == "/v1/experience/generate"
    assert GENERATE_EXPERIENCE.request_type is GenerateExperienceRequest
    assert GENERATE_EXPERIENCE.response_type is GeneratedCandidateResponse
    assert GENERATE_EXPERIENCE.success_status == 200
    assert GET_EXPERIENCE.path == "/v1/experience/get"
    assert PROPOSE_SKILL.path == "/v1/skill/propose"
    assert PROPOSE_SKILL.request_type is ProposeSkillRequest
    assert PROPOSE_SKILL.response_type is ArtifactCandidate
    assert PROPOSE_SKILL.success_status == 201
    assert GENERATE_SKILL.path == "/v1/skill/generate"
    assert GENERATE_SKILL.request_type is GenerateSkillRequest
    assert GENERATE_SKILL.response_type is GeneratedCandidateResponse
    assert GENERATE_SKILL.success_status == 200
    assert GET_SKILL.path == "/v1/skill/get"
    assert all(operation.path.startswith("/v1/artifact-candidates/") for operation in review_operations)
    assert APPROVE_ARTIFACT_CANDIDATE.request_type is ApproveArtifactCandidateRequest

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    assert set(schemas["ExperienceProposal"]["properties"]) == {"situation", "action", "outcome", "lesson"}
    assert set(schemas["SkillProposal"]["properties"]) == {
        "name",
        "description",
        "instructions",
        "validation",
    }
    assert schemas["ListArtifactCandidatesRequest"]["properties"]["limit"] == {
        "type": "integer",
        "minimum": 1,
        "maximum": 100,
        "default": 50,
    }
    for schema_name in (
        "ArtifactCandidate",
        "ProposeExperienceRequest",
        "GenerateExperienceRequest",
        "ProposeSkillRequest",
        "GenerateSkillRequest",
        "ReviseArtifactCandidateRequest",
    ):
        properties = schemas[schema_name]["properties"]
        assert properties["source_refs"]["maxItems"] == 32
        assert properties["artifact_refs"]["maxItems"] == 32
        assert "combined maximum of 32" in properties["source_refs"]["description"]
        assert "combined maximum of 32" in properties["artifact_refs"]["description"]


def test_managed_skill_transport_rejects_untrimmed_projection_metadata() -> None:
    with pytest.raises(ValidationError):
        SkillProposal(
            name=" managed-skill ",
            description="Use for a bounded task.",
            instructions="Perform the bounded task.",
            validation=[SkillValidationItem("The expected result exists.")],
        )


def test_external_skill_operations_preserve_local_authority_and_exact_resolution() -> None:
    assert SCAN_EXTERNAL_SKILLS.path == "/v1/external-skills/scan"
    assert SCAN_EXTERNAL_SKILLS.request_type is ScanExternalSkillsRequest
    assert SCAN_EXTERNAL_SKILLS.response_type is ScanExternalSkillsResponse
    assert LIST_EXTERNAL_SKILLS.path == "/v1/external-skills/list"
    assert LIST_EXTERNAL_SKILLS.request_type is ListExternalSkillsRequest
    assert LIST_EXTERNAL_SKILLS.response_type is ListExternalSkillsResponse
    assert RESOLVE_EXTERNAL_SKILL.path == "/v1/external-skills/resolve"
    assert RESOLVE_EXTERNAL_SKILL.request_type is ResolveExternalSkillRequest
    assert RESOLVE_EXTERNAL_SKILL.response_type is ExternalSkillResolution
    assert IMPORT_EXTERNAL_SKILL.path == "/v1/external-skills/import"
    assert IMPORT_EXTERNAL_SKILL.request_type is ImportExternalSkillRequest
    assert IMPORT_EXTERNAL_SKILL.response_type is GeneratedCandidateResponse

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    registration = schemas["ExternalSkillRegistration"]
    assert {
        "external_skill_id",
        "provider",
        "agent_kind",
        "host_id",
        "installation_scope",
        "locator",
        "fingerprint",
        "name",
        "description",
    } == set(registration["properties"])
    assert "cross-Agent" in registration["properties"]["locator"]["description"]
    assert schemas["ResolveExternalSkillRequest"]["required"] == [
        "scope_id",
        "external_skill_id",
        "fingerprint",
    ]
    assert "mode" not in schemas["ResolveExternalSkillRequest"]["properties"]
    assert schemas["ImportExternalSkillRequest"]["required"] == [
        "scope_id",
        "external_skill_id",
        "fingerprint",
        "mode",
    ]


def test_memory_search_mode_remains_on_the_search_request() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    properties = contract["components"]["schemas"]["SearchMemoryRequest"]["properties"]

    assert properties["mode"] == {
        "$ref": "#/components/schemas/MemorySearchMode",
        "default": "auto",
    }


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


def test_handoff_operations_expose_the_complete_explicit_lifecycle() -> None:
    operations = (
        ACTIVATE_HANDOFF,
        PREPARE_HANDOFF,
        FINALIZE_HANDOFF,
        COMMIT_HANDOFF,
        CONTINUE_HANDOFF,
    )

    assert all(operation.path.startswith("/v1/handoff/") for operation in operations)
    assert all(operation.success_status == 200 for operation in operations)
    assert ACTIVATE_HANDOFF.request_type is ActivateHandoffRequest
    assert ACTIVATE_HANDOFF.response_type is HandoffActivation
    assert PREPARE_HANDOFF.request_type is PrepareHandoffRequest
    assert PREPARE_HANDOFF.response_type is HandoffDraft
    assert FINALIZE_HANDOFF.request_type is FinalizeHandoffRequest
    assert FINALIZE_HANDOFF.response_type is PreparedHandoff
    assert COMMIT_HANDOFF.request_type is CommitHandoffRequest
    assert COMMIT_HANDOFF.response_type is CommittedHandoff
    assert CONTINUE_HANDOFF.request_type is ContinueHandoffRequest
    assert CONTINUE_HANDOFF.response_type is HandoffResolution


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

    assert create_app(handoff_report_enabled=True).openapi() == contract
    assert (
        create_server_app(settings=ServerSettings(handoff_report=HandoffReportConfig(enabled=True))).openapi()
        == contract
    )
