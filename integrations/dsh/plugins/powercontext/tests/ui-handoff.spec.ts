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

import { expect, it, vi } from 'vitest'
import { HandoffHttp, workInput } from '../src/ui/handoff/client.ts'
import type { PreflightView } from '../src/handoff/preflight.ts'

it('can request targets with a browser fetch that requires the global receiver', async () => {
  vi.stubGlobal('fetch', function (this: unknown) {
    if (this !== undefined && this !== globalThis) throw new TypeError('Illegal invocation')
    return Promise.resolve(new Response('{"targets":[]}', { status: 200 }))
  })
  try {
    await expect(new HandoffHttp().targets()).resolves.toEqual({ targets: [] })
  } finally {
    vi.unstubAllGlobals()
  }
})

it('passes the exact preflight boundary and revision with user-reviewed work and explicit retry', async () => {
  const requests: Array<{ url: string; body: Record<string, unknown> }> = []
  const fetcher = vi.fn(async (url: string | URL | Request, init?: RequestInit) => {
    requests.push({ url: String(url), body: JSON.parse(String(init?.body)) })
    return new Response('{}', { status: 200, headers: { 'content-type': 'application/json' } })
  })
  const api = new HandoffHttp(fetcher as typeof fetch)
  const source = { sessionId: 'session', messageId: 'answer', throughSeq: 42, cwd: '/work', workspaceObservedAt: '2026-11-13T00:00:00.000Z' }
  const preflight = { preflightId: 'p1', revision: 3, targetId: 'codex-desktop', source } as PreflightView
  const work = workInput(' goal ', ' current ', ' next ', 'missing tests\n\nunknown workspace', 'continuable')
  await api.prepare(preflight, 'request-1', work, 'old-delivery')
  await api.launch('delivery-1', source.sessionId, preflight.targetId, 'request-2', 'attempt-1')
  expect(requests[0]).toEqual({ url: '/powercontext/deliveries', body: {
    preflightId: 'p1', expectedRevision: 3, source, requestId: 'request-1', workInput: {
      schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input', objective: 'goal',
      state: [{ text: 'current', basis: 'declared', evidence: [] }], disposition: 'continuable',
      next_action: { text: 'next', basis: 'declared', evidence: [] }, omissions: ['missing tests', 'unknown workspace', 'The historical workspace observation was not available.'],
    }, supersedesDeliveryId: 'old-delivery',
  } })
  expect(requests[1]).toEqual({ url: '/powercontext/deliveries/delivery-1/launch', body: {
    sessionId: 'session', targetId: 'codex-desktop', requestId: 'request-2', retryOf: 'attempt-1',
  } })
})

it('does not invent success when the Host reports an error', async () => {
  const api = new HandoffHttp(vi.fn(async () => new Response('{"code":"stale_preflight"}', { status: 409 })) as typeof fetch)
  await expect(api.get('delivery', 'session')).rejects.toThrow('stale_preflight')
  expect(api.exportUrl('delivery', 'session')).toBe('/powercontext/deliveries/delivery/export?sessionId=session')
})

it('reads and validates the target installation diagnostic', async () => {
  const fetcher = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ targetId: 'codex-desktop', state: 'present', version: '1.2.3' }), { status: 200 }))
  const api = new HandoffHttp(fetcher as typeof fetch)
  const signal = new AbortController().signal
  expect(await api.diagnoseTarget('codex-desktop', signal)).toEqual({ targetId: 'codex-desktop', state: 'present', version: '1.2.3' })
  expect(fetcher.mock.calls[0][0]).toBe('/powercontext/targets/codex-desktop/diagnose')
  expect(fetcher.mock.calls[0][1]).toMatchObject({ signal })
  await expect(api.diagnoseTarget('claude-desktop')).rejects.toThrow('integration_unavailable')
})

it('rejects a recovery response for a different delivery or session', async () => {
  const api = new HandoffHttp(vi.fn(async () => new Response(JSON.stringify({
    delivery: { deliveryId: 'other', source: { sessionId: 'session' } },
  }), { status: 200 })) as typeof fetch)
  await expect(api.get('wanted', 'session')).rejects.toThrow('binding_mismatch')
})
