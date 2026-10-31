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

import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import { buildSourceId } from '../src/capture.ts'
import type { PowerContextClient } from '../src/client.ts'
import { resolveConfig } from '../src/config.ts'
import { createPendingSourceFlusher } from '../src/flush.ts'

type ConformanceCase = {
  id: string
  modes: string[]
  input: Record<string, any>
  expected: Record<string, any>
}

const CONFORMANCE_CASES = (JSON.parse(readFileSync(
  new URL('../../../../../tests/integration_conformance/cases.json', import.meta.url),
  'utf8',
)) as { cases: ConformanceCase[] }).cases

function conformanceCase(id: string): ConformanceCase {
  const value = CONFORMANCE_CASES.find((item) => item.id === id && item.modes.includes('best_effort'))
  if (!value) throw new Error(`missing best-effort conformance case: ${id}`)
  return value
}

function progress(currentCursor: number, remainingWork = false): Record<string, unknown> {
  return {
    status: currentCursor ? 'processed' : 'idle',
    previous_cursor: 0,
    current_cursor: currentCursor,
    high_watermark: currentCursor,
    processed_source_count: currentCursor ? 1 : 0,
    remaining_work: remainingWork,
  }
}

function clientWith(...responses: Array<unknown | Error>): PowerContextClient {
  return {
    request: vi.fn(async () => {
      const response = responses.shift()
      if (response instanceof Error) throw response
      return { kind: 'json', value: response, status: 200, requestId: undefined }
    }),
  } as unknown as PowerContextClient
}

describe('pending Source flusher', () => {
  it('keeps duplicate event identity stable and later turns distinct', () => {
    const vector = conformanceCase('duplicate_delivery')
    const prompt = vector.input.duplicate_event[0].content
    const laterPrompt = vector.input.later_event.find((message: { role: string }) => message.role === 'user').content
    const first = buildSourceId('scope-a', 'session-1', '3', prompt)
    expect(buildSourceId('scope-a', 'session-1', '3', prompt)).toBe(first)
    expect(laterPrompt).toBe(prompt)
    expect(buildSourceId('scope-a', 'session-1', '4', laterPrompt)).not.toBe(first)
    expect(vector.expected).toMatchObject({ duplicate_same_id: true, later_distinct_id: true })
  })

  it('retries a cursor behind the target and acknowledges only after it catches up', async () => {
    const vector = conformanceCase('cursor_catches_up')
    const client = clientWith(...vector.input.flush_cursors.map((cursor: number, index: number) => (
      progress(cursor, vector.input.remaining_work[index])
    )))
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: String(vector.expected.flush_calls) }),
    )
    flusher.record('scope-a', vector.input.target_position)

    await expect(flusher.flush('session_before_compact')).resolves.toEqual({
      acknowledged: vector.expected.acknowledged,
      pendingScopes: vector.expected.pending_scopes,
    })
    expect(client.request).toHaveBeenCalledTimes(vector.expected.flush_calls)
  })

  it('clears a replayed position already covered by the confirmed cursor', async () => {
    const client = clientWith(progress(1))
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '1' }),
    )
    flusher.record('scope-a', 1)

    await expect(flusher.flush('session_shutdown')).resolves.toEqual({ acknowledged: true, pendingScopes: 0 })
    flusher.record('scope-a', 1)

    await expect(flusher.flush('session_shutdown')).resolves.toEqual({ acknowledged: true, pendingScopes: 0 })
    expect(client.request).toHaveBeenCalledTimes(1)
  })

  it('does not acknowledge any malformed 2xx cursor vector', async () => {
    const vector = conformanceCase('malformed_acknowledgement')
    for (const variant of vector.input.invalid_cursor_variants as string[]) {
      const diagnostic = vi.fn()
      const response = variant === 'missing'
        ? { ...progress(1), current_cursor: undefined }
        : { ...progress(1), current_cursor: '1' }
      const flusher = createPendingSourceFlusher(
        clientWith(response),
        resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '1' }),
        diagnostic,
      )
      flusher.record('private-scope', vector.input.target_position)

      await expect(flusher.flush('session_before_compact')).resolves.toEqual({
        acknowledged: vector.expected.acknowledged,
        pendingScopes: vector.expected.pending_scopes,
      })
      expect(diagnostic).toHaveBeenCalledWith(
        'pre_compaction_checkpoint',
        expect.any(Error),
        expect.objectContaining({
          host: 'pi',
          lifecycle_event: 'session_before_compact',
          target_position: vector.input.target_position,
        }),
      )
      expect(JSON.stringify(diagnostic.mock.calls)).not.toContain('private-scope')
    }
  })

  it('retains accepted positions after failure for a later closing boundary', async () => {
    const vector = conformanceCase('flush_failure')
    const client = clientWith(new Error(vector.input.failure), progress(vector.input.target_position))
    const flusher = createPendingSourceFlusher(client, resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '1' }))
    flusher.record('scope-a', vector.input.target_position)

    await expect(flusher.flush('session_before_switch')).resolves.toEqual({
      acknowledged: false,
      pendingScopes: vector.expected.pending_after_failure ? 1 : 0,
    })
    await expect(flusher.flush('session_shutdown')).resolves.toEqual({
      acknowledged: true,
      pendingScopes: 0,
    })
  })

  it('keeps acknowledgements isolated by Scope', async () => {
    const vector = conformanceCase('scope_isolation')
    const request = vi.fn(async (_operation: string, payload?: Record<string, unknown>) => ({
      kind: 'json' as const,
      value: progress(vector.input.scopes.find((scope: { id: string }) => scope.id === payload?.scope_id).cursor),
      status: 200,
      requestId: undefined,
    }))
    const flusher = createPendingSourceFlusher(
      { request } as unknown as PowerContextClient,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '2' }),
    )
    for (const scope of vector.input.scopes) flusher.record(scope.id, scope.target_position)

    await expect(flusher.flush('session_before_compact')).resolves.toEqual({
      acknowledged: false,
      pendingScopes: vector.expected.pending_scopes,
    })
    expect(request.mock.calls.map(([, payload]) => payload?.scope_id)).toEqual(['scope-a', 'scope-b'])
  })

  it('shares the flush call budget across all pending Scopes in one lifecycle boundary', async () => {
    const client = clientWith(progress(1), progress(1))
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '1' }),
    )
    flusher.record('scope-a', 1)
    flusher.record('scope-b', 1)

    await expect(flusher.flush('session_shutdown')).resolves.toEqual({ acknowledged: false, pendingScopes: 1 })
    expect(client.request).toHaveBeenCalledTimes(1)
    expect(client.request).toHaveBeenLastCalledWith('flush_memory', { scope_id: 'scope-a' }, expect.any(AbortSignal))

    await expect(flusher.flush('session_shutdown')).resolves.toEqual({ acknowledged: true, pendingScopes: 0 })
    expect(client.request).toHaveBeenCalledTimes(2)
    expect(client.request).toHaveBeenLastCalledWith('flush_memory', { scope_id: 'scope-b' }, expect.any(AbortSignal))
  })

  it('retains work cancelled before acknowledgement and keeps an acknowledgement after cancellation', async () => {
    const vector = conformanceCase('cancellation_boundaries')
    const before = new AbortController()
    before.abort()
    const cancelledClient = {
      request: vi.fn(async (_operation: string, _payload: unknown, signal?: AbortSignal) => {
        if (signal?.aborted) throw new Error('cancelled')
        return { kind: 'json' as const, value: progress(vector.input.target_position), status: 200, requestId: undefined }
      }),
    } as unknown as PowerContextClient
    const cancelled = createPendingSourceFlusher(cancelledClient, resolveConfig())
    cancelled.record('scope-a', vector.input.target_position)
    await expect(cancelled.flush('session_before_compact', before.signal)).resolves.toEqual({
      acknowledged: false,
      pendingScopes: vector.expected.pending_before,
    })

    const after = new AbortController()
    const acknowledgedClient = {
      request: vi.fn(async () => {
        after.abort()
        return { kind: 'json' as const, value: progress(vector.input.target_position), status: 200, requestId: undefined }
      }),
    } as unknown as PowerContextClient
    const acknowledged = createPendingSourceFlusher(acknowledgedClient, resolveConfig())
    acknowledged.record('scope-a', vector.input.target_position)
    await expect(acknowledged.flush('session_before_compact', after.signal)).resolves.toEqual({
      acknowledged: true,
      pendingScopes: vector.expected.pending_after,
    })
  })

  it('preserves an acknowledgement when a later flush call exceeds the boundary deadline', async () => {
    const diagnostic = vi.fn()
    const boundaries: AbortController[] = []
    let calls = 0
    const client = {
      request: vi.fn(async (_operation: string, _payload: unknown, signal?: AbortSignal) => {
        calls += 1
        if (calls === 1) {
          return { kind: 'json' as const, value: progress(1, true), status: 200, requestId: undefined }
        }
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(signal.reason), { once: true })
        })
        throw new Error('unreachable')
      }),
    } as unknown as PowerContextClient
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '2' }),
      diagnostic,
      () => {
        const controller = new AbortController()
        boundaries.push(controller)
        return controller.signal
      },
    )
    flusher.record('scope-a', 1)

    const flushing = flusher.flush('session_shutdown')
    await vi.waitFor(() => expect(client.request).toHaveBeenCalledTimes(2))
    boundaries[0].abort(new DOMException('deadline exceeded', 'TimeoutError'))

    await expect(flushing).resolves.toEqual({ acknowledged: true, pendingScopes: 0 })
    expect(diagnostic).not.toHaveBeenCalled()
    await expect(flusher.flush('session_shutdown')).resolves.toEqual({ acknowledged: true, pendingScopes: 0 })
    expect(client.request).toHaveBeenCalledTimes(2)
  })

  it('keeps boundary acknowledgement separate from a concurrent later capture', async () => {
    const diagnostic = vi.fn()
    const boundaries: AbortController[] = []
    let calls = 0
    const client = {
      request: vi.fn(async (_operation: string, _payload: unknown, signal?: AbortSignal) => {
        calls += 1
        if (calls === 1) {
          return { kind: 'json' as const, value: progress(1, true), status: 200, requestId: undefined }
        }
        if (calls === 2) {
          await new Promise<void>((_resolve, reject) => {
            signal?.addEventListener('abort', () => reject(signal.reason), { once: true })
          })
          throw new Error('unreachable')
        }
        return { kind: 'json' as const, value: progress(3), status: 200, requestId: undefined }
      }),
    } as unknown as PowerContextClient
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '2' }),
      diagnostic,
      () => {
        const controller = new AbortController()
        boundaries.push(controller)
        return controller.signal
      },
    )
    flusher.record('scope-a', 1)

    const firstBoundary = flusher.flush('session_before_compact')
    await vi.waitFor(() => expect(client.request).toHaveBeenCalledTimes(2))
    flusher.record('scope-a', 3)
    boundaries[0].abort(new DOMException('deadline exceeded', 'TimeoutError'))

    await expect(firstBoundary).resolves.toEqual({ acknowledged: true, pendingScopes: 1 })
    expect(diagnostic).not.toHaveBeenCalled()
    await expect(flusher.flush('session_shutdown')).resolves.toEqual({ acknowledged: true, pendingScopes: 0 })
    expect(client.request).toHaveBeenCalledTimes(3)
  })

  it('reports the boundary target rather than a concurrent later capture on deadline', async () => {
    const diagnostic = vi.fn()
    const boundaries: AbortController[] = []
    let calls = 0
    const client = {
      request: vi.fn(async (_operation: string, _payload: unknown, signal?: AbortSignal) => {
        calls += 1
        if (calls === 1) {
          return { kind: 'json' as const, value: progress(0, true), status: 200, requestId: undefined }
        }
        await new Promise<void>((_resolve, reject) => {
          signal?.addEventListener('abort', () => reject(signal.reason), { once: true })
        })
        throw new Error('unreachable')
      }),
    } as unknown as PowerContextClient
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '2' }),
      diagnostic,
      () => {
        const controller = new AbortController()
        boundaries.push(controller)
        return controller.signal
      },
    )
    flusher.record('scope-a', 1)

    const firstBoundary = flusher.flush('session_before_compact')
    await vi.waitFor(() => expect(client.request).toHaveBeenCalledTimes(2))
    flusher.record('scope-a', 3)
    boundaries[0].abort(new DOMException('deadline exceeded', 'TimeoutError'))

    await expect(firstBoundary).resolves.toEqual({ acknowledged: false, pendingScopes: 1 })
    expect(diagnostic).toHaveBeenCalledWith(
      'pre_compaction_checkpoint',
      expect.any(DOMException),
      expect.objectContaining({ target_position: 1, lifecycle_event: 'session_before_compact' }),
    )
  })

  it('serializes concurrent lifecycle boundaries', async () => {
    let release: (() => void) | undefined
    const blocked = new Promise<void>((resolve) => { release = resolve })
    let calls = 0
    const client = {
      request: vi.fn(async () => {
        calls += 1
        if (calls === 1) await blocked
        return { kind: 'json' as const, value: progress(7), status: 200, requestId: undefined }
      }),
    } as unknown as PowerContextClient
    const flusher = createPendingSourceFlusher(client, resolveConfig())
    flusher.record('scope-a', 7)

    const first = flusher.flush('session_before_compact')
    const second = flusher.flush('session_shutdown')
    await Promise.resolve()
    expect(calls).toBe(1)
    release?.()
    await expect(Promise.all([first, second])).resolves.toEqual([
      { acknowledged: true, pendingScopes: 0 },
      { acknowledged: true, pendingScopes: 0 },
    ])
    expect(calls).toBe(1)
  })

  it('includes queue waiting in the lifecycle deadline and retains pending work', async () => {
    vi.useFakeTimers()
    vi.setSystemTime(0)
    const diagnostic = vi.fn()
    const boundaries: AbortController[] = []
    setTimeout(() => {
      for (const controller of boundaries) {
        controller.abort(new DOMException('deadline exceeded', 'TimeoutError'))
      }
    }, 1000)
    let calls = 0
    const client = {
      request: vi.fn(async (_operation: string, _payload: unknown, signal?: AbortSignal) => {
        calls += 1
        if (calls > 2) return { kind: 'json' as const, value: progress(1), status: 200, requestId: undefined }
        await new Promise<void>((resolve, reject) => {
          const timeout = setTimeout(resolve, 900)
          signal?.addEventListener('abort', () => {
            clearTimeout(timeout)
            reject(signal.reason)
          }, { once: true })
        })
        if (signal?.aborted) throw signal.reason
        return { kind: 'json' as const, value: progress(0, true), status: 200, requestId: undefined }
      }),
    } as unknown as PowerContextClient
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '1' }),
      diagnostic,
      () => {
        const controller = new AbortController()
        boundaries.push(controller)
        return controller.signal
      },
    )
    flusher.record('private-scope', 1)

    try {
      const startedAt = Date.now()
      const agentEnd = flusher.flush('agent_end')
      const beforeCompact = flusher.flush('session_before_compact')
      const shutdown = flusher.flush('session_shutdown')
      await vi.advanceTimersByTimeAsync(1000)

      await expect(Promise.all([agentEnd, beforeCompact, shutdown])).resolves.toEqual([
        { acknowledged: false, pendingScopes: 1 },
        { acknowledged: false, pendingScopes: 1 },
        { acknowledged: false, pendingScopes: 1 },
      ])
      expect(Date.now() - startedAt).toBe(1000)
      expect(client.request).toHaveBeenCalledTimes(2)
      expect(diagnostic).toHaveBeenCalledWith(
        'flush_memory',
        expect.anything(),
        expect.objectContaining({
          outcome: 'deadline_exceeded',
          failure_class: 'deadline_exceeded',
          host: 'pi',
          lifecycle_event: 'session_shutdown',
          target_position: 1,
        }),
      )
      expect(JSON.stringify(diagnostic.mock.calls)).not.toContain('private-scope')

      await expect(flusher.flush('session_shutdown')).resolves.toEqual({ acknowledged: true, pendingScopes: 0 })
      expect(client.request).toHaveBeenCalledTimes(3)
    } finally {
      vi.useRealTimers()
    }
  })

  it('flushes only accepted positions at both Pi closing boundaries', async () => {
    const vector = conformanceCase('closing_boundaries')
    const client = clientWith(
      progress(vector.input.target_position),
      progress(vector.input.target_position + 1),
    )
    const flusher = createPendingSourceFlusher(client, resolveConfig())

    await flusher.flush(vector.input.events_by_host.pi[0])
    expect(client.request).toHaveBeenCalledTimes(vector.expected.uncaptured_flush_calls)

    flusher.record('scope-a', vector.input.target_position)
    await flusher.flush(vector.input.events_by_host.pi[0])
    flusher.record('scope-a', vector.input.target_position + 1)
    await flusher.flush(vector.input.events_by_host.pi[1])

    expect(client.request).toHaveBeenCalledTimes(vector.expected.captured_flush_calls)
  })

  it('retains an accepted Source when extraction is unavailable', async () => {
    const vector = conformanceCase('extraction_unavailable')
    const client = clientWith(new Error('memory extraction unavailable'))
    const flusher = createPendingSourceFlusher(
      client,
      resolveConfig({ POWERCONTEXT_PI_FLUSH_MAX_CALLS: '1' }),
    )
    flusher.record('scope-a', vector.input.target_position)

    await expect(flusher.flush('session_before_compact')).resolves.toEqual({
      acknowledged: false,
      pendingScopes: vector.expected.pending_scopes,
    })
    expect(client.request).toHaveBeenCalledTimes(vector.expected.flush_calls_by_host.pi)
  })
})
