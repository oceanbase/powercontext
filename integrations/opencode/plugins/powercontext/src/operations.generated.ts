/*
 * Copyright (c) 2026 OceanBase.
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

// generated from openapi/powercontext.yaml; do not edit.

export const OPERATIONS = {
  get_liveness: { method: 'GET', path: '/health/live', location: null, scopeMode: 'none', pathParameters: [] },
  get_readiness: { method: 'GET', path: '/health/ready', location: null, scopeMode: 'none', pathParameters: [] },
  get_capabilities: { method: 'GET', path: '/v1/capabilities', location: null, scopeMode: 'none', pathParameters: [] },
  list_scopes: { method: 'GET', path: '/v1/scopes', location: null, scopeMode: 'none', pathParameters: [] },
  create_scope: { method: 'POST', path: '/v1/scopes', location: "body", scopeMode: 'none', pathParameters: [] },
  publish_artifact: { method: 'POST', path: '/v1/artifact-publications', location: "body", scopeMode: 'none', pathParameters: [] },
  get_scope: { method: 'GET', path: '/v1/scopes/{scope_id}', location: null, scopeMode: 'none', pathParameters: ['scope_id'] },
  update_scope: { method: 'PUT', path: '/v1/scopes/{scope_id}', location: "body", scopeMode: 'none', pathParameters: ['scope_id'] },
  get_default_scope: { method: 'GET', path: '/v1/scopes/default', location: null, scopeMode: 'none', pathParameters: [] },
  set_default_scope: { method: 'PUT', path: '/v1/scopes/default', location: "body", scopeMode: 'none', pathParameters: [] },
  resolve_scope_selection: { method: 'POST', path: '/v1/scopes/selection/resolve', location: "body", scopeMode: 'none', pathParameters: [] },
  resolve_scope_binding: { method: 'POST', path: '/v1/scope-bindings/resolve', location: "body", scopeMode: 'none', pathParameters: [] },
  set_scope_binding: { method: 'PUT', path: '/v1/scope-bindings', location: "body", scopeMode: 'none', pathParameters: [] },
  clear_scope_binding: { method: 'POST', path: '/v1/scope-bindings/clear', location: "body", scopeMode: 'none', pathParameters: [] },
  capture_content_source: { method: 'POST', path: '/v1/sources/content', location: "body", scopeMode: 'current', pathParameters: [] },
  register_source_definition: { method: 'POST', path: '/v1/source-definitions/register', location: "body", scopeMode: 'none', pathParameters: [] },
  get_connector_checkpoint: { method: 'POST', path: '/v1/connector-checkpoints/get', location: "body", scopeMode: 'none', pathParameters: [] },
  submit_source_observation: { method: 'POST', path: '/v1/source-observations', location: "body", scopeMode: 'none', pathParameters: [] },
  commit_connector_checkpoint: { method: 'POST', path: '/v1/connector-checkpoints/commit', location: "body", scopeMode: 'none', pathParameters: [] },
  prepare_context: { method: 'POST', path: '/v1/context/prepare', location: "body", scopeMode: 'current', pathParameters: [] },
  create_work_contract: { method: 'POST', path: '/v1/work/contracts/create', location: "body", scopeMode: 'current', pathParameters: [] },
  handoff_current_work: { method: 'POST', path: '/v1/work/handoffs/prepare-current', location: "body", scopeMode: 'current', pathParameters: [] },
  acknowledge_handoff: { method: 'POST', path: '/v1/work/handoffs/acknowledge', location: "body", scopeMode: 'current', pathParameters: [] },
  record_task_outcome: { method: 'POST', path: '/v1/work/outcomes/record', location: "body", scopeMode: 'current', pathParameters: [] },
  activate_handoff: { method: 'POST', path: '/v1/handoff/activate', location: "body", scopeMode: 'current', pathParameters: [] },
  prepare_handoff: { method: 'POST', path: '/v1/handoff/prepare', location: "body", scopeMode: 'current', pathParameters: [] },
  finalize_handoff: { method: 'POST', path: '/v1/handoff/finalize', location: "body", scopeMode: 'current', pathParameters: [] },
  commit_handoff: { method: 'POST', path: '/v1/handoff/commit', location: "body", scopeMode: 'current', pathParameters: [] },
  continue_handoff: { method: 'POST', path: '/v1/handoff/continue', location: "body", scopeMode: 'current', pathParameters: [] },
  flush_memory: { method: 'POST', path: '/v1/memory/flush', location: "body", scopeMode: 'current', pathParameters: [] },
  remember_memory: { method: 'POST', path: '/v1/memory/remember', location: "body", scopeMode: 'current', pathParameters: [] },
  search_memory: { method: 'POST', path: '/v1/memory/search', location: "body", scopeMode: 'current', pathParameters: [] },
  list_memory_entries: { method: 'POST', path: '/v1/memory/entries/list', location: "body", scopeMode: 'current', pathParameters: [] },
  get_memory_entry: { method: 'POST', path: '/v1/memory/entries/get', location: "body", scopeMode: 'current', pathParameters: [] },
  revise_memory_entry: { method: 'POST', path: '/v1/memory/entries/revise', location: "body", scopeMode: 'current', pathParameters: [] },
  retire_memory_entry: { method: 'POST', path: '/v1/memory/entries/retire', location: "body", scopeMode: 'current', pathParameters: [] },
  list_memory_changes: { method: 'POST', path: '/v1/memory/changes', location: "body", scopeMode: 'current', pathParameters: [] },
  propose_experience: { method: 'POST', path: '/v1/experience/propose', location: "body", scopeMode: 'current', pathParameters: [] },
  generate_experience: { method: 'POST', path: '/v1/experience/generate', location: "body", scopeMode: 'current', pathParameters: [] },
  get_experience: { method: 'POST', path: '/v1/experience/get', location: "body", scopeMode: 'current', pathParameters: [] },
  propose_skill: { method: 'POST', path: '/v1/skill/propose', location: "body", scopeMode: 'current', pathParameters: [] },
  generate_skill: { method: 'POST', path: '/v1/skill/generate', location: "body", scopeMode: 'current', pathParameters: [] },
  get_skill: { method: 'POST', path: '/v1/skill/get', location: "body", scopeMode: 'current', pathParameters: [] },
  list_managed_skills: { method: 'POST', path: '/v1/skill/library', location: "body", scopeMode: 'current', pathParameters: [] },
  update_skill_lifecycle: { method: 'POST', path: '/v1/skill/lifecycle', location: "body", scopeMode: 'current', pathParameters: [] },
  get_skill_package_manifest: { method: 'POST', path: '/v1/skill/package/manifest', location: "body", scopeMode: 'current', pathParameters: [] },
  download_skill_package: { method: 'POST', path: '/v1/skill/package/download', location: "body", scopeMode: 'current', pathParameters: [] },
  propose_skill_package: { method: 'POST', path: '/v1/skill/package/propose', location: "body", scopeMode: 'current', pathParameters: [] },
  record_skill_usage: { method: 'POST', path: '/v1/skill/usage', location: "body", scopeMode: 'current', pathParameters: [] },
  list_remote_skill_targets: { method: 'POST', path: '/v1/skill/remote/targets', location: "body", scopeMode: 'current', pathParameters: [] },
  create_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/create', location: "body", scopeMode: 'current', pathParameters: [] },
  enroll_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/enroll', location: "body", scopeMode: 'none', pathParameters: [] },
  rename_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/rename', location: "body", scopeMode: 'current', pathParameters: [] },
  revoke_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/revoke', location: "body", scopeMode: 'current', pathParameters: [] },
  publish_remote_skill: { method: 'POST', path: '/v1/skill/remote/publication/publish', location: "body", scopeMode: 'current', pathParameters: [] },
  unpublish_remote_skill: { method: 'POST', path: '/v1/skill/remote/publication/unpublish', location: "body", scopeMode: 'current', pathParameters: [] },
  reconcile_remote_skills: { method: 'POST', path: '/v1/skill/remote/reconcile', location: "body", scopeMode: 'none', pathParameters: [] },
  download_remote_skill_package: { method: 'POST', path: '/v1/skill/remote/package/download', location: "body", scopeMode: 'none', pathParameters: [] },
  record_remote_skill_receipt: { method: 'POST', path: '/v1/skill/remote/receipt', location: "body", scopeMode: 'none', pathParameters: [] },
  scan_external_skills: { method: 'POST', path: '/v1/external-skills/scan', location: "body", scopeMode: 'current', pathParameters: [] },
  list_external_skills: { method: 'POST', path: '/v1/external-skills/list', location: "body", scopeMode: 'current', pathParameters: [] },
  resolve_external_skill: { method: 'POST', path: '/v1/external-skills/resolve', location: "body", scopeMode: 'current', pathParameters: [] },
  import_external_skill: { method: 'POST', path: '/v1/external-skills/import', location: "body", scopeMode: 'current', pathParameters: [] },
  list_artifact_candidates: { method: 'POST', path: '/v1/artifact-candidates/list', location: "body", scopeMode: 'current', pathParameters: [] },
  get_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/get', location: "body", scopeMode: 'current', pathParameters: [] },
  approve_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/approve', location: "body", scopeMode: 'current', pathParameters: [] },
  reject_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/reject', location: "body", scopeMode: 'current', pathParameters: [] },
  revise_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/revise', location: "body", scopeMode: 'current', pathParameters: [] },
  get_stats: { method: 'POST', path: '/v1/stats', location: "body", scopeMode: 'selection', pathParameters: [] },
  get_handoff_report: { method: 'POST', path: '/v1/handoff-reports/get', location: "body", scopeMode: 'selection', pathParameters: [] },
} as const

export type OperationId = keyof typeof OPERATIONS

export type OperationSpec = (typeof OPERATIONS)[OperationId]

export const OPERATION_IDS = Object.keys(OPERATIONS) as OperationId[]
