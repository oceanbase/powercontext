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

function sourcePosition(value: unknown): number | undefined {
  if (!value || typeof value !== 'object') return undefined
  const position = (value as { position?: unknown }).position
  if (typeof position !== 'number' || !Number.isInteger(position) || position < 1) return undefined
  return position
}

async function flushThrough(input: CaptureInput, position: number): Promise<boolean> {
  for (let index = 0; index < input.config.flushMaxCalls; index += 1) {
    try {
      const result = await input.client.request('flush_memory', { scope_id: input.scopeId }, input.signal)
      const cursor = result.kind === 'json' && result.value && typeof result.value === 'object'
        ? (result.value as { current_cursor?: unknown }).current_cursor
        : undefined
      if (typeof cursor === 'number' && cursor >= position) return true
    } catch (error) {
      // A transient flush failure should not discard the captured position.
      try {
        input.onFailure?.('flush_memory', error)
      } catch {
        // Diagnostics are best effort and must not affect the turn.
      }
    }
  }
  return false
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
    if (input.config.flushOnCapture && position !== undefined && !(await flushThrough(input, position))) {
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
