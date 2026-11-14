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

// Explicit writes still use these markers; automatic paths follow the shared prompt-privacy rule below.
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

const SECRET_PATTERNS = [
  /-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|$)/giu,
  // Consume complete headers before the assignment rule can consume just a scheme or first cookie.
  /(?<![\w-])["']?\b(?:authorization|proxy-authorization|cookie|set-cookie)\b["']?[ \t]*[:=][ \t]*[^\r\n]*(?:\r?\n[ \t]+[^\r\n]*)*/giu,
  /(?<![\w-])["']?\b(?:api[_ -]?key|access[_ -]?(?:key|token)|client[_ -]?secret|secret(?:[_ -]?key)?|private[_ -]?key|password|passwd|passphrase|token|authorization|cookie)\b["']?\s*[:=]\s*(?:"[^"\r\n]*"|'[^'\r\n]*'|`[^`\r\n]*`|[^\s,;}\]]+)/giu,
  /(?<![\w-])bearer\s+[A-Za-z0-9._~+/=-]{8,}(?![\w-])/giu,
  /(?<![\w-])(?:sk-[A-Za-z0-9][A-Za-z0-9_-]{7,}|github_pat_[A-Za-z0-9_]{8,}|gh[pousr]_[A-Za-z0-9_]{8,}|xox[baprs]-[A-Za-z0-9-]{8,})(?![\w-])/giu,
] as const

/** Return text with recognized credential-shaped values replaced. */
export function scrubSecrets(text: string, authorization?: string): string {
  const credential = authorization?.replace(/^Bearer\s+/iu, '')
  if (credential) text = text.replaceAll(credential, '[REDACTED]')
  return SECRET_PATTERNS.reduce((scrubbed, pattern) => scrubbed.replace(pattern, '[REDACTED]'), text)
}

/** Detect secrets without rejecting ordinary words that merely contain a marker. */
export function containsSecret(text: string, authorization?: string): boolean {
  return scrubSecrets(text, authorization) !== text
}

function containsSecretMarker(text: string): boolean {
  return SECRET_MARKERS.some((marker) => text.includes(marker))
}

function containsSecretValue(value: unknown): boolean {
  if (typeof value === 'string') return containsSecretMarker(value)
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
