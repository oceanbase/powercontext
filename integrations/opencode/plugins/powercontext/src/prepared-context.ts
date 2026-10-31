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

import { InvalidResponseError } from './errors.ts'

export const PREPARED_CONTEXT_SCHEMA = 'powercontext.prepared-context.v1'
const FIELDS = new Set(['schema', 'status', 'content', 'content_bytes'])

export interface PreparedContext {
  schema: typeof PREPARED_CONTEXT_SCHEMA
  status: 'ready' | 'empty'
  content: string | null
  content_bytes: number
}

export function validatePreparedContext(value: unknown, maxBytes: number): PreparedContext {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new InvalidResponseError('/v1/context/prepare')
  const record = value as Record<string, unknown>
  if (Object.keys(record).length !== FIELDS.size || Object.keys(record).some((key) => !FIELDS.has(key))) {
    throw new InvalidResponseError('/v1/context/prepare')
  }
  if (record.schema !== PREPARED_CONTEXT_SCHEMA) throw new InvalidResponseError('/v1/context/prepare')
  if (!Number.isInteger(record.content_bytes) || Number(record.content_bytes) < 0 || Number(record.content_bytes) > maxBytes) {
    throw new InvalidResponseError('/v1/context/prepare')
  }
  if (record.status === 'empty' && record.content === null && record.content_bytes === 0) {
    return { schema: PREPARED_CONTEXT_SCHEMA, status: 'empty', content: null, content_bytes: 0 }
  }
  if (
    record.status !== 'ready'
    || typeof record.content !== 'string'
    || Buffer.byteLength(record.content, 'utf8') !== record.content_bytes
  ) {
    throw new InvalidResponseError('/v1/context/prepare')
  }
  return {
    schema: PREPARED_CONTEXT_SCHEMA,
    status: 'ready',
    content: record.content,
    content_bytes: Number(record.content_bytes),
  }
}

// `POST /v1/context/prepare` bounds `query` at 8,192 characters. The byte bound is the hosts' own
// margin, shared with the hook-based hosts, so a recall also fits a server that measures bytes.
export const MAX_QUERY_CHARACTERS = 8_192
export const MAX_QUERY_BYTES = 8_192

/**
 * Reduce a prompt to the recall query. A prompt within both bounds is sent as submitted, trimmed.
 * A longer prompt keeps its most recent text, which holds the turn in hand, cut between characters
 * so that the query fits both bounds and splits no character.
 */
export function boundedRecallQuery(prompt: string): string {
  const characters = Array.from(prompt.trim())
  let start = characters.length
  let bytes = 0
  while (start > 0 && characters.length - start < MAX_QUERY_CHARACTERS) {
    const size = Buffer.byteLength(characters[start - 1]!, 'utf8')
    if (bytes + size > MAX_QUERY_BYTES) break
    bytes += size
    start -= 1
  }
  return characters.slice(start).join('').trim()
}
