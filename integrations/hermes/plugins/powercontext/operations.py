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

"""PowerContext operation names and request validation metadata."""

from __future__ import annotations

OPERATION_TOOL_MAP: dict[str, str] = {
    "powercontext_prepare_context": "prepare_context",
    "powercontext_capture_source": "capture_content_source",
    "powercontext_list_memory_entries": "list_memory_entries",
    "powercontext_revise_memory_entry": "revise_memory_entry",
    "powercontext_list_memory_changes": "list_memory_changes",
    "powercontext_flush_memory": "flush_memory",
    "powercontext_get_stats": "get_stats",
    "powercontext_create_work_contract": "create_work_contract",
    "powercontext_handoff_current_work": "handoff_current_work",
    "powercontext_acknowledge_handoff": "acknowledge_handoff",
    "powercontext_record_task_outcome": "record_task_outcome",
    "powercontext_activate_handoff": "activate_handoff",
    "powercontext_prepare_handoff": "prepare_handoff",
    "powercontext_finalize_handoff": "finalize_handoff",
    "powercontext_commit_handoff": "commit_handoff",
    "powercontext_continue_handoff": "continue_handoff",
    "powercontext_propose_experience": "propose_experience",
    "powercontext_generate_experience": "generate_experience",
    "powercontext_get_experience": "get_experience",
    "powercontext_propose_skill": "propose_skill",
    "powercontext_generate_skill": "generate_skill",
    "powercontext_get_skill": "get_skill",
    "powercontext_scan_external_skills": "scan_external_skills",
    "powercontext_list_external_skills": "list_external_skills",
    "powercontext_resolve_external_skill": "resolve_external_skill",
    "powercontext_import_external_skill": "import_external_skill",
    "powercontext_list_artifact_candidates": "list_artifact_candidates",
    "powercontext_get_artifact_candidate": "get_artifact_candidate",
    "powercontext_approve_artifact_candidate": "approve_artifact_candidate",
    "powercontext_reject_artifact_candidate": "reject_artifact_candidate",
    "powercontext_revise_artifact_candidate": "revise_artifact_candidate",
}

OPERATION_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "prepare_context": ("query",),
    "capture_content_source": ("source_id", "content"),
    "revise_memory_entry": ("citation", "kind", "text"),
    "create_work_contract": ("source_id", "contract"),
    "handoff_current_work": ("source_id", "handoff"),
    "acknowledge_handoff": ("source_id", "receiver", "status", "selection"),
    "record_task_outcome": ("source_id", "outcome"),
    "activate_handoff": ("boundary_source", "objective"),
    "prepare_handoff": ("objective", "evidence"),
    "finalize_handoff": ("draft",),
    "commit_handoff": ("handoff",),
    "continue_handoff": ("selection",),
    "propose_experience": ("proposal", "source_refs", "artifact_refs"),
    "generate_experience": ("source_refs", "artifact_refs"),
    "get_experience": ("artifact",),
    "propose_skill": ("proposal", "source_refs", "artifact_refs"),
    "generate_skill": ("origin", "source_refs", "artifact_refs"),
    "get_skill": ("artifact",),
    "resolve_external_skill": ("external_skill_id", "fingerprint"),
    "import_external_skill": ("external_skill_id", "fingerprint", "mode"),
    "get_artifact_candidate": ("candidate_id",),
    "approve_artifact_candidate": ("candidate_id", "expected_version"),
    "reject_artifact_candidate": ("candidate_id", "expected_version", "reason"),
    "revise_artifact_candidate": (
        "candidate_id",
        "expected_version",
        "proposal",
        "source_refs",
        "artifact_refs",
    ),
}
