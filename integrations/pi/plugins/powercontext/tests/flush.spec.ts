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

import { describe, expect, it, vi } from 'vitest'
import { captureUserPrompt } from '../src/capture.ts'
import { resolveConfig } from '../src/config.ts'
import { UnknownOutcomeError } from '../src/errors.ts'
import { createPendingSourceFlusher } from '../src/flush.ts'

describe('uncertain flush outcomes', () => {
  it('does not enqueue an immediate flush for automatic replay', async () => {
    const request = vi.fn()
      .mockResolvedValueOnce({ kind: 'json', value: { position: 3 } })
      .mockRejectedValue(new UnknownOutcomeError('/v1/memory/flush'))
    const onFlushFailure = vi.fn()
    const position = await captureUserPrompt({
      client: { request } as never,
      config: { ...resolveConfig({}), flushOnCapture: true },
      scopeId: 'one', prompt: 'Continue', cwd: '/workspace', sessionId: 'session', turnId: 'turn',
      onFlushFailure,
    })
    expect(position).toBe(3)
    expect(request.mock.calls.map(([operation]) => operation)).toEqual(['capture_content_source', 'flush_memory'])
    expect(onFlushFailure).not.toHaveBeenCalled()
  })

  it('does not retry the same pending write on another lifecycle event', async () => {
    const request = vi.fn().mockRejectedValue(new UnknownOutcomeError('/v1/memory/flush'))
    const flusher = createPendingSourceFlusher({ request } as never, resolveConfig({}))
    flusher.record('one', 3)
    await flusher.flush()
    await flusher.flush()
    expect(request).toHaveBeenCalledTimes(1)
    flusher.record('one', 4)
    await flusher.flush()
    expect(request).toHaveBeenCalledTimes(2)
  })
})
