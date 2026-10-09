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
import { loadSettings, nonempty } from '../shared/settings.mjs'
import { bindingKeys, resolveScope, sessionIdentity, sha256 } from '../shared/scope.mjs'
import { failureResult, stageResult, startObservation } from '../shared/observations.mjs'
import { beginCaptureTracking } from '../shared/pending.mjs'
import { failureCode, HOOK_BUDGET_MS, request } from '../shared/transport.mjs'

const SETTINGS = loadSettings()
const MAX_QUERY_CHARACTERS = 8_192
const MAX_SOURCE_CHARACTERS = 200_000
const SECRET_PATTERN = /(?:\b(?:api[_-]?key|access[_-]?token|token|authorization|password|secret|private[_-]?key)["']?\s*[:=]\s*\S+|\bbearer\s+\S+|\bsk-[A-Za-z0-9_-]{8,}|-----BEGIN [^-]*PRIVATE KEY-----)/iu

function diagnostic(stage, code) {
  process.stderr.write(`${JSON.stringify({ component: 'powercontext.zcode', stage, code })}\n`)
}

function emit(output) {
  return new Promise((resolve, reject) => {
    process.stdout.write(`${JSON.stringify(output)}\n`, error => error ? reject(error) : resolve())
  })
}

function boundedQuery(prompt) {
  const characters = Array.from(prompt.trim())
  let query = characters.slice(-MAX_QUERY_CHARACTERS).join('')
  const encoded = Buffer.from(query, 'utf8')
  if (encoded.length > MAX_QUERY_CHARACTERS) {
    query = encoded.subarray(-MAX_QUERY_CHARACTERS).toString('utf8').replace(/^\uFFFD/u, '')
  }
  return query.trim()
}

function sourceId(input, scopeId, prompt) {
  const sessionId = sessionIdentity(input) ?? ''
  const turnId = nonempty(input.turnId) ?? ''
  return `zcode-user-prompt:${sha256([scopeId, sessionId, turnId, prompt].join('\0'))}`
}

async function run(input) {
  const event = input.hookEventName ?? input.hook_event_name
  if (event !== 'UserPromptSubmit' || typeof input.prompt !== 'string' || !input.prompt.trim()) return
  const budgetSignal = AbortSignal.timeout(HOOK_BUDGET_MS)
  let observation
  const observe = async (name, result, scope) => {
    try { await observation?.stage(name, result, scope) } catch { diagnostic('runtime', 'runtime_state_unavailable') }
  }
  const finish = async () => {
    try { await observation?.finish() } catch { diagnostic('runtime', 'runtime_state_unavailable') }
  }
  let scopeId
  let keys
  try {
    keys = bindingKeys(input, SETTINGS)
    try { observation = await startObservation(input, SETTINGS, keys) }
    catch { diagnostic('runtime', process.env.ZCODE_PLUGIN_DATA ? 'runtime_state_unavailable' : 'runtime_data_unavailable') }
    await observe('scope', stageResult('running'))
    const resolved = await resolveScope(input, SETTINGS, budgetSignal, keys)
    scopeId = resolved.scopeId
    keys = resolved.keys
    await observe('scope', stageResult('resolved'), scopeId)
  } catch (error) {
    diagnostic('scope', failureCode(error))
    await observe('scope', failureResult(error))
    for (const stage of ['prepare', 'capture', 'context_output']) await observe(stage, stageResult('skipped', { reason: 'scope_unresolved' }))
    await finish()
    await emit({ hookSpecificOutput: { hookEventName: event, additionalContext: '' } })
    return
  }

  // Ordinary tool processes need not inherit Hook-only session variables. Keep the current
  // binding separate from recalled history so explicit MCP calls can reuse the exact identity.
  const binding = bindingContext(scopeId, keys)
  let context = ''
  const query = boundedQuery(input.prompt)
  if (query) {
    try {
      await observe('prepare', stageResult('running'))
      const result = await request(SETTINGS, 'POST', '/v1/context/prepare', {
        scope_id: scopeId, query, max_bytes: MAX_CONTEXT_BYTES,
      }, budgetSignal)
      if (result.status !== 200) throw new Error('invalid_status')
      const prepared = validatePrepared(result.body)
      await observe('prepare', stageResult(prepared ? 'ready' : 'empty', { content_bytes: result.body.content_bytes }))
      if (prepared) context = `${CONTEXT_PREFIX}${prepared}`
    } catch (error) {
      diagnostic('prepare', failureCode(error))
      await observe('prepare', failureResult(error))
    }
  }

  if (SETTINGS.capturePrompts && input.prompt.length <= MAX_SOURCE_CHARACTERS &&
      !SECRET_PATTERN.test(input.prompt)) {
    let tracking, trackingIncomplete = false
    try {
      await observe('capture', stageResult('running'))
      try { tracking = await beginCaptureTracking(input, SETTINGS, keys, scopeId) }
      catch { trackingIncomplete = true; diagnostic('pending', 'pending_tracking_incomplete') }
      const id = sourceId(input, scopeId, input.prompt)
      const result = await request(SETTINGS, 'POST', '/v1/sources/content', {
        scope_id: scopeId,
        source_id: id,
        content: input.prompt,
        metadata: {
          origin: 'zcode', event: 'user_prompt_submit',
          ...(nonempty(input.sessionId) ?? nonempty(input.session_id)
            ? { session_id: nonempty(input.sessionId) ?? nonempty(input.session_id) } : {}),
          ...(nonempty(input.turnId) ? { turn_id: nonempty(input.turnId) } : {}),
        },
      }, budgetSignal)
      if (result.status !== 202 || result.body.status !== 'accepted' ||
          result.body.source?.name !== 'content' || result.body.source?.source_id !== id || !Number.isSafeInteger(result.body.position) ||
          result.body.position < 1) {
        throw Object.assign(new Error('invalid_receipt'), { requestSent: true, httpStatus: result.status })
      }
      try { await tracking?.accepted(result.body.position) }
      catch { trackingIncomplete = true; diagnostic('pending', 'pending_tracking_incomplete') }
      await observe('capture', stageResult('accepted', { source_position: result.body.position, http_status: result.status,
        ...(trackingIncomplete ? { reason: 'pending_tracking_incomplete' } : {}) }))
    } catch (error) {
      if (!error.requestSent || error.httpStatus >= 400 && error.httpStatus < 500) {
        try { await tracking?.rejected() } catch { diagnostic('pending', 'pending_tracking_incomplete') }
      }
      diagnostic('capture', failureCode(error))
      await observe('capture', failureResult(error, true))
    }
  } else {
    await observe('capture', stageResult('skipped', { reason: !SETTINGS.capturePrompts ? 'capture_disabled' :
      input.prompt.length > MAX_SOURCE_CHARACTERS ? 'source_too_long' : 'sensitive_content' }))
  }

  await observe('flush', stageResult('skipped', { reason: 'awaiting_stop' }))
  try {
    await emit({ hookSpecificOutput: { hookEventName: event, additionalContext: binding + context } })
    await observe('context_output', stageResult('emitted', { content_bytes: Buffer.byteLength(binding + context, 'utf8') }))
  } catch {
    await observe('context_output', stageResult('failed', { code: 'output_unavailable' }))
  }
  await finish()
}

async function main() {
  let raw = ''
  try {
    const decoder = new TextDecoder('utf-8', { fatal: true })
    for await (const chunk of process.stdin) {
      raw += decoder.decode(chunk, { stream: true })
      if (raw.length > MAX_SOURCE_CHARACTERS + 20_000) return
    }
    raw += decoder.decode()
    const input = JSON.parse(raw)
    if (!input || typeof input !== 'object' || Array.isArray(input)) return
    await run(input)
  } catch {
    diagnostic('hook', 'invalid_input')
  }
}

await main()
