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
import { queryObservations } from '../shared/observations.mjs'
import { resolveScope } from '../shared/scope.mjs'
import { loadSettings } from '../shared/settings.mjs'
import { failureCode, HOOK_BUDGET_MS, request } from '../shared/transport.mjs'

const checks = {}
try {
  const { values } = parseArgs({ options: { 'data-dir': { type: 'string' }, prepare: { type: 'boolean' } } })
  const settings = loadSettings()
  const input = { cwd: process.cwd() }
  const signal = AbortSignal.timeout(HOOK_BUDGET_MS)
  for (const [name, path] of [['server', '/health/ready'], ['protected_api', '/v1/capabilities']]) {
    try {
      const response = await request(settings, 'GET', path, undefined, signal)
      checks[name] = { status: response.status === 200 ? 'ok' : 'failed', code: response.status === 200 ? name === 'server' ? 'ready' : 'protected_api_reachable' : 'invalid_response' }
    } catch (error) { checks[name] = { status: 'failed', code: failureCode(error) } }
  }
  let scope
  try {
    scope = await resolveScope(input, settings, signal)
    checks.scope_probe = { status: 'ok', code: 'resolved', scope_id: scope.scopeId,
      session_key_used: scope.keys.some(key => key.kind === 'session') }
  } catch (error) { checks.scope_probe = { status: 'failed', code: failureCode(error) } }
  if (values.prepare && scope) {
    try {
      const response = await request(settings, 'POST', '/v1/context/prepare', {
        scope_id: scope.scopeId, query: 'PowerContext read-only connectivity diagnostic', max_bytes: 8000,
      }, signal)
      const body = response.body
      if (response.status !== 200 || body.schema !== 'powercontext.prepared-context.v1' ||
          !(body.status === 'empty' && body.content === null && body.content_bytes === 0 || body.status === 'ready' &&
            typeof body.content === 'string' && body.content_bytes === Buffer.byteLength(body.content, 'utf8') && body.content_bytes <= 8000)) throw new Error('invalid_prepared')
      checks.prepare_probe = { status: 'ok', code: body.status }
    } catch (error) { checks.prepare_probe = { status: 'failed', code: failureCode(error) } }
  }
  const dataDir = values['data-dir'] ?? process.env.ZCODE_PLUGIN_DATA
  if (dataDir) {
    try {
      const report = await queryObservations(input, settings, dataDir, !process.env.ZCODE_SESSION_ID)
      const observation = report.observation
      checks.runtime = { status: report.issues.length || report.status === 'configuration_mismatch' ? 'failed' :
        report.status === 'not_observed' || report.stale || report.incomplete ? 'skipped' : 'ok',
        code: report.issues.length ? 'invalid_state' : report.stale ? 'stale_observation' : report.incomplete ? 'incomplete_observation' : report.status,
        ...(observation ? { event: observation.event, observed_at: observation.started_at,
          scope_id: observation.scope_id, selection: report.selection,
          stages: Object.fromEntries(Object.entries(observation.stages).map(([name, result]) => [name,
            [result.state, result.code, result.reason].filter(Boolean).join(':')])) } : {}),
      }
    } catch { checks.runtime = { status: 'failed', code: 'runtime_state_unavailable' } }
  } else checks.runtime = { status: 'skipped', code: 'not_observed' }
  checks.mcp_session = { status: 'skipped', code: 'not_observed' }
} catch { checks.probe = { status: 'failed', code: 'invalid_arguments' } }
console.log(JSON.stringify({ schema: 'powercontext.zcode.diagnostics.v1', checks }))
if (Object.values(checks).some(check => check.status === 'failed')) process.exitCode = 1
