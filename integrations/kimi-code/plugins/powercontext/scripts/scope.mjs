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

import { parseArgs } from 'node:util'
import { Failure, isScopeId, loadSettings, request, resolveScope, workspaceKey } from '../shared/runtime.mjs'

const schema = 'powercontext.kimi-code.scope-result.v1'
let binding
try {
  let args
  try { args = parseArgs({ allowPositionals: true, options: {
    cwd: { type: 'string' }, 'scope-id': { type: 'string' },
  } }) } catch { throw new Failure('invalid_arguments') }
  const [action] = args.positionals
  const target = args.values['scope-id']
  if (args.positionals.length !== 1 || !['resolve', 'bind', 'unbind'].includes(action) ||
      (action === 'bind' ? !isScopeId(target) : target !== undefined)) throw new Failure('invalid_arguments')
  const input = { cwd: args.values.cwd }
  const key = workspaceKey(input.cwd)
  const settings = loadSettings()
  const signal = AbortSignal.timeout(4000)
  if (action !== 'resolve') {
    binding = { action, status: 'unknown' }
    let response
    try {
      response = await request(settings, action === 'bind' ? 'PUT' : 'POST',
        action === 'bind' ? '/v1/scope-bindings' : '/v1/scope-bindings/clear',
        { key, ...(action === 'bind' ? { scope_id: target } : {}) }, signal)
    } catch (error) {
      if (error.httpStatus >= 400 && error.httpStatus < 500) binding.status = 'rejected'
      else if (error instanceof Failure && !error.requestSent) binding.status = 'not_sent'
      throw error
    }
    const { status, value } = response
    if (status !== 200 || (action === 'bind' ? value.scope_id !== target || !value.key ||
        Object.entries(key).some(([name, expected]) => value.key[name] !== expected) :
        typeof value.cleared !== 'boolean')) throw new Failure('binding_not_confirmed')
    binding = action === 'bind' ? { action, status: 'saved', scope_id: target } :
      { action, status: value.cleared ? 'cleared' : 'absent', cleared: value.cleared }
  }
  const scopeId = await resolveScope(settings, signal, input)
  console.log(JSON.stringify({ schema, status: 'resolved', scope_id: scopeId,
    selection: settings.scopeId ? 'explicit' : 'workspace_or_default',
    ...(binding ? { binding, ...(action === 'bind' ? { bound_scope_is_current: scopeId === target } : {}) } : {}) }))
} catch (error) {
  console.log(JSON.stringify({ schema, status: 'failed',
    code: error instanceof Failure ? error.code : 'invalid_response', ...(binding ? { binding } : {}) }))
  process.exitCode = 1
}
