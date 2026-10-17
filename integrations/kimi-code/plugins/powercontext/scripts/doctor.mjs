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

import { Failure, loadSettings, request, resolveScope } from '../shared/runtime.mjs'
import { parseArgs } from 'node:util'
import { queryObservations } from '../shared/observations.mjs'

const { values } = parseArgs({ options: { cwd: { type: 'string' }, 'session-id': { type: 'string' },
  runtime: { type: 'boolean', default: false } } })
const input = { cwd: values.cwd ?? process.cwd(), session_id: values['session-id'] }
const checks = {}
let settings
function result(error) {
  const failure = error instanceof Failure ? error : new Failure('invalid_response')
  return { status: 'failed', detail: failure.code }
}
try { settings = loadSettings() }
catch (error) { checks[['authentication_failed', 'invalid_credentials'].includes(error.code) ? 'credentials' : 'settings'] = result(error) }
if (settings) {
  checks.settings = { status: 'ok', detail: 'Installed endpoint, Scope and runtime credentials are valid.' }
  try {
    const { value } = await request(settings, 'GET', '/health/ready', undefined, AbortSignal.timeout(1500))
    checks.server = ['ready', 'degraded'].includes(value.status) ? { status: 'ok', detail: value.status === 'ready' ? 'Server is ready.' : 'Server is available; optional capabilities are degraded.' } :
      { status: 'failed', detail: 'Server is not ready.' }
  } catch (error) { checks.server = result(error) }
  try {
    const scopeId = await resolveScope(settings, AbortSignal.timeout(1500), input)
    checks.scope = { status: 'ok', detail: 'Current Scope is accessible.',
      checks: { scope_id: scopeId, selection: settings.scopeId ? 'explicit' : 'workspace_or_default' } }
  } catch (error) { checks.scope = result(error) }
}
let runtime
if (values.runtime) {
  try {
    runtime = await queryObservations(input, loadSettings({ credentials: false }))
  } catch (error) {
    runtime = { status: 'failed', code: ['invalid_state', 'runtime_capacity_exceeded', 'invalid_settings', 'invalid_session']
      .includes(error.code ?? error.message) ? error.code ?? error.message : 'runtime_unavailable' }
  }
}
process.stdout.write(JSON.stringify({ schema: 'powercontext.kimi-code.diagnostics.v1', checks, ...(runtime ? { runtime } : {}) }) + '\n')
