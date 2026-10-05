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

import { stageResult, startObservation } from '../shared/observations.mjs'
import { flushBoundary } from '../shared/pending.mjs'
import { bindingKeys, resolveScope } from '../shared/scope.mjs'
import { loadSettings } from '../shared/settings.mjs'
import { failureCode } from '../shared/transport.mjs'

function diagnostic(code) {
  process.stderr.write(`${JSON.stringify({ component: 'powercontext.zcode', stage: 'flush', code })}\n`)
}

async function stop(input, signal) {
  if ((input.hookEventName ?? input.hook_event_name) !== 'Stop') return
  const settings = loadSettings()
  let observation
  const observe = async (stage, result, scopeId) => {
    try { await observation?.stage(stage, result, scopeId) } catch { diagnostic('runtime_state_unavailable') }
  }
  try {
    const keys = bindingKeys(input, settings)
    try { observation = await startObservation(input, settings, keys) }
    catch { diagnostic('runtime_state_unavailable') }
    for (const name of ['prepare', 'capture', 'context_output']) {
      await observe(name, stageResult('skipped', { reason: 'stop_boundary' }))
    }
    await observe('scope', stageResult('skipped', { reason: 'not_requested' }))
    const resolveCurrent = async () => {
      try {
        await observe('scope', stageResult('running'))
        const scope = await resolveScope(input, settings, signal, keys)
        await observe('scope', stageResult('resolved'), scope.scopeId)
        return scope
      } catch (error) {
        await observe('scope', stageResult('failed', { code: failureCode(error) }))
        throw error
      }
    }
    const result = input.stopHookActive === true || input.stop_hook_active === true
      ? stageResult('skipped', { reason: 'stop_reentry' })
      : await flushBoundary(input, settings, keys, resolveCurrent, signal)
    await observe('flush', result)
    if (result.code || result.state === 'unknown') diagnostic(result.code ?? result.reason)
  } catch (error) {
    const code = ['runtime_data_unavailable', 'pending_capacity_exceeded', 'invalid_pending', 'unsupported_pending']
      .includes(error.message) ? error.message : failureCode(error)
    diagnostic(code)
    await observe('flush', stageResult('skipped', { reason: code }))
  }
  try { await observation?.finish() } catch { diagnostic('runtime_state_unavailable') }
}

async function main() {
  const signal = AbortSignal.timeout(800)
  let outputSent = false
  const deadline = setTimeout(() => {
    diagnostic('deadline_exceeded')
    if (!outputSent) process.stdout.write('{}\n')
    process.exit(0)
  }, 1000)
  try {
    const chunks = []
    let size = 0
    for await (const chunk of process.stdin) {
      size += chunk.length
      if (size > 240_000) { diagnostic('input_too_large'); return }
      chunks.push(chunk)
    }
    const input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)))
    if (!input || typeof input !== 'object' || Array.isArray(input)) throw new Error('invalid_input')
    await stop(input, signal)
  } catch { diagnostic('invalid_input') }
  finally {
    // A Stop Hook never blocks or continues the model turn.
    outputSent = true
    await new Promise(resolve => process.stdout.write('{}\n', resolve))
    clearTimeout(deadline)
  }
}
await main()
