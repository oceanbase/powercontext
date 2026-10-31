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

const SECRET_MARKERS = ['sk-', 'api_key', 'BEGIN PRIVATE']
const PAYLOAD_WRITES = new Set([
  'remember_memory', 'capture_content_source', 'capture_working_note', 'revise_memory_entry',
  'replace_artifact', 'change_atomic_memory_lifecycle',
])
const CONTENT_FIELDS = new Map<string, readonly string[]>([
  ['pc_source_add', ['input']],
  ['create_scope', ['title', 'summary', 'external_references']],
  ['create_work_contract', ['contract']],
  ['record_task_outcome', ['outcome']],
  ['propose_experience', ['proposal', 'reason']],
  ['propose_skill', ['proposal', 'reason']],
  ['revise_candidate', ['proposal', 'reason']],
  ['generate_experience', ['reason']],
  ['generate_skill', ['reason']],
  ['reject_candidate', ['reason']],
  ['import_external_skill', ['reason']],
  ['merge_atomic_memories', ['content']],
  ['handoff_current_work', ['handoff']],
  ['acknowledge_handoff', ['prepared', 'message']],
  ['activate_handoff', ['objective']],
  ['prepare_handoff', ['objective']],
  ['finalize_handoff', ['draft']],
  ['commit_handoff', ['handoff']],
])

export function containsSecret(text: string): boolean {
  return SECRET_MARKERS.some((marker) => text.includes(marker))
}

function containsSecretValue(value: unknown): boolean {
  if (typeof value === 'string') return containsSecret(value)
  if (!value || typeof value !== 'object') return false
  return Object.values(value).some(containsSecretValue)
}

export function hasSecretContent(operation: string, payload: unknown): boolean {
  if (PAYLOAD_WRITES.has(operation)) return containsSecretValue(payload)
  const fields = CONTENT_FIELDS.get(operation)
  if (!fields || !payload || typeof payload !== 'object') return false
  const content = payload as Record<string, unknown>
  return fields.some(field => containsSecretValue(content[field]))
}
