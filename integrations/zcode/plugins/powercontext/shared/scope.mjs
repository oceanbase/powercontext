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
import { realpathSync } from 'node:fs'
import { resolve } from 'node:path'
import { workspacePathsSync } from './git-worktree.mjs'
import { nonempty } from './settings.mjs'
import { request } from './transport.mjs'

export function sha256(value) {
  return createHash('sha256').update(value).digest('hex')
}

export function isScopeId(value) {
  // Scope IDs are opaque; the built-in contract counts Unicode characters and rejects blank values.
  return typeof value === 'string' && [...value].length <= 256 && /[^\p{White_Space}\u001c-\u001f]/u.test(value)
}

export function sessionIdentity(input) {
  const ids = [input.sessionId, input.session_id, process.env.ZCODE_SESSION_ID].map(nonempty).filter(Boolean)
  if (new Set(ids).size > 1) throw new Error('session_conflict')
  const id = ids[0]
  if (id && (id.length > 256 || /[\r\n\0]/u.test(id))) throw new Error('invalid_session')
  return id
}

// The Git top level, and, in a linked worktree, its main checkout's root after it.
function workspaces(cwd) {
  const directory = resolve(cwd)
  const [root, main] = workspacePathsSync(directory, true, 500)
  const own = realPath(root) ?? realPath(directory) ?? directory
  // The helper returns the main checkout's root as a real path.
  return main === undefined ? [own] : [own, main]
}

function realPath(path) {
  try { return realpathSync(path) } catch { return undefined }
}

export function currentWorkspace(cwd) {
  return workspaces(cwd)[0]
}

// Binding and clearing write only this key.
export function workspaceKey(cwd) {
  return { integration: 'zcode', kind: 'workspace', external_id: sha256(currentWorkspace(cwd)) }
}

export function bindingKeys(input, settings) {
  if (settings.remoteWorkspace) return []
  const keys = []
  const sessionId = sessionIdentity(input)
  if (sessionId) keys.push({ integration: 'zcode', kind: 'session', external_id: sessionId })
  if (nonempty(input.cwd)) {
    for (const workspace of workspaces(input.cwd)) {
      keys.push({ integration: 'zcode', kind: 'workspace', external_id: sha256(workspace) })
    }
  }
  return keys
}

export async function resolveScope(input, settings, budgetSignal, keys = bindingKeys(input, settings)) {
  if (settings.remoteWorkspace && !settings.explicitScopeId) throw new Error('scope_unresolved')
  const result = await request(settings, 'POST', '/v1/scope-bindings/resolve', {
    explicit_scope_id: settings.explicitScopeId ?? null, binding_keys: keys,
  }, budgetSignal)
  const scopeId = result.body.scope_id
  if (result.status !== 200 || !isScopeId(scopeId)) throw new Error('scope_unresolved')
  return { scopeId, keys }
}
