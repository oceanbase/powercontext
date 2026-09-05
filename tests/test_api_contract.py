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

from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel, ValidationError

from powercontext.http import (
    AcknowledgeHandoffRequest,
    ActivateHandoffRequest,
    ApproveArtifactCandidateRequest,
    ArtifactCandidate,
    ArtifactCreated,
    ArtifactReference,
    ArtifactRevision,
    CaptureContentSourceRequest,
    CaptureContentSourceResponse,
    CommitHandoffRequest,
    CommittedHandoff,
    ContinueHandoffRequest,
    CreateArtifactRequest,
    CreateMemoryArtifactContent,
    CreateMemoryArtifactEntry,
    CreateMemoryArtifactRequest,
    CreateSourceRequest,
    CreateWorkContractRequest,
    ExternalSkillResolution,
    FinalizeHandoffRequest,
    GeneratedCandidateResponse,
    GenerateExperienceRequest,
    GenerateSkillRequest,
    GetMemoryEntryRequest,
    GetStatsRequest,
    HandoffAcknowledgement,
    HandoffActivation,
    HandoffCurrentWorkRequest,
    HandoffDraft,
    HandoffResolution,
    ImportExternalSkillRequest,
    ListArtifactsRequest,
    ListExternalSkillsRequest,
    ListExternalSkillsResponse,
    ListMemoryEntriesRequest,
    PrepareContextRequest,
    PreparedContext,
    PreparedHandoff,
    PreparedWorkHandoff,
    PrepareHandoffRequest,
    ProposeExperienceRequest,
    ProposeSkillRequest,
    RecordTaskOutcomeRequest,
    ResolveExternalSkillRequest,
    ReviseArtifactCandidateRequest,
    ScanExternalSkillsRequest,
    ScanExternalSkillsResponse,
    ScopedStats,
    SearchMemoryRequest,
    SkillProposal,
    SkillValidationItem,
    SourceRecord,
    SourceType,
    SourceTypeReference,
    StatsPeriod,
    WorkSourceReceipt,
)
from powercontext.http._generated.operations import (
    ACKNOWLEDGE_HANDOFF,
    ACTIVATE_HANDOFF,
    APPROVE_ARTIFACT_CANDIDATE,
    CAPTURE_CONTENT_SOURCE,
    COMMIT_HANDOFF,
    CONTINUE_HANDOFF,
    CREATE_ARTIFACT,
    CREATE_REMOTE_SKILL_TARGET,
    CREATE_SOURCE,
    CREATE_WORK_CONTRACT,
    DOWNLOAD_REMOTE_SKILL_PACKAGE,
    DOWNLOAD_SKILL_PACKAGE,
    ENROLL_REMOTE_SKILL_TARGET,
    FINALIZE_HANDOFF,
    FLUSH_MEMORY,
    GENERATE_EXPERIENCE,
    GENERATE_SKILL,
    GET_ARTIFACT,
    GET_ARTIFACT_CANDIDATE,
    GET_ARTIFACT_REVISION,
    GET_EXPERIENCE,
    GET_MEMORY_ENTRY,
    GET_READINESS,
    GET_SKILL,
    GET_SKILL_PACKAGE_MANIFEST,
    GET_SOURCE,
    GET_STATS,
    HANDOFF_CURRENT_WORK,
    IMPORT_EXTERNAL_SKILL,
    LIST_ARTIFACT_CANDIDATES,
    LIST_ARTIFACTS,
    LIST_EXTERNAL_SKILLS,
    LIST_MANAGED_SKILLS,
    LIST_MEMORY_CHANGES,
    LIST_MEMORY_ENTRIES,
    LIST_REMOTE_SKILL_TARGETS,
    PREPARE_CONTEXT,
    PREPARE_HANDOFF,
    PROPOSE_EXPERIENCE,
    PROPOSE_SKILL,
    PROPOSE_SKILL_PACKAGE,
    PUBLISH_ARTIFACT,
    PUBLISH_REMOTE_SKILL,
    RECONCILE_REMOTE_SKILLS,
    RECORD_REMOTE_SKILL_RECEIPT,
    RECORD_SKILL_USAGE,
    RECORD_TASK_OUTCOME,
    REJECT_ARTIFACT_CANDIDATE,
    REMEMBER_MEMORY,
    RENAME_REMOTE_SKILL_TARGET,
    REPLACE_ARTIFACT,
    RESOLVE_EXTERNAL_SKILL,
    RETIRE_MEMORY_ENTRY,
    REVISE_ARTIFACT_CANDIDATE,
    REVISE_MEMORY_ENTRY,
    REVOKE_REMOTE_SKILL_TARGET,
    SCAN_EXTERNAL_SKILLS,
    SEARCH_MEMORY,
    SUBMIT_SOURCE_OBSERVATION,
    UNPUBLISH_REMOTE_SKILL,
    UPDATE_SKILL_LIFECYCLE,
)
from powercontext.server.app import create_app
from powercontext.server.factory import create_server_app
from powercontext.server.settings import HandoffReportConfig, ServerSettings

CONTRACT_PATH = Path(__file__).resolve().parents[1] / "openapi" / "powercontext.yaml"


def test_contract_uses_the_namespaced_request_id_header() -> None:
    contract = CONTRACT_PATH.read_text()

    assert "X-PowerContext-Request-ID" in contract
    assert "X-Request-ID" not in contract


def test_contract_declares_server_and_remote_target_bearer_boundaries() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    assert contract["security"] == [{"BearerAuth": []}, {}]
    assert contract["components"]["securitySchemes"]["BearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Bearer credential resolved to an opaque authenticated Principal by the Server deployment.",
    }
    assert contract["components"]["securitySchemes"]["TargetBearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "description": "Per-target credential issued once during remote Receiver enrollment.",
    }
    public_paths = {"/health/live", "/health/ready", "/v1/skill/remote/target/enroll"}
    target_paths = {
        "/v1/skill/remote/reconcile",
        "/v1/skill/remote/package/download",
        "/v1/skill/remote/receipt",
    }
    for path, path_item in contract["paths"].items():
        operation = next(iter(path_item.values()))
        if path in public_paths:
            assert operation["security"] == []
        elif path in target_paths:
            assert operation["responses"]["401"] == {"$ref": "#/components/responses/Unauthorized"}
            assert operation["security"] == [{"TargetBearerAuth": []}]
        else:
            assert operation["responses"]["401"] == {"$ref": "#/components/responses/Unauthorized"}
            assert operation["responses"]["403"] == {"$ref": "#/components/responses/Forbidden"}
            assert "x-powercontext-access" in operation


def test_every_access_protected_operation_declares_the_unavailable_response() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    for path_item in contract["paths"].values():
        operation = next(iter(path_item.values()))
        if "x-powercontext-access" in operation:
            assert operation["responses"]["503"] == {"$ref": "#/components/responses/Unavailable"}


def test_source_ingestion_operations_preserve_the_access_boundary() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    expected = {
        "/v1/source-definitions/register": {
            "action": "server.admin",
            "resource": {"type": "server"},
        },
        "/v1/connector-checkpoints/get": {
            "action": "scope.contribute",
            "resource": {"type": "scope", "scope-id-from": "binding.scope_id"},
        },
        "/v1/source-observations": {
            "action": "scope.contribute",
            "resource": {"type": "scope", "scope-id-from": "scope_id"},
        },
        "/v1/connector-checkpoints/commit": {
            "action": "scope.contribute",
            "resource": {"type": "scope", "scope-id-from": "binding.scope_id"},
        },
    }

    for path, requirement in expected.items():
        operation = contract["paths"][path]["post"]
        assert operation["x-powercontext-access"] == requirement


def test_access_contract_uses_compound_checks_and_generic_binding_replacement() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    paths = contract["paths"]
    schemas = contract["components"]["schemas"]

    assert "/v1/access/check-batch" not in paths
    assert "/v1/access/bindings/reassign-handoff-receiver" not in paths
    assert paths["/v1/access/check"]["post"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/AccessCheckResponse"
    }
    assert schemas["AccessCheckRequest"]["required"] == ["match", "requirements"]
    assert schemas["AccessRequirementMatch"]["enum"] == ["all", "any"]
    assert paths["/v1/access/bindings/replace"]["post"]["operationId"] == "replace_access_binding"
    assert schemas["AccessRoleCardinality"]["enum"] == ["many_per_resource", "one_per_resource"]


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


def test_source_observation_contract_uses_explicit_connector_scope_and_captured_values() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    request = schemas["SubmitSourceObservationRequest"]
    observation = schemas["SourceObservation"]

    assert set(request["properties"]) == {"scope_id", "observation"}
    assert request["properties"]["observation"] == {"$ref": "#/components/schemas/SourceObservation"}
    assert observation["properties"]["materialization"]["enum"] == ["captured"]
    assert "ProjectedSource" not in schemas
    assert SUBMIT_SOURCE_OBSERVATION.scope_mode == "none"


def test_standard_skill_operations_preserve_agent_and_receiver_scope_boundaries() -> None:
    agent_scoped = (
        LIST_MANAGED_SKILLS,
        UPDATE_SKILL_LIFECYCLE,
        GET_SKILL_PACKAGE_MANIFEST,
        DOWNLOAD_SKILL_PACKAGE,
        PROPOSE_SKILL_PACKAGE,
        RECORD_SKILL_USAGE,
        LIST_REMOTE_SKILL_TARGETS,
        CREATE_REMOTE_SKILL_TARGET,
        RENAME_REMOTE_SKILL_TARGET,
        REVOKE_REMOTE_SKILL_TARGET,
        PUBLISH_REMOTE_SKILL,
        UNPUBLISH_REMOTE_SKILL,
    )
    receiver_scoped = (
        ENROLL_REMOTE_SKILL_TARGET,
        RECONCILE_REMOTE_SKILLS,
        DOWNLOAD_REMOTE_SKILL_PACKAGE,
        RECORD_REMOTE_SKILL_RECEIPT,
    )

    assert {operation.scope_mode for operation in agent_scoped} == {"current"}
    assert {operation.scope_mode for operation in receiver_scoped} == {"none"}


def test_stats_operation_exposes_dashboard_ready_selection_values() -> None:
    assert GET_STATS.method == "POST"
    assert GET_STATS.path == "/v1/stats"
    assert GET_STATS.request_type is GetStatsRequest
    assert GET_STATS.request_location == "body"
    assert GET_STATS.response_type is ScopedStats
    assert GET_STATS.success_status == 200
    assert GetStatsRequest.model_validate({"selection": {"mode": "all"}}).period is StatsPeriod.FIELD_30D

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]
    stats = schemas["ScopedStats"]
    usage = schemas["UsageStatistics"]
    usage_value = schemas["ModelUsageValue"]
    recall = schemas["RecallTokenStatistics"]

    operation = contract["paths"]["/v1/stats"]["post"]
    assert operation["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/GetStatsRequest"
    }
    assert set(stats["properties"]) == {
        "selection",
        "scope_ids",
        "as_of",
        "inventory",
        "usage",
        "recall",
        "by_scope",
    }
    assert usage["properties"]["by_purpose"]["maxItems"] == 16
    assert usage["properties"]["daily"]["maxItems"] == 30
    assert usage_value["properties"]["input_tokens"]["nullable"] is True
    assert usage_value["properties"]["output_tokens"]["nullable"] is True
    assert recall["properties"]["estimator"]["nullable"] is True
    assert recall["properties"]["daily"]["maxItems"] == 30


def test_scope_resource_operations_separate_identity_from_mutable_metadata() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    path_item = contract["paths"]["/v1/scopes/{scope_id}"]
    assert path_item["get"]["parameters"][0]["in"] == "path"
    assert "requestBody" not in path_item["get"]
    assert path_item["put"]["parameters"][0]["in"] == "path"
    assert path_item["put"]["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/UpdateScopeRequest"
    }
    assert "scope_id" not in contract["components"]["schemas"]["UpdateScopeRequest"]["properties"]


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


def test_handoff_access_metadata_resolves_business_revision_to_logical_authorization() -> None:
    assert CONTINUE_HANDOFF.access is not None
    assert CONTINUE_HANDOFF.access.action is None
    assert CONTINUE_HANDOFF.access.resolver == "continue_handoff_access"
    assert ACKNOWLEDGE_HANDOFF.access is not None
    assert ACKNOWLEDGE_HANDOFF.access.action is None
    assert ACKNOWLEDGE_HANDOFF.access.resolver == "acknowledge_handoff_access"


def test_access_contract_uses_logical_resources_and_generic_skill_read_access() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    assert schemas["AccessResourceType"]["enum"] == ["server", "scope", "artifact"]
    assert "access.self" not in schemas["AccessAction"]["enum"]
    artifact = schemas["ArtifactAccessResource"]
    assert artifact["required"] == ["type", "scope_id", "identity"]
    assert set(artifact["properties"]) == {"type", "scope_id", "identity", "selector"}
    selector = schemas["MemoryEntryAccessSelector"]
    assert selector["required"] == ["type", "entry_id"]
    assert set(selector["properties"]) == {"type", "entry_id"}
    identity = schemas["AccessArtifactIdentity"]
    assert identity["required"] == ["family", "artifact_id"]
    assert set(identity["properties"]) == {"family", "artifact_id"}
    assert set(schemas["AccessDecision"]["properties"]) == {"allowed", "reason_code"}
    assert schemas["AccessBinding"]["properties"]["policy_revision"]["maxLength"] == 64
    assert schemas["AccessAuditEvent"]["properties"]["policy_revision"]["maxLength"] == 64

    assert GET_MEMORY_ENTRY.access is not None
    assert GET_MEMORY_ENTRY.access.resolver == "exact_memory_access"
    assert GET_EXPERIENCE.access is not None
    assert GET_EXPERIENCE.access.resolver == "exact_experience_access"
    assert GET_SKILL.access is not None
    assert GET_SKILL.access.resolver == "exact_skill_access"
    assert PUBLISH_ARTIFACT.path == "/v1/artifact-publications"
    assert PUBLISH_ARTIFACT.access is not None
    assert PUBLISH_ARTIFACT.access.resolver == "publish_artifact_access"


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
        "package",
        "license",
        "compatibility",
        "metadata",
        "allowed_tools",
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


def test_work_operations_expose_the_high_level_continuity_loop() -> None:
    operations = (
        CREATE_WORK_CONTRACT,
        HANDOFF_CURRENT_WORK,
        ACKNOWLEDGE_HANDOFF,
        RECORD_TASK_OUTCOME,
    )

    assert all(operation.path.startswith("/v1/work/") for operation in operations)
    assert CREATE_WORK_CONTRACT.request_type is CreateWorkContractRequest
    assert CREATE_WORK_CONTRACT.response_type is WorkSourceReceipt
    assert CREATE_WORK_CONTRACT.success_status == 202
    assert HANDOFF_CURRENT_WORK.request_type is HandoffCurrentWorkRequest
    assert HANDOFF_CURRENT_WORK.response_type is PreparedWorkHandoff
    assert HANDOFF_CURRENT_WORK.success_status == 200
    assert ACKNOWLEDGE_HANDOFF.request_type is AcknowledgeHandoffRequest
    assert ACKNOWLEDGE_HANDOFF.response_type is HandoffAcknowledgement
    assert ACKNOWLEDGE_HANDOFF.success_status == 200
    assert RECORD_TASK_OUTCOME.request_type is RecordTaskOutcomeRequest
    assert RECORD_TASK_OUTCOME.response_type is WorkSourceReceipt
    assert RECORD_TASK_OUTCOME.success_status == 202


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


def test_base_access_contract_uses_only_the_seven_scoped_operations() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    paths = contract["paths"]

    expected_operations = {
        ("/v1/scopes/{scope_id}/sources", "post"): "create_source",
        ("/v1/scopes/{scope_id}/sources/{source_type}/{source_id}", "get"): "get_source",
        ("/v1/scopes/{scope_id}/artifacts", "post"): "create_artifact",
        ("/v1/scopes/{scope_id}/artifacts/{family}", "get"): "list_artifacts",
        ("/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}", "get"): "get_artifact",
        ("/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}", "put"): "replace_artifact",
        (
            "/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}",
            "get",
        ): "get_artifact_revision",
    }
    actual_operations = {
        (path, method): operation["operationId"]
        for path, path_item in paths.items()
        if path.startswith("/v1/scopes/{scope_id}/sources") or path.startswith("/v1/scopes/{scope_id}/artifacts")
        for method, operation in path_item.items()
    }
    assert actual_operations == expected_operations
    assert not any(
        operation_id in {"list_sources", "search_sources", "search_artifacts", "delete_artifact", "list_scopes"}
        for operation_id in actual_operations.values()
    )
    assert not any("search-results" in path for path in paths)


def test_base_access_create_requests_leave_identity_generation_to_the_server() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    source = schemas["CreateSourceRequest"]
    assert source["required"] == ["content"]
    assert set(source["properties"]) == {"source_type", "content"}
    assert source["properties"]["source_type"]["enum"] == ["content"]
    assert source["properties"]["source_type"]["default"] == "content"

    artifact = schemas["CreateArtifactRequest"]
    assert len(artifact["oneOf"]) == 4
    assert artifact["discriminator"]["propertyName"] == "family"
    for name in (
        "CreateMemoryArtifactRequest",
        "CreateExperienceArtifactRequest",
        "CreateSkillArtifactRequest",
        "CreateHandoffArtifactRequest",
    ):
        family_request = schemas[name]
        assert family_request["required"] == ["family", "content"]
        assert set(family_request["properties"]) == {"family", "content"}
        assert not {"scope_id", "source_id", "artifact_id"} & set(family_request["properties"])

    assert CreateSourceRequest(content="evidence").source_type is SourceType.CONTENT
    assert (
        CreateArtifactRequest(
            root=CreateMemoryArtifactRequest(
                family="memory",
                content=CreateMemoryArtifactContent(
                    entries=[CreateMemoryArtifactEntry(kind="preference", text="Use Chinese")]
                ),
            )
        ).root.family
        == "memory"
    )
    memory_entry = schemas["CreateMemoryArtifactEntry"]["properties"]
    assert memory_entry["kind"]["minLength"] == 1
    assert memory_entry["kind"]["maxLength"] == 128
    for recommended in ("fact", "preference", "decision", "constraint", "working_note"):
        assert recommended in memory_entry["kind"]["description"]
    assert (
        CreateMemoryArtifactEntry(kind="business_specific", text="Keep the caller's kind").kind == "business_specific"
    )
    for invalid_kind in (" ", "x" * 129):
        with pytest.raises(ValidationError):
            CreateMemoryArtifactEntry(kind=invalid_kind, text="invalid")
    for model, payload in (
        (CreateSourceRequest, {"scope_id": "scope", "content": "evidence"}),
        (CreateSourceRequest, {"source_id": "source", "content": "evidence"}),
        (CreateArtifactRequest, {"artifact_id": "artifact", "family": "memory", "content": {}}),
    ):
        with pytest.raises(ValidationError):
            model.model_validate(payload)


def test_artifact_collection_only_accepts_pagination() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    paths = contract["paths"]

    assert "/v1/scopes/{scope_id}/sources/{source_type}" not in paths
    parameters = paths["/v1/scopes/{scope_id}/artifacts/{family}"]["get"]["parameters"]
    assert [parameter["name"] for parameter in parameters if parameter["in"] == "query"] == ["limit", "cursor"]
    assert ListArtifactsRequest().model_dump() == {"limit": 50, "cursor": None}


def test_base_access_uses_a_dedicated_source_type_reference() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    schemas = contract["components"]["schemas"]

    assert set(schemas["SourceReference"]["properties"]) == {"name", "source_id"}
    assert set(schemas["SourceTypeReference"]["properties"]) == {"source_type", "source_id"}
    for schema_name in ("ArtifactCreated", "ArtifactRevision", "ArtifactCollectionItem"):
        assert schemas[schema_name]["properties"]["sources"]["items"] == {
            "$ref": "#/components/schemas/SourceTypeReference"
        }
    for request_name in (
        "CreateMemoryArtifactRequest",
        "CreateExperienceArtifactRequest",
        "CreateSkillArtifactRequest",
        "CreateHandoffArtifactRequest",
        "ReplaceMemoryArtifactRequest",
        "ReplaceExperienceArtifactRequest",
        "ReplaceSkillArtifactRequest",
        "ReplaceHandoffArtifactRequest",
    ):
        assert "sources" not in schemas[request_name]["properties"]
    assert SourceTypeReference(source_type=SourceType.CONTENT, source_id="source").source_type is SourceType.CONTENT


def test_base_access_operations_describe_create_and_conditional_get() -> None:
    assert CREATE_SOURCE.request_type is CreateSourceRequest
    assert CREATE_ARTIFACT.request_type is CreateArtifactRequest
    assert CREATE_ARTIFACT.response_type is ArtifactCreated
    assert LIST_ARTIFACTS.request_type is ListArtifactsRequest
    assert GET_SOURCE.request_type is None
    assert GET_ARTIFACT.request_type is None
    assert GET_ARTIFACT_REVISION.request_type is None
    assert REPLACE_ARTIFACT.response_type is ArtifactRevision
    assert 304 in GET_ARTIFACT.responses

    contract = yaml.safe_load(CONTRACT_PATH.read_text())
    item_path = contract["paths"]["/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}"]
    get_parameters = {parameter["name"]: parameter for parameter in item_path["get"]["parameters"]}
    assert get_parameters["If-None-Match"]["required"] is False
    assert "content" not in item_path["get"]["responses"]["304"]

    parameters = {item["name"]: item for item in item_path["put"]["parameters"]}
    assert parameters["If-Match"]["required"] is True
    assert parameters["If-Match"]["schema"] == {"type": "string", "minLength": 1}
    assert set(item_path["put"]["responses"]) >= {"412", "428"}


def test_generated_response_models_ignore_unknown_fields() -> None:
    response = SourceRecord.model_validate({
        "scope_id": "scope",
        "source_type": "content",
        "source_id": "source",
        "content": "evidence",
        "metadata": {},
        "created_at": "2026-09-02T12:00:00Z",
        "position": 1,
        "content_digest": f"sha256:{'0' * 64}",
        "future_optional_field": "ignored",
    })

    assert "future_optional_field" not in response.model_dump()


def test_server_publishes_the_canonical_openapi_schema() -> None:
    contract = yaml.safe_load(CONTRACT_PATH.read_text())

    assert create_app(handoff_report_enabled=True).openapi() == contract
    assert (
        create_server_app(settings=ServerSettings(handoff_report=HandoffReportConfig(enabled=True))).openapi()
        == contract
    )
