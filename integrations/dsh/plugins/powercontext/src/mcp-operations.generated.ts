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

// generated from the Server's public MCP catalog; do not edit.

export const MCP_OPERATIONS = {
  "acknowledge_handoff": {
    "readOnly": false
  },
  "activate_handoff": {
    "readOnly": false
  },
  "approve_artifact_candidate": {
    "readOnly": false
  },
  "capture_content_source": {
    "readOnly": false
  },
  "clear_scope_binding": {
    "readOnly": false
  },
  "commit_handoff": {
    "readOnly": false
  },
  "continue_handoff": {
    "readOnly": true
  },
  "create_dream_run": {
    "readOnly": false
  },
  "create_scope": {
    "readOnly": false
  },
  "create_work_contract": {
    "readOnly": false
  },
  "finalize_handoff": {
    "readOnly": false
  },
  "generate_experience": {
    "readOnly": false
  },
  "generate_skill": {
    "readOnly": false
  },
  "get_artifact_candidate": {
    "readOnly": true
  },
  "get_dream_run": {
    "readOnly": true
  },
  "get_experience": {
    "readOnly": true
  },
  "get_handoff_report": {
    "readOnly": true
  },
  "get_memory_capacity": {
    "readOnly": true
  },
  "get_memory_entry": {
    "readOnly": true
  },
  "get_scope": {
    "readOnly": true
  },
  "get_skill": {
    "readOnly": true
  },
  "get_topic_memory": {
    "readOnly": true
  },
  "handoff_current_work": {
    "readOnly": false
  },
  "import_external_skill": {
    "readOnly": false
  },
  "list_artifact_candidates": {
    "readOnly": true
  },
  "list_dream_runs": {
    "readOnly": true
  },
  "list_external_skills": {
    "readOnly": true
  },
  "list_managed_skills": {
    "readOnly": true
  },
  "list_memory_entries": {
    "readOnly": true
  },
  "list_scopes": {
    "readOnly": true
  },
  "prepare_handoff_hint": {
    "readOnly": true
  },
  "propose_experience": {
    "readOnly": false
  },
  "propose_skill": {
    "readOnly": false
  },
  "publish_artifact": {
    "readOnly": false
  },
  "query_code": {
    "readOnly": true
  },
  "record_task_outcome": {
    "readOnly": false
  },
  "reject_artifact_candidate": {
    "readOnly": false
  },
  "remember_memory": {
    "readOnly": false
  },
  "resolve_external_skill": {
    "readOnly": true
  },
  "resolve_scope_binding": {
    "readOnly": true
  },
  "retire_memory_entry": {
    "readOnly": false
  },
  "revise_artifact_candidate": {
    "readOnly": false
  },
  "revise_memory_entry": {
    "readOnly": false
  },
  "scan_external_skills": {
    "readOnly": false
  },
  "search_memory": {
    "readOnly": true
  },
  "search_topic_memory": {
    "readOnly": true
  },
  "set_scope_binding": {
    "readOnly": false
  }
} as const
