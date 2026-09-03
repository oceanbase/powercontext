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

export const UNSCOPED_MESSAGE = 'PowerContext could not resolve the current Scope.'

export function sessionCwd(cwd: string | undefined): string | undefined {
  const value = cwd?.trim()
  return value ? value : undefined
}

export function workspaceBindingKey(cwd: string): { integration: string; kind: string; external_id: string } {
  return {
    integration: 'dsh',
    kind: 'workspace',
    external_id: createHash('sha256').update(resolve(cwd)).digest('hex'),
  }
}

export async function resolveScopeId(
  client: PowerContextClient,
  cwd: string | undefined,
  configuredScopeId?: string,
): Promise<string | undefined> {
  const workspace = sessionCwd(cwd)
  const response = await client.request('resolve_scope_binding', {
    explicit_scope_id: configuredScopeId,
    binding_keys: workspace ? [workspaceBindingKey(workspace)] : [],
  })
  const value = response.value
  const scopeId = value && typeof value === 'object' ? (value as { scope_id?: unknown }).scope_id : undefined
  return typeof scopeId === 'string' && scopeId.trim() ? scopeId : undefined
}
