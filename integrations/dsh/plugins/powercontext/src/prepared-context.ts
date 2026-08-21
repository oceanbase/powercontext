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
import { MAX_CONTEXT_BYTES } from './errors.ts'

export const PREPARED_CONTEXT_SCHEMA = 'powercontext.prepared-context.v1'
const PREPARED_FIELDS = new Set(['schema', 'status', 'content', 'content_bytes'])

export interface PreparedContext {
  schema: typeof PREPARED_CONTEXT_SCHEMA
  status: 'ready' | 'empty'
  content: string | null
  content_bytes: number
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function validatePreparedContext(
  response: unknown,
  path = '/v1/context/prepare',
  maxBytes = MAX_CONTEXT_BYTES,
): PreparedContext {
  if (!isRecord(response)) throw new InvalidResponseError(path)
  const keys = Object.keys(response)
  if (keys.length !== PREPARED_FIELDS.size || keys.some((key) => !PREPARED_FIELDS.has(key))) {
    throw new InvalidResponseError(path)
  }
  if (response.schema !== PREPARED_CONTEXT_SCHEMA) throw new InvalidResponseError(path)
  const status = response.status
  const content = response.content
  const contentBytes = response.content_bytes
  if (typeof contentBytes !== 'number' || !Number.isInteger(contentBytes) || contentBytes < 0) {
    throw new InvalidResponseError(path)
  }
  if (status === 'empty') {
    if (content !== null || contentBytes !== 0) throw new InvalidResponseError(path)
    return { schema: PREPARED_CONTEXT_SCHEMA, status, content: null, content_bytes: 0 }
  }
  if (status !== 'ready' || typeof content !== 'string' || !content.trim()) {
    throw new InvalidResponseError(path)
  }
  const encoded = Buffer.from(content, 'utf8')
  if (encoded.byteLength !== contentBytes || contentBytes > maxBytes) {
    throw new InvalidResponseError(path)
  }
  return { schema: PREPARED_CONTEXT_SCHEMA, status, content, content_bytes: contentBytes }
}
