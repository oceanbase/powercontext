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
import { authorizeUnknownFlushRetry } from '../shared/pending.mjs'
import { bindingKeys, resolveScope } from '../shared/scope.mjs'
import { loadSettings } from '../shared/settings.mjs'
import { failureCode, HOOK_BUDGET_MS } from '../shared/transport.mjs'

try {
  const { values, positionals } = parseArgs({ allowPositionals: true, options: {
    cwd: { type: 'string' }, 'session-id': { type: 'string' }, 'scope-id': { type: 'string' },
    'data-dir': { type: 'string' }, 'accept-unknown-outcome': { type: 'boolean' },
  } })
  if (positionals.length !== 1 || positionals[0] !== 'resume-flush' || !values['accept-unknown-outcome'] ||
      !values.cwd || !isAbsolute(values.cwd) || !values['scope-id']) throw new Error('invalid_arguments')
  const input = { cwd: values.cwd, sessionId: values['session-id'] }
  const settings = loadSettings(), keys = bindingKeys(input, settings)
  const scope = await resolveScope(input, settings, AbortSignal.timeout(HOOK_BUDGET_MS), keys)
  if (scope.scopeId !== values['scope-id']) throw new Error('scope_changed')
  const status = await authorizeUnknownFlushRetry(input, settings, keys, scope.scopeId, values['data-dir'] ?? process.env.ZCODE_PLUGIN_DATA)
  console.log(JSON.stringify({ schema: 'powercontext.zcode.pending-control.v1', status, scope_id: scope.scopeId }))
} catch (error) {
  const code = ['session_required', 'scope_changed', 'claim_busy', 'invalid_pending', 'unsupported_pending',
    'pending_capacity_exceeded', 'runtime_data_unavailable'].includes(error.message) ? error.message : failureCode(error)
  console.log(JSON.stringify({ schema: 'powercontext.zcode.pending-control.v1', status: 'failed', code }))
  process.exitCode = 1
}
