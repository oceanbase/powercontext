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
import { queryObservations } from '../shared/observations.mjs'
import { queryPending } from '../shared/pending.mjs'
import { sessionIdentity } from '../shared/scope.mjs'
import { loadSettings } from '../shared/settings.mjs'

try {
  const { values } = parseArgs({ options: {
    cwd: { type: 'string' }, 'session-id': { type: 'string' }, 'data-dir': { type: 'string' }, latest: { type: 'boolean' },
  } })
  if (!values.cwd || !isAbsolute(values.cwd) || values.latest && values['session-id']) throw new Error('invalid_arguments')
  const input = { cwd: values.cwd, sessionId: values['session-id'] }
  if (!values.latest && !sessionIdentity(input)) throw new Error('session_required')
  const settings = loadSettings(), dataDir = values['data-dir'] ?? process.env.ZCODE_PLUGIN_DATA
  const result = await queryObservations(input, settings, dataDir, values.latest)
  try { result.pending = await queryPending(input, settings, dataDir, values.latest) }
  catch (error) {
    result.pending = { status: 'unavailable', code: ['invalid_pending', 'unsupported_pending', 'pending_capacity_exceeded']
      .includes(error.message) ? error.message : 'pending_state_unavailable' }
    process.exitCode = 1
  }
  console.log(JSON.stringify(result))
  if (result.issues.length) process.exitCode = 1
} catch (error) {
  const code = ['runtime_data_unavailable', 'runtime_capacity_exceeded', 'session_required', 'session_conflict', 'invalid_session',
    'invalid_server_url', 'invalid_arguments'].includes(error.message) ? error.message : 'runtime_state_unavailable'
  console.log(JSON.stringify({ schema: 'powercontext.zcode.runtime-status.v1', status: 'failed', code }))
  process.exitCode = 1
}
