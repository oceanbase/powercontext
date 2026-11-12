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

import { afterEach, describe, expect, it, vi } from 'vitest'
import {
  STATUS_SCHEMA, STATUS_STALE_AFTER_MS, parseStatusPayload, type StatusPayload,
} from '../src/status-contract.ts'
import {
  StatusController, activeSessionId, displayState, type StatusSnapshot,
} from '../src/ui/status-controller.ts'

function runtime() {
  const stage = (operation: string) => ({
    operation, state: 'not_yet_observed', observed_at: null, age_ms: null,
  })
  return {
    observation: 'local_automatic_path',
    freshness: 'not_yet_observed',
    attempt: null,
    turn: null,
    started_at: null,
    age_ms: null,
    stale_after_ms: 300_000,
    observed_scope: null,
    stages: {
      scope: stage('resolve_scope_binding'), prepare: stage('prepare_context'),
      capture: stage('capture_content_source'), flush: stage('flush_memory'), injection: stage('context_inject'),
    },
    coverage: 'Local observations are not Memory freshness.',
  }
}

export function statusPayload(overrides: {
  connection?: Partial<StatusPayload['connection']>
  session?: StatusPayload['session']
  notices?: string[]
} = {}): StatusPayload {
  return {
    schema: STATUS_SCHEMA,
    observed_at: new Date().toISOString(),
    plugin_version: '0.1.0',
    deployment: { kind: 'local', mode: 'desktop', owner_local: 'automatic', ssh: false, bridge: 'available', client: 'packaged' },
    configuration: {
      endpoint_origin: 'http://127.0.0.1:8000',
      endpoint_source: 'default',
      path_prefix: false,
      authorization_configured: false,
      authorization_source: 'default',
      scope_source: 'default',
      request_timeout_ms: 1_000,
    },
    connection: {
      state: 'ready', observed_at: new Date().toISOString(), age_ms: 20, stale: false,
      stale_after_ms: 30_000, generation: 1, in_flight: false, ...overrides.connection,
    },
    session: overrides.session ?? null,
    runtime: runtime(),
    server: {
      liveness: { state: 'ok', observed_at: new Date().toISOString(), age_ms: 20 },
      readiness: { state: 'ok', observed_at: new Date().toISOString(), age_ms: 20 },
      capabilities: { state: 'ok', observed_at: new Date().toISOString(), age_ms: 20 },
    },
    capabilities: {
      prepared_context: true,
      context_versions: ['powercontext.prepared-context.v1'],
      memory_extraction: true,
      handoff_generation: false,
    },
    notices: overrides.notices ?? [],
  } as StatusPayload
}

class FakeDocument {
  visibilityState: DocumentVisibilityState = 'visible'
  private readonly listeners = new Set<() => void>()

  addEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (type === 'visibilitychange') this.listeners.add(listener as () => void)
  }

  removeEventListener(type: string, listener: EventListenerOrEventListenerObject): void {
    if (type === 'visibilitychange') this.listeners.delete(listener as () => void)
  }

  setVisibility(value: DocumentVisibilityState): void {
    this.visibilityState = value
    for (const listener of this.listeners) listener()
  }
}

function response(payload: unknown, status = 200): Response {
  return new Response(JSON.stringify(payload), {
    status, headers: { 'content-type': 'application/json' },
  })
}

async function flush(): Promise<void> {
  await vi.advanceTimersByTimeAsync(0)
}

describe('status controller', () => {
  afterEach(() => {
    vi.useRealTimers()
  })

  it('reads the bridge without a conversation and publishes a verified connection', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => response(statusPayload()))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(String(fetcher.mock.calls[0]![0])).toBe('/powercontext/status')
    const init = fetcher.mock.calls[0]![1] as RequestInit
    expect(init.headers).toEqual({ accept: 'application/json' })
    expect(JSON.stringify(init)).not.toMatch(/authorization|bearer/i)
    expect(controller.getSnapshot().data?.connection.state).toBe('ready')
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('ready')
    unsubscribe()
    controller.dispose()
  })

  it('normalizes a missing Session to the null sentinel without aborting or refetching', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => response(statusPayload()))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(1)
    controller.setSession(undefined)
    controller.setSession(undefined)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().session_id).toBeNull()
    unsubscribe()
    controller.dispose()
  })

  it('sends only the active Session identity as a selector', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => response(statusPayload({
      session: { id: 'session-1', scope_id: 'scope-a', scope_state: 'resolved' },
    })))
    const controller = new StatusController({ fetch: fetcher, document })
    controller.setSession('session-1')
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()

    expect(String(fetcher.mock.calls[0]![0])).toBe('/powercontext/status?session_id=session-1')
    expect(controller.getSnapshot().data?.session?.scope_id).toBe('scope-a')
    unsubscribe()
    controller.dispose()
  })

  it('retains the selected Session while the host restores it', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(statusPayload({ notices: ['session_unavailable'] })))
      .mockResolvedValueOnce(response(statusPayload({
        session: { id: 'restoring-session', scope_id: 'scope-a', scope_state: 'resolved' },
      })))
    const controller = new StatusController({ fetch: fetcher, document })
    controller.setSession('restoring-session')
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()

    expect(controller.getSnapshot().session_id).toBe('restoring-session')
    expect(controller.getSnapshot().data?.session).toBeNull()
    controller.refreshNow()
    await flush()
    expect(fetcher.mock.calls[1]![0]).toBe('/powercontext/status?session_id=restoring-session')
    expect(controller.getSnapshot().data?.session?.scope_id).toBe('scope-a')
    unsubscribe()
    controller.dispose()
  })

  it('maps 401 to an authentication failure and recovers after the bounded retry', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({ code: 'unauthorized' }, 401))
      .mockResolvedValueOnce(response(statusPayload()))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()

    expect(controller.getSnapshot().error?.kind).toBe('authentication')
    expect(controller.getSnapshot().error?.code).toBe('unauthorized')
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('failed')

    await vi.advanceTimersByTimeAsync(2_000)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(2)
    expect(controller.getSnapshot().error).toBeNull()
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('ready')
    unsubscribe()
    controller.dispose()
  })

  it('distinguishes a non-local 403 and an offline bridge, then recovers', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response({ code: 'local_only' }, 403))
      .mockRejectedValueOnce(new TypeError('offline'))
      .mockResolvedValueOnce(response(statusPayload()))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()

    expect(controller.getSnapshot().error?.kind).toBe('forbidden')
    expect(controller.getSnapshot().error?.code).toBe('local_only')

    await vi.advanceTimersByTimeAsync(2_000)
    await flush()
    expect(controller.getSnapshot().error?.code).toBe('host_unreachable')

    await vi.advanceTimersByTimeAsync(4_000)
    await flush()
    expect(controller.getSnapshot().error).toBeNull()
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('ready')
    unsubscribe()
    controller.dispose()
  })

  it('invalidates an older Session response and ignores the superseded generation', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    let resolveOld: ((value: Response) => void) | undefined
    const old = new Promise<Response>((resolve) => { resolveOld = resolve })
    const fetcher = vi.fn()
      .mockReturnValueOnce(old)
      .mockResolvedValueOnce(response(statusPayload({
        session: { id: 'session-2', scope_id: 'scope-b', scope_state: 'resolved' },
      })))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    controller.setSession('session-2')
    await flush()

    expect(controller.getSnapshot().session_id).toBe('session-2')
    expect(controller.getSnapshot().data?.session?.id).toBe('session-2')
    resolveOld?.(response(statusPayload({
      session: { id: 'session-1', scope_id: 'scope-a', scope_state: 'resolved' },
    })))
    await flush()
    expect(controller.getSnapshot().data?.session?.id).toBe('session-2')
    expect(fetcher.mock.calls[1]![0]).toBe('/powercontext/status?session_id=session-2')
    unsubscribe()
    controller.dispose()
  })

  it('pauses while hidden and re-verifies immediately when visible', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => response(statusPayload()))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(1)

    document.setVisibility('hidden')
    await vi.advanceTimersByTimeAsync(60_000)
    expect(fetcher).toHaveBeenCalledTimes(1)

    document.setVisibility('visible')
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(2)
    unsubscribe()
    controller.dispose()
  })

  it('keeps a previous failure visible while a retry is pending and only turns green on success', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    let rejectRetry: ((reason: unknown) => void) | undefined
    let resolveRecovery: ((value: Response) => void) | undefined
    const retry = new Promise<Response>((_resolve, reject) => { rejectRetry = reject })
    const recovery = new Promise<Response>((resolve) => { resolveRecovery = resolve })
    const fetcher = vi.fn()
      .mockResolvedValueOnce(response(statusPayload()))
      .mockReturnValueOnce(retry)
      .mockReturnValueOnce(recovery)
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('ready')

    await vi.advanceTimersByTimeAsync(10_000)
    await flush()
    expect(controller.getSnapshot().loading).toBe(true)
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('ready')
    rejectRetry?.(new TypeError('offline'))
    await flush()
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('failed')

    await vi.advanceTimersByTimeAsync(2_000)
    await flush()
    expect(controller.getSnapshot().loading).toBe(true)
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('failed')
    resolveRecovery?.(response(statusPayload()))
    await flush()
    expect(controller.getSnapshot().error).toBeNull()
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('ready')
    unsubscribe()
    controller.dispose()
  })

  it('turns an expired success into the stale display state', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => response(statusPayload()))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()
    const snapshot = controller.getSnapshot()
    expect(displayState(snapshot, (snapshot.last_success_at ?? 0) + STATUS_STALE_AFTER_MS + 1)).toBe('stale')
    unsubscribe()
    controller.dispose()
  })

  it('treats an unavailable bridge route as unconfigured, not as a Server failure', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => response({ code: 'not_found' }, 404))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()
    expect(controller.getSnapshot().error?.code).toBe('host_bridge_unavailable')
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('unconfigured')
    unsubscribe()
    controller.dispose()
  })

  it('shows an unverified local Web host as unconfigured rather than a Server failure', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => response({ code: 'owner_local_unverified' }, 403))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()
    expect(controller.getSnapshot().error?.kind).toBe('bridge')
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('unconfigured')
    unsubscribe()
    controller.dispose()
  })

  it('caps retry delays and stops all work on dispose', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    const fetcher = vi.fn(async () => { throw new TypeError('offline') })
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    await flush()
    for (const delay of [2_000, 4_000, 8_000, 16_000, 30_000, 30_000]) {
      const before = fetcher.mock.calls.length
      await vi.advanceTimersByTimeAsync(delay)
      await flush()
      expect(fetcher.mock.calls.length).toBe(before + 1)
    }
    unsubscribe()
    controller.dispose()
    const calls = fetcher.mock.calls.length
    await vi.advanceTimersByTimeAsync(120_000)
    expect(fetcher.mock.calls.length).toBe(calls)
  })

  it('does not publish or reschedule after a superseding manual refresh', async () => {
    vi.useFakeTimers()
    const document = new FakeDocument()
    let resolveFirst: ((value: Response) => void) | undefined
    const first = new Promise<Response>((resolve) => { resolveFirst = resolve })
    const fetcher = vi.fn()
      .mockReturnValueOnce(first)
      .mockResolvedValueOnce(response(statusPayload()))
    const controller = new StatusController({ fetch: fetcher, document })
    const unsubscribe = controller.subscribe(() => undefined)
    controller.refreshNow()
    await flush()
    expect(fetcher).toHaveBeenCalledTimes(2)
    resolveFirst?.(response(statusPayload({ connection: { state: 'failed', code: 'old' } })))
    await flush()
    expect(controller.getSnapshot().data?.connection.code).toBeUndefined()
    expect(displayState(controller.getSnapshot(), Date.now())).toBe('ready')
    unsubscribe()
    controller.dispose()
  })
})

describe('status helpers', () => {
  it('selects the conversation retained by the main view without creating one', () => {
    expect(activeSessionId({ byId: {} })).toBeUndefined()
    expect(activeSessionId({
      byId: {
        a: { id: 'a', retainedBy: { mainView: 0 } },
        b: { id: 'b', retainedBy: { mainView: 1 } },
      },
    })).toBe('b')
  })

  it('drops unknown members instead of rendering arbitrary bridge fields', () => {
    const payload = statusPayload()
    const parsed = parseStatusPayload({
      ...payload,
      authorization: 'Bearer secret-value',
      session: { ...payload, id: 'session-1', scope_id: 'scope-a', scope_state: 'resolved' },
    })
    expect(parsed).not.toBeNull()
    expect(JSON.stringify(parsed)).not.toContain('secret-value')
    expect(JSON.stringify(parsed)).not.toContain('authorization":')
  })

  it('rejects a malformed envelope', () => {
    expect(parseStatusPayload(null)).toBeNull()
    expect(parseStatusPayload({ schema: 'other' })).toBeNull()
    const invalid = statusPayload() as unknown as Record<string, unknown>
    delete invalid.runtime
    expect(parseStatusPayload(invalid)).toBeNull()
  })
})


describe('version allowlist', () => {
  it('retains only the package version and accepts older bridges without metadata', () => {
    const payload = statusPayload()
    expect(parseStatusPayload(payload)?.server.package_version).toBeNull()
    expect(parseStatusPayload({ ...payload, server: { ...payload.server, package_version: '1.5.2.dev0+server', secret: 'private-value' } })?.server)
      .toEqual({ ...parseStatusPayload(payload)!.server, package_version: '1.5.2.dev0+server' })
  })
})
