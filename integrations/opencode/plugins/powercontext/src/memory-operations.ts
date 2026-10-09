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

import type { ClientSuccess, JsonObject, PowerContextClient } from './client.ts'
import type { OperationId } from './operations.generated.ts'

export class MemoryOperationError extends Error {
  readonly code: 'invalid_request' | 'unsupported'

  constructor(code: 'invalid_request' | 'unsupported', message: string) {
    super(message)
    this.name = 'MemoryOperationError'
    this.code = code
  }
}

interface AtomicReference extends JsonObject {
  family: 'atomic-memory'
  artifact_id: string
  revision: number
}

function atomicReference(value: unknown): AtomicReference {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    throw new MemoryOperationError('invalid_request', 'Supply the exact Atomic Memory artifact reference.')
  }
  const ref = value as JsonObject
  if (ref.family !== 'atomic-memory' || typeof ref.artifact_id !== 'string'
    || !/^[\x21-\x7E]{1,128}$/.test(ref.artifact_id)
    || typeof ref.revision !== 'number' || !Number.isSafeInteger(ref.revision) || ref.revision < 1) {
    throw new MemoryOperationError('invalid_request', 'Supply the exact Atomic Memory artifact reference.')
  }
  return { family: 'atomic-memory', artifact_id: ref.artifact_id, revision: ref.revision }
}

/** Translate the maintained Memory tool names at their identity and write boundary. */
export async function requestMemoryOperation(
  client: PowerContextClient,
  operationId: OperationId,
  payload: JsonObject | undefined,
  scopeId: string,
  signal?: AbortSignal,
): Promise<ClientSuccess | undefined> {
  const body = payload ?? {}
  if (operationId === 'list_memory_entries') {
    return client.request('list_atomic_memories', {
      scope_id: scopeId,
      states: body.states ?? (body.include_inactive ? ['active', 'forgotten', 'merged', 'retired'] : ['active']),
      limit: body.limit ?? 50,
      cursor: body.cursor,
    }, signal)
  }
  if (!['get_memory_entry', 'revise_memory_entry', 'retire_memory_entry'].includes(operationId)) return undefined
  if (body.citation !== undefined && body.artifact !== undefined) {
    throw new MemoryOperationError('invalid_request', 'Choose one exact artifact reference or one historical citation.')
  }
  if (body.artifact === undefined) {
    if (operationId !== 'get_memory_entry') {
      throw new MemoryOperationError('unsupported',
        'Legacy Memory citations are read-only. Use an Atomic Memory artifact reference for changes.')
    }
    if (body.citation === undefined) {
      throw new MemoryOperationError('invalid_request', 'Supply an Atomic Memory reference or a full historical citation.')
    }
    return client.request('get_memory_entry', { scope_id: scopeId, citation: body.citation }, signal)
  }
  const ref = atomicReference(body.artifact)
  const identity = { scope_id: scopeId, family: ref.family, artifact_id: ref.artifact_id }
  if (operationId === 'get_memory_entry') {
    const head = await client.request('get_artifact', identity, signal)
    const current = head.value as JsonObject | null
    if (head.kind === 'json' && current?.revision === ref.revision) return head
    // An exact historical read must never carry a current head's write validator.
    return client.request('get_artifact_revision', { ...identity, revision: ref.revision }, signal)
  }
  if (operationId === 'revise_memory_entry') {
    if (typeof body.if_match !== 'string' || body.if_match !== `"revision:${ref.revision}"`) {
      throw new MemoryOperationError('invalid_request',
        'Use the content ETag returned by pc_memory_get for this exact current revision.')
    }
    return client.request('replace_artifact', {
      ...identity,
      if_match: body.if_match,
      content: { kind: body.kind, text: body.text },
    }, signal)
  }
  if (typeof body.state_version !== 'number' || !Number.isSafeInteger(body.state_version) || body.state_version < 0) {
    throw new MemoryOperationError('invalid_request', 'Supply the current state_version from search, list or pc_memory_state.')
  }
  return client.request('change_atomic_memory_lifecycle', {
    scope_id: scopeId,
    target: { artifact: ref, state_version: body.state_version },
    state: 'forgotten',
  }, signal)
}
