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

import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import type { PowerContextClient } from './client.ts'

type ScopeBindingKey = { integration: string; kind: string; external_id: string }

export function sessionBindingKey(sessionID: string): ScopeBindingKey {
  return { integration: 'opencode', kind: 'session', external_id: sessionID }
}

export function workspaceBindingKey(cwd: string): ScopeBindingKey {
  return {
    integration: 'opencode',
    kind: 'workspace',
    external_id: createHash('sha256').update(resolve(cwd)).digest('hex'),
  }
}

export async function resolveScopeId(
  client: PowerContextClient,
  input: { cwd: string; sessionID: string; configuredScopeId?: string; persistSession?: boolean },
): Promise<string> {
  const response = await client.request('resolve_scope_binding', {
    explicit_scope_id: input.configuredScopeId,
    binding_keys: [sessionBindingKey(input.sessionID), workspaceBindingKey(input.cwd)],
  })
  const value = response.value
  const scopeId = value && typeof value === 'object' ? (value as { scope_id?: unknown }).scope_id : undefined
  if (typeof scopeId !== 'string' || !scopeId.trim()) throw new Error('PowerContext returned an invalid Scope')
  if (input.persistSession && !input.configuredScopeId) {
    await client.request('set_scope_binding', { key: sessionBindingKey(input.sessionID), scope_id: scopeId })
  }
  return scopeId
}
