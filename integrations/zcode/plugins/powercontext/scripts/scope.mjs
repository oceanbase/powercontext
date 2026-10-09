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

import { isAbsolute } from 'node:path'
import { parseArgs } from 'node:util'
import { loadSettings, nonempty } from '../shared/settings.mjs'
import { isScopeId, resolveScope, sessionIdentity, workspaceKey } from '../shared/scope.mjs'
import { failureCode, HOOK_BUDGET_MS, request, serverOrigin } from '../shared/transport.mjs'

const schema = 'powercontext.zcode.scope-result.v1'
let binding = null
try {
  let args
  try {
    args = parseArgs({ allowPositionals: true, options: {
      cwd: { type: 'string' }, 'session-id': { type: 'string' }, 'scope-id': { type: 'string' },
    } })
  } catch { throw new Error('invalid_arguments') }
  const action = args.positionals[0]
  const cwd = nonempty(args.values.cwd)
  if (args.positionals.length !== 1 || !['resolve', 'bind', 'unbind'].includes(action) || !cwd || !isAbsolute(cwd) ||
      action !== 'bind' && args.values['scope-id'] !== undefined || action === 'bind' && !isScopeId(args.values['scope-id'])) {
    throw new Error('invalid_arguments')
  }
  const input = { cwd, sessionId: args.values['session-id'] }
  sessionIdentity(input)
  const settings = loadSettings()
  serverOrigin(settings)
  const signal = AbortSignal.timeout(HOOK_BUDGET_MS)
  if (action !== 'resolve') {
    if (settings.remoteWorkspace) throw new Error('remote_binding_disabled')
    const key = workspaceKey(cwd)
    const target = args.values['scope-id']
    binding = { action, status: 'unknown' }
    let response
    try {
      response = await request(settings, action === 'bind' ? 'PUT' : 'POST',
        action === 'bind' ? '/v1/scope-bindings' : '/v1/scope-bindings/clear', {
          key, ...(action === 'bind' ? { scope_id: target } : {}),
        }, signal)
    } catch (error) {
      if (/^http_4\d\d$/u.test(error?.message ?? '')) binding.status = 'rejected'
      throw error
    }
    if (response.status !== 200 || action === 'bind' && (response.body.scope_id !== target ||
        !response.body.key || Object.entries(key).some(([name, value]) => response.body.key[name] !== value)) ||
        action === 'unbind' && typeof response.body.cleared !== 'boolean') {
      throw new Error('binding_not_confirmed')
    }
    binding = action === 'bind' ? { action, status: 'saved', scope_id: response.body.scope_id } : {
      action, status: response.body.cleared ? 'cleared' : 'absent', cleared: response.body.cleared,
    }
  }
  const { scopeId, keys } = await resolveScope(input, settings, signal)
  console.log(JSON.stringify({ schema, status: 'resolved', scope_id: scopeId,
    session_key_used: keys.some(key => key.kind === 'session'), workspace_key_used: keys.some(key => key.kind === 'workspace'),
    ...(binding ? { binding, ...(binding.action === 'bind' ? { bound_scope_is_current: scopeId === binding.scope_id,
      ...(scopeId !== binding.scope_id ? { resolution_note: settings.explicitScopeId ? 'explicit_scope_override' : 'binding_not_current' } : {}),
    } : {}) } : {}),
  }))
} catch (error) {
  console.log(JSON.stringify({ schema, status: 'failed', code: failureCode(error), ...(binding ? { binding } : {}) }))
  process.exitCode = 1
}
