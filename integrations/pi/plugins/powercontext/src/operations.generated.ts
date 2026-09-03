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
  get_liveness: { method: 'GET', path: '/health/live', location: null, scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_readiness: { method: 'GET', path: '/health/ready', location: null, scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_capabilities: { method: 'GET', path: '/v1/capabilities', location: null, scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  capture_content_source: { method: 'POST', path: '/v1/sources/content', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [202], emptyStatuses: [] },
  register_source_definition: { method: 'POST', path: '/v1/source-definitions/register', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_connector_checkpoint: { method: 'POST', path: '/v1/connector-checkpoints/get', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  submit_source_observation: { method: 'POST', path: '/v1/source-observations', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [202], emptyStatuses: [] },
  commit_connector_checkpoint: { method: 'POST', path: '/v1/connector-checkpoints/commit', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  prepare_context: { method: 'POST', path: '/v1/context/prepare', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  create_work_contract: { method: 'POST', path: '/v1/work/contracts/create', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [202], emptyStatuses: [] },
  handoff_current_work: { method: 'POST', path: '/v1/work/handoffs/prepare-current', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  acknowledge_handoff: { method: 'POST', path: '/v1/work/handoffs/acknowledge', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  record_task_outcome: { method: 'POST', path: '/v1/work/outcomes/record', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [202], emptyStatuses: [] },
  activate_handoff: { method: 'POST', path: '/v1/handoff/activate', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  prepare_handoff: { method: 'POST', path: '/v1/handoff/prepare', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  finalize_handoff: { method: 'POST', path: '/v1/handoff/finalize', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  commit_handoff: { method: 'POST', path: '/v1/handoff/commit', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  continue_handoff: { method: 'POST', path: '/v1/handoff/continue', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  flush_memory: { method: 'POST', path: '/v1/memory/flush', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  remember_memory: { method: 'POST', path: '/v1/memory/remember', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  search_memory: { method: 'POST', path: '/v1/memory/search', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  list_memory_entries: { method: 'POST', path: '/v1/memory/entries/list', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_memory_entry: { method: 'POST', path: '/v1/memory/entries/get', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  revise_memory_entry: { method: 'POST', path: '/v1/memory/entries/revise', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  retire_memory_entry: { method: 'POST', path: '/v1/memory/entries/retire', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  list_memory_changes: { method: 'POST', path: '/v1/memory/changes', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  propose_experience: { method: 'POST', path: '/v1/experience/propose', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  generate_experience: { method: 'POST', path: '/v1/experience/generate', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_experience: { method: 'POST', path: '/v1/experience/get', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  propose_skill: { method: 'POST', path: '/v1/skill/propose', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  generate_skill: { method: 'POST', path: '/v1/skill/generate', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_skill: { method: 'POST', path: '/v1/skill/get', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  list_managed_skills: { method: 'POST', path: '/v1/skill/library', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  update_skill_lifecycle: { method: 'POST', path: '/v1/skill/lifecycle', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_skill_package_manifest: { method: 'POST', path: '/v1/skill/package/manifest', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  download_skill_package: { method: 'POST', path: '/v1/skill/package/download', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  propose_skill_package: { method: 'POST', path: '/v1/skill/package/propose', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  record_skill_usage: { method: 'POST', path: '/v1/skill/usage', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  list_remote_skill_targets: { method: 'POST', path: '/v1/skill/remote/targets', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  create_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/create', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  enroll_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/enroll', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  rename_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/rename', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  revoke_remote_skill_target: { method: 'POST', path: '/v1/skill/remote/target/revoke', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  publish_remote_skill: { method: 'POST', path: '/v1/skill/remote/publication/publish', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  unpublish_remote_skill: { method: 'POST', path: '/v1/skill/remote/publication/unpublish', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  reconcile_remote_skills: { method: 'POST', path: '/v1/skill/remote/reconcile', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  download_remote_skill_package: { method: 'POST', path: '/v1/skill/remote/package/download', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  record_remote_skill_receipt: { method: 'POST', path: '/v1/skill/remote/receipt', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  scan_external_skills: { method: 'POST', path: '/v1/external-skills/scan', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  list_external_skills: { method: 'POST', path: '/v1/external-skills/list', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  resolve_external_skill: { method: 'POST', path: '/v1/external-skills/resolve', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  import_external_skill: { method: 'POST', path: '/v1/external-skills/import', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  list_artifact_candidates: { method: 'POST', path: '/v1/artifact-candidates/list', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/get', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  approve_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/approve', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  reject_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/reject', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  revise_artifact_candidate: { method: 'POST', path: '/v1/artifact-candidates/revise', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_stats: { method: 'GET', path: '/v1/stats', location: "query", scope: true, pathParams: [], queryParams: ['scope_id','period'], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  create_handoff_report_project: { method: 'POST', path: '/v1/handoff-reports/projects/create', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  list_handoff_report_projects: { method: 'POST', path: '/v1/handoff-reports/projects/list', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  list_handoff_report_known_scopes: { method: 'POST', path: '/v1/handoff-reports/scopes/list-known', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_handoff_report_project: { method: 'POST', path: '/v1/handoff-reports/projects/get', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  update_handoff_report_project: { method: 'POST', path: '/v1/handoff-reports/projects/update', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  register_handoff_report_workstream: { method: 'POST', path: '/v1/handoff-reports/workstreams/register', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  list_handoff_report_workstreams: { method: 'POST', path: '/v1/handoff-reports/workstreams/list', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  update_handoff_report_workstream: { method: 'POST', path: '/v1/handoff-reports/workstreams/update', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_handoff_report: { method: 'POST', path: '/v1/handoff-reports/get', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  record_handoff_report_activity: { method: 'POST', path: '/v1/handoff-reports/activities/record', location: "body", scope: true, pathParams: [], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  list_handoff_report_activities: { method: 'POST', path: '/v1/handoff-reports/activities/list', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  purge_handoff_report_activities: { method: 'POST', path: '/v1/handoff-reports/activities/purge', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_handoff_report_workspace: { method: 'POST', path: '/v1/handoff-reports/workspace-bindings/get', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  attach_handoff_report_workspace: { method: 'POST', path: '/v1/handoff-reports/workspace-bindings/attach', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  detach_handoff_report_workspace: { method: 'POST', path: '/v1/handoff-reports/workspace-bindings/detach', location: "body", scope: false, pathParams: [], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  create_source: { method: 'POST', path: '/v1/scopes/{scope_id}/sources', location: "body", scope: true, pathParams: ['scope_id'], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  get_source: { method: 'GET', path: '/v1/scopes/{scope_id}/sources/{source_type}/{source_id}', location: null, scope: true, pathParams: ['scope_id','source_type','source_id'], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  create_artifact: { method: 'POST', path: '/v1/scopes/{scope_id}/artifacts', location: "body", scope: true, pathParams: ['scope_id'], queryParams: [], headerParams: [], successStatuses: [201], emptyStatuses: [] },
  list_artifacts: { method: 'GET', path: '/v1/scopes/{scope_id}/artifacts/{family}', location: "query", scope: true, pathParams: ['scope_id','family'], queryParams: ['limit','cursor'], headerParams: [], successStatuses: [200], emptyStatuses: [] },
  get_artifact: { method: 'GET', path: '/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}', location: null, scope: true, pathParams: ['scope_id','family','artifact_id'], queryParams: [], headerParams: ['If-None-Match'], successStatuses: [200,304], emptyStatuses: [304] },
  replace_artifact: { method: 'PUT', path: '/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}', location: "body", scope: true, pathParams: ['scope_id','family','artifact_id'], queryParams: [], headerParams: ['If-Match'], successStatuses: [200], emptyStatuses: [] },
  get_artifact_revision: { method: 'GET', path: '/v1/scopes/{scope_id}/artifacts/{family}/{artifact_id}/revisions/{revision}', location: null, scope: true, pathParams: ['scope_id','family','artifact_id','revision'], queryParams: [], headerParams: [], successStatuses: [200], emptyStatuses: [] },
} as const

export type OperationId = keyof typeof OPERATIONS

export type OperationSpec = (typeof OPERATIONS)[OperationId]

export const OPERATION_IDS = Object.keys(OPERATIONS) as OperationId[]
