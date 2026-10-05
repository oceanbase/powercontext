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

import { bindingContext, CONTEXT_PREFIX, MAX_CONTEXT_BYTES, validatePrepared } from '../shared/context.mjs'
import { failureResult, stageResult, startObservation } from '../shared/observations.mjs'
import { bindingKeys, resolveScope } from '../shared/scope.mjs'
import { loadSettings } from '../shared/settings.mjs'
import { failureCode, HOOK_BUDGET_MS, request } from '../shared/transport.mjs'

const GENERIC_QUERY = 'Current project decisions, constraints, and outstanding work'

function diagnostic(stage, code) {
  process.stderr.write(`${JSON.stringify({ component: 'powercontext.zcode', stage, code })}\n`)
}

async function sessionStart(input, signal) {
  if ((input.hookEventName ?? input.hook_event_name) !== 'SessionStart' ||
      !['startup', 'clear', 'compact', 'resume'].includes(input.source)) return
  const settings = loadSettings()
  let observation, scope, keys
  const observe = async (name, result, scopeId) => {
    try { await observation?.stage(name, result, scopeId) }
    catch { diagnostic('runtime', 'runtime_state_unavailable') }
  }
  try {
    keys = bindingKeys(input, settings)
    try { observation = await startObservation(input, settings, keys) }
    catch { diagnostic('runtime', process.env.ZCODE_PLUGIN_DATA ? 'runtime_state_unavailable' : 'runtime_data_unavailable') }
    await observe('scope', stageResult('running'))
    scope = await resolveScope(input, settings, signal, keys)
    await observe('scope', stageResult('resolved'), scope.scopeId)
  } catch (error) {
    diagnostic('scope', failureCode(error))
    await observe('scope', failureResult(error))
  }
  let context = ''
  if (scope && ['compact', 'resume'].includes(input.source)) {
    try {
      await observe('prepare', stageResult('running', { query_source: 'lifecycle_generic' }))
      const response = await request(settings, 'POST', '/v1/context/prepare', {
        scope_id: scope.scopeId, query: GENERIC_QUERY, max_bytes: MAX_CONTEXT_BYTES,
      }, signal)
      if (response.status !== 200) throw new Error('invalid_status')
      const prepared = validatePrepared(response.body)
      await observe('prepare', stageResult(prepared ? 'ready' : 'empty', {
        query_source: 'lifecycle_generic', content_bytes: response.body.content_bytes,
      }))
      if (prepared) context = bindingContext(scope.scopeId, scope.keys) + CONTEXT_PREFIX + prepared
    } catch (error) {
      diagnostic('prepare', failureCode(error))
      await observe('prepare', failureResult(error))
    }
  } else await observe('prepare', stageResult('skipped', { reason: scope ? 'prompt_recall_follows' : 'scope_unresolved' }))
  for (const name of ['capture', 'flush']) await observe(name, stageResult('skipped', { reason: 'lifecycle_readonly' }))
  try {
    await new Promise((resolve, reject) => {
      process.stdout.write(`${JSON.stringify({ hookSpecificOutput: { hookEventName: 'SessionStart', additionalContext: context } })}\n`,
        error => error ? reject(error) : resolve())
    })
    await observe('context_output', stageResult(context ? 'emitted' : 'empty', { content_bytes: Buffer.byteLength(context, 'utf8') }))
  } catch { await observe('context_output', stageResult('failed', { code: 'output_unavailable' })) }
  try { await observation?.finish() } catch { diagnostic('runtime', 'runtime_state_unavailable') }
}

async function main() {
  const signal = AbortSignal.timeout(HOOK_BUDGET_MS)
  try {
    const chunks = []
    let size = 0
    for await (const chunk of process.stdin) {
      size += chunk.length
      if (size > 240_000) { diagnostic('hook', 'input_too_large'); return }
      chunks.push(chunk)
    }
    const input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)))
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('invalid_input')
    await sessionStart(input, signal)
  } catch { diagnostic('hook', 'invalid_input') }
}
await main()
