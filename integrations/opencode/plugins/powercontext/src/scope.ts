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
import { workspacePaths } from './git-worktree.ts'

type ScopeBindingKey = { integration: string; kind: string; external_id: string }

const GIT_TIMEOUT_MS = 500

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

/** Return the workspace keys that resolution tries in order, with a linked worktree's main checkout after its own. */
export async function workspaceBindingKeys(cwd: string): Promise<ScopeBindingKey[]> {
  return (await workspacePaths(cwd, false, GIT_TIMEOUT_MS)).map(workspaceBindingKey)
}

export async function resolveScopeId(
  client: PowerContextClient,
  input: { cwd?: string; sessionID?: string; configuredScopeId?: string; persistSession?: boolean },
  signal?: AbortSignal,
): Promise<string> {
  const sessionID = input.sessionID?.trim()
  const cwd = input.cwd?.trim()
  const bindingKeys: ScopeBindingKey[] = []
  if (sessionID) bindingKeys.push(sessionBindingKey(sessionID))
  if (cwd) bindingKeys.push(...await workspaceBindingKeys(cwd))
  const response = await client.request('resolve_scope_binding', {
    explicit_scope_id: input.configuredScopeId,
    binding_keys: bindingKeys,
  }, signal)
  const value = response.value
  const scopeId = value && typeof value === 'object' ? (value as { scope_id?: unknown }).scope_id : undefined
  if (typeof scopeId !== 'string' || !scopeId.trim()) throw new Error('PowerContext returned an invalid Scope')
  const resolved = scopeId.trim()
  if (input.persistSession && !input.configuredScopeId && sessionID) {
    await client.request('set_scope_binding', { key: sessionBindingKey(sessionID), scope_id: resolved }, signal)
  }
  return resolved
}
