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

export function workspaceBindingKey(cwd: string): ScopeBindingKey {
  return {
    integration: 'pi',
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
  cwd: string,
  configuredScopeId?: string,
): Promise<string> {
  const response = await client.request('resolve_scope_binding', {
    explicit_scope_id: configuredScopeId,
    binding_keys: await workspaceBindingKeys(cwd),
  })
  const value = response.value
  const scopeId = value && typeof value === 'object' ? (value as { scope_id?: unknown }).scope_id : undefined
  if (typeof scopeId !== 'string' || !scopeId.trim()) throw new Error('PowerContext returned an invalid Scope')
  return scopeId
}
