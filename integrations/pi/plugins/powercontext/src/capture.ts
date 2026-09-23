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
import { flushThrough as flushCheckpoint, sourcePosition } from './checkpoints.ts'
import { writeFailureConfirmation } from './errors.ts'
import { containsSecret } from './secrets.ts'

export { containsSecret }

export const MAX_SOURCE_BYTES = 200_000

export interface CaptureInput {
  client: PowerContextClient
  config: ResolvedConfig
  scopeId: string
  prompt: string
  cwd: string
  sessionId: string
  turnId: string
  signal?: AbortSignal
  onFlushFailure?: (position: number) => void
  onFailure?: (event: string, error: unknown) => void
}

export function buildSourceId(scopeId: string, sessionId: string, turnId: string, prompt: string): string {
  const identity = [scopeId, sessionId, turnId, prompt].join('\0')
  return `pi-user-prompt:${createHash('sha256').update(identity).digest('hex')}`
}


async function flushThrough(input: CaptureInput, position: number): Promise<'complete' | 'pending' | 'unknown'> {
  try {
    return await flushCheckpoint(input.client, input.scopeId, position, input.config.flushMaxCalls, input.signal)
      ? 'complete' : 'pending'
  } catch (error) {
    try { input.onFailure?.('flush_memory', error) } catch { /* Diagnostics cannot affect the turn. */ }
    return writeFailureConfirmation(error) === 'unconfirmed' ? 'unknown' : 'pending'
  }
}

export async function captureUserPrompt(input: CaptureInput): Promise<number | undefined> {
  if (
    !input.config.capturePrompts
    || Buffer.byteLength(input.prompt, 'utf8') > MAX_SOURCE_BYTES
    || containsSecret(input.prompt)
  ) {
    return undefined
  }
  try {
    const result = await input.client.request('capture_content_source', {
      scope_id: input.scopeId,
      source_id: buildSourceId(input.scopeId, input.sessionId, input.turnId, input.prompt),
      content: input.prompt,
      metadata: {
        origin: 'pi',
        event: 'user_prompt_submit',
        cwd: input.cwd,
        session_id: input.sessionId,
        turn_id: input.turnId,
      },
    }, input.signal)
    const position = result.kind === 'json' ? sourcePosition(result.value) : undefined
    if (input.config.flushOnCapture && position !== undefined && await flushThrough(input, position) === 'pending') {
      try {
        input.onFlushFailure?.(position)
      } catch {
        // Pending-source bookkeeping is auxiliary; it must not affect the turn.
      }
    }
    return position
  } catch (error) {
    // Source persistence is auxiliary; it must not delay or break the Pi turn.
    try {
      input.onFailure?.('capture_source', error)
    } catch {
      // Diagnostics are best effort and must not affect the turn.
    }
    return undefined
  }
}
