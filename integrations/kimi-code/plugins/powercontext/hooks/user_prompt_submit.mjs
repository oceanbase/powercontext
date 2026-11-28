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

import { randomUUID } from 'node:crypto'
import { fileURLToPath } from 'node:url'
import { authorization, diagnostic, Failure, loadSettings, MAX_CONTEXT_BYTES, preparedText, request, resolveScope } from '../shared/runtime.mjs'
import { startObservation } from '../shared/observations.mjs'
import { scrubSecrets } from '../shared/prompt-privacy.mjs'
import { boundedRecallQuery } from '../shared/recall-query.mjs'

const MAX_SOURCE_CHARACTERS = 200000
const HOOK_DEADLINE_MS = 4000
// Requests stop early enough for the Hook to write its output and runtime history before the hard exit.
const signal = AbortSignal.timeout(HOOK_DEADLINE_MS - 300)
let emitted = false
let observation
// Output becomes final once preparation settles; a later capture cannot withdraw it.
let prepared = ''
function emit(message = '') {
  if (emitted) return
  emitted = true
  // Kimi adds any non-empty stdout, even `{}`, to the conversation as a hook result; write nothing without a message.
  if (message) process.stdout.write(JSON.stringify({ message }) + '\n')
}
const deadline = setTimeout(() => {
  diagnostic(emitted ? 'observation' : 'hook', new Failure(emitted ? 'runtime_unavailable' : 'server_unavailable'))
  emit(prepared)
  process.exit(0)
}, HOOK_DEADLINE_MS)

function promptText(value) {
  if (typeof value === 'string') return value
  if (!Array.isArray(value)) return ''
  return value.filter(part => part?.type === 'text' && typeof part.text === 'string').map(part => part.text).join('\n')
}

async function run(input) {
  if (input?.hook_event_name !== 'UserPromptSubmit') return
  const prompt = promptText(input.prompt)
  if (!prompt.trim()) return
  const settings = loadSettings({ credentials: false })
  try {
    observation = startObservation(input, settings, () => diagnostic('observation', new Failure('runtime_unavailable')))
  } catch { diagnostic('observation', new Failure('runtime_unavailable')) }
  let scopeId
  try {
    settings.authorization = authorization(settings.useToken)
    scopeId = await resolveScope(settings, signal, input)
    observation?.record('scope', 'resolved', { scope_id: scopeId })
  } catch (error) {
    observation?.fail('scope', error)
    throw error
  }
  let context = ''
  const safePrompt = scrubSecrets(prompt, settings.authorization)
  try {
    const { value } = await request(settings, 'POST', '/v1/context/prepare', {
      scope_id: scopeId, query: boundedRecallQuery(safePrompt), max_bytes: MAX_CONTEXT_BYTES,
    }, signal)
    context = preparedText(value)
    observation?.record('prepare', context ? 'ready' : 'empty', { content_bytes: value.content_bytes })
  } catch (error) { observation?.fail('prepare', error); diagnostic('context_prepare', error) }
  const binding = 'PowerContext current-request binding metadata:\n' +
    JSON.stringify({ schema: 'powercontext.kimi-code.request-binding.v1', scope_id: scopeId,
      cwd: input.cwd, scope_script: fileURLToPath(new URL('../scripts/scope.mjs', import.meta.url)) }) + '\n\n'
  prepared = binding + context
  if (settings.capturePrompts && prompt.length <= MAX_SOURCE_CHARACTERS && safePrompt === prompt && !signal.aborted) {
    const sourceId = 'kimi-user-prompt:' + randomUUID()
    try {
      const { status, value } = await request(settings, 'POST', '/v1/sources/content', {
        scope_id: scopeId, source_id: sourceId, content: prompt,
        metadata: { origin: 'kimi-code', event: 'user_prompt_submit',
          ...(typeof input.session_id === 'string' && input.session_id.trim() &&
            input.session_id.length <= 256 ? { session_id: input.session_id } : {}) },
      }, signal)
      if (status !== 202 || value.status !== 'accepted' || value.source?.name !== 'content' ||
          value.source?.source_id !== sourceId || !Number.isSafeInteger(value.position) || value.position < 1) {
        throw new Failure('invalid_response', status, true)
      }
      observation?.record('capture', 'accepted', { source_position: value.position })
    } catch (error) { observation?.fail('capture', error, true); diagnostic('capture_source', error) }
  } else {
    observation?.record('capture', 'skipped', { reason: !settings.capturePrompts ? 'disabled' :
      prompt.length > MAX_SOURCE_CHARACTERS ? 'too_large' : signal.aborted ? 'deadline_exceeded' : 'sensitive' })
  }
  emit(prepared)
  observation?.record('injection', context ? 'emitted' : 'skipped', context ? { content_bytes: Buffer.byteLength(context) } : { reason: 'no_context' })
}

try {
  const chunks = []
  let size = 0
  for await (const chunk of process.stdin) {
    size += chunk.length
    if (size > 1048576) throw new Failure('invalid_input')
    chunks.push(chunk)
  }
  const input = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)))
  await run(input)
} catch (error) { diagnostic('hook', error) }
finally {
  emit()
  await observation?.finish()
  clearTimeout(deadline)
}
