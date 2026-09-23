import { flushThrough, sourcePosition } from './checkpoints.ts'
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

import { createHash } from 'node:crypto'
import type { PowerContextClient } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { logSafely, reportFailure } from './diagnostics.ts'
import { authenticationRejection, MAX_SOURCE_LENGTH, RequestNotSentError } from './errors.ts'
import { containsSecret } from './secrets.ts'
import { cancellationReason, type StatusAttempt } from './status.ts'

export interface CaptureInput {
  client: PowerContextClient
  config: ResolvedConfig
  scopeId: string
  prompt: string
  cwd?: string
  sessionId: string
  turnId: string
  signal?: AbortSignal
  log: (event: Record<string, unknown>) => void
  observation?: StatusAttempt
}

export function buildSourceId(scopeId: string, sessionId: string, turnId: string, prompt: string): string {
  const identity = [scopeId, sessionId, turnId, prompt].join('\0')
  return `dsh-user-prompt:${createHash('sha256').update(identity).digest('hex')}`
}



export async function captureUserPrompt(input: CaptureInput): Promise<void> {
  const observation = input.observation
  observation?.skip('flush', 'capture_skipped')
  if (!input.config.capturePrompts) {
    observation?.skip('capture', 'capture_disabled')
    return
  }
  if (input.prompt.length > MAX_SOURCE_LENGTH || containsSecret(input.prompt)) {
    observation?.skip('capture', input.prompt.length > MAX_SOURCE_LENGTH ? 'source_too_long' : 'sensitive_content')
    logSafely(input.log, { event: 'capture_content_source', outcome: 'skipped' })
    return
  }
  let position: number | undefined
  let captureStatus = 202
  if (input.signal?.aborted) {
    observation?.skip('capture', cancellationReason(input.signal))
    return
  }
  observation?.record('capture', { state: 'running' })
  try {
    if (input.signal?.aborted) throw new RequestNotSentError('', input.signal.reason)
    const result = await input.client.request('capture_content_source', {
      scope_id: input.scopeId,
      source_id: buildSourceId(input.scopeId, input.sessionId, input.turnId, input.prompt),
      content: input.prompt,
      metadata: {
        origin: 'dsh',
        event: 'user_prompt_submit',
        ...input.cwd ? { cwd: input.cwd } : {},
        session_id: input.sessionId,
        turn_id: input.turnId,
      },
    }, input.signal)
    position = result.kind === 'json' ? sourcePosition(result.value) : undefined
    captureStatus = result.status
  } catch (error) {
    observation?.fail('capture', error, true, input.signal)
    observation?.skip('flush', authenticationRejection(error) ? 'capture_rejected' : 'capture_not_confirmed')
    reportFailure(input.log, 'capture_content_source', error)
    return
  }

  logSafely(input.log, { event: 'capture_content_source', outcome: 'ok', status: captureStatus })
  observation?.record('capture', { state: 'accepted', http_status: captureStatus })
  observation?.skip('flush', input.config.flushOnCapture ? 'source_position_missing' : 'flush_disabled')
  if (input.config.flushOnCapture && position !== undefined) {
    if (input.signal?.aborted) {
      observation?.skip('flush', cancellationReason(input.signal))
      return
    }
    observation?.record('flush', { state: 'running' })
    try {
      const reached = await flushThrough(input.client, input.scopeId, position, input.config.flushMaxCalls, input.signal)
      observation?.record('flush', reached
        ? { state: 'completed', code: 'cursor_reached', message: 'The processing cursor reached this Source position; Memory production is not verified.' }
        : { state: 'incomplete', code: 'flush_budget_exhausted', message: 'The bounded flush calls ended without observing the cursor reach this Source position.' })
    } catch (error) {
      observation?.fail('flush', error, true, input.signal)
      reportFailure(input.log, 'flush_memory', error)
    }
  }
}
