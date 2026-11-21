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
import { Context } from '@deepseek-ai/cordis'
import { registerCompanionMemory, COMPANION_MEMORY_SERVICE, type CompanionMemoryService } from '../src/companion-memory.ts'
import { ServerResponseError } from '../src/errors.ts'
import { resolveConfig } from '../src/config.ts'
import type { PluginRuntime } from '../src/invoke.ts'
import { PowerContextClient } from '../src/client.ts'

function fixture() {
  let cwd: string | undefined = '/projects/alpha'
  let service!: CompanionMemoryService
  let dispose!: () => void
  const content = 'Project Alpha uses PostgreSQL.'
  const request = vi.fn(async () => ({ kind: 'json', value: {
    schema: 'powercontext.prepared-context.v1', status: 'ready', content, content_bytes: Buffer.byteLength(content),
  } }))
  const resolveScope = vi.fn(async (path?: string) => path?.endsWith('alpha') ? 'scope-alpha' : 'scope-beta')
  const runtime = {
    config: resolveConfig({ baseUrl: 'http://127.0.0.1:8765', authorization: 'Bearer private-token', timeoutMs: 50 }),
    client: { request }, resolveScope, log: vi.fn(),
  } as unknown as PluginRuntime
  const ctx = {
    get: () => ({ get: (id: string) => id === 'active' ? { header: { cwd } } : undefined }),
    provide: (_name: string, value: CompanionMemoryService) => { service = value },
    effect: (callback: () => () => void) => { dispose = callback() },
  }
  registerCompanionMemory(ctx, runtime)
  return { service, runtime, request, resolveScope, dispose, cwd: (next?: string) => { cwd = next } }
}

describe('optional companion memory service', () => {
  it('propagates valid per-call parents without exposing credentials or accepting arbitrary headers', async () => {
    const f = fixture()
    const seen: Headers[] = []
    f.runtime.client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8765', authorization: 'Bearer actual-host-key', requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        seen.push(new Headers(init.headers))
        const content = 'Project memory'
        return new Response(JSON.stringify({ schema: 'powercontext.prepared-context.v1', status: 'ready', content, content_bytes: Buffer.byteLength(content) }), { headers: { 'content-type': 'application/json' } })
      },
    })
    const parents = ['1', '2'].map(char => `00-${char.repeat(32)}-${char.repeat(16)}-01`)
    await Promise.all(parents.map(traceparent => f.service.prepare({
      sessionId: 'active', query: 'hello', traceContext: { traceparent, authorization: 'injected-key' } as { traceparent: string },
    })))
    expect(seen.map(headers => headers.get('traceparent'))).toEqual(parents)
    expect(seen.every(headers => headers.get('authorization') === 'Bearer actual-host-key')).toBe(true)
    await f.service.prepare({ sessionId: 'active', query: 'hello', traceContext: { traceparent: 'invalid\r\nbaggage: secret' } })
    expect(seen[2].has('traceparent')).toBe(false)
    expect(seen[2].has('baggage')).toBe(false)
  })
  it('reuses the running transport and requires an explicit or durable workspace binding', async () => {
    const f = fixture()
    const result = await f.service.prepare({ sessionId: 'active', query: 'Which database?', maxBytes: 1024 })
    expect(result).toMatchObject({ status: 'ready', workspace: 'alpha', scopeId: 'scope-alpha' })
    expect(f.resolveScope).toHaveBeenCalledWith('/projects/alpha', expect.any(AbortSignal), false)
    expect(f.request).toHaveBeenCalledWith('prepare_context', {
      scope_id: 'scope-alpha', query: 'Which database?', max_bytes: 1024,
      assembly: { sections: [{ family: 'memory', limit: 3 }] },
    }, expect.any(AbortSignal), { traceContext: undefined })
    expect(JSON.stringify(result)).not.toContain('private-token')
    expect(JSON.stringify(result)).not.toContain('127.0.0.1')
  })

  it('describes bindings without retrieving or writing anything', async () => {
    const f = fixture()
    expect(await f.service.prepare({ sessionId: 'active' })).toMatchObject({ status: 'connected' })
    expect(f.request).not.toHaveBeenCalled()
  })

  it('never resolves a missing session or a missing workspace to the default Scope', async () => {
    const f = fixture()
    expect(await f.service.prepare({ sessionId: '' })).toMatchObject({ reason: 'session-required' })
    expect(await f.service.prepare({ sessionId: 'unknown' })).toMatchObject({ reason: 'workspace-unavailable' })
    f.cwd(undefined)
    expect(await f.service.prepare({ sessionId: 'active' })).toMatchObject({ reason: 'workspace-unavailable' })
    expect(f.resolveScope).not.toHaveBeenCalled()
    expect(f.request).not.toHaveBeenCalled()
  })

  it('does not retrieve after workspace binding failure', async () => {
    const f = fixture()
    f.resolveScope.mockRejectedValue(new ServerResponseError({ statusCode: 404, message: 'sensitive connection information' }))
    expect(await f.service.prepare({ sessionId: 'active', query: 'hello' })).toEqual({ status: 'unavailable', reason: 'scope-unavailable' })
    expect(f.request).not.toHaveBeenCalled()
  })

  it('distinguishes a broken connection from a missing workspace binding', async () => {
    const f = fixture()
    f.resolveScope.mockRejectedValue(new Error('sensitive connection information'))
    expect(await f.service.prepare({ sessionId: 'active' })).toEqual({ status: 'unavailable', reason: 'connection-unavailable' })
  })

  it('partitions local history by workspace, Scope and connection identity', async () => {
    const f = fixture()
    const a = await f.service.prepare({ sessionId: 'active' })
    f.cwd('/projects/beta')
    const b = await f.service.prepare({ sessionId: 'active' })
    expect(a).not.toEqual(b)
    f.cwd('/projects/alpha')
    f.runtime.config.authorization = 'Bearer replacement-token'
    const changed = await f.service.prepare({ sessionId: 'active' })
    expect(changed).not.toEqual(a)
  })

  it('rejects late replies after the session workspace changes', async () => {
    const f = fixture()
    f.request.mockImplementation(async () => {
      f.cwd('/projects/beta')
      return { kind: 'json', value: { schema: 'powercontext.prepared-context.v1', status: 'ready', content: 'alpha', content_bytes: 5 } }
    })
    expect(await f.service.prepare({ sessionId: 'active', query: 'hello' })).toMatchObject({ status: 'unavailable' })
  })

  it('bounds a dependency ignoring cancellation and handles plugin disposal', async () => {
    const f = fixture()
    f.resolveScope.mockImplementation(() => new Promise(() => {}))
    // Keep the test event loop alive: AbortSignal.timeout itself is unrefed.
    const keepAlive = setInterval(() => {}, 100)
    try {
      expect(await f.service.prepare({ sessionId: 'active', query: 'hello' })).toMatchObject({ status: 'unavailable' })
      f.dispose()
      expect(await f.service.prepare({ sessionId: 'active' })).toMatchObject({ status: 'unavailable' })
    } finally { clearInterval(keepAlive) }
  })

  it('validates returned context and redacts credentials in the query', async () => {
    const f = fixture()
    f.request.mockResolvedValue({ kind: 'json', value: { schema: 'wrong', status: 'ready', content: 'oversized', content_bytes: 9999 } })
    expect(await f.service.prepare({ sessionId: 'active', query: 'private-token' })).toMatchObject({ status: 'unavailable' })
    expect(f.request).toHaveBeenCalledWith(
      'prepare_context', expect.objectContaining({ query: '[REDACTED]' }),
      expect.any(AbortSignal), { traceContext: undefined },
    )
    expect(JSON.stringify(f.request.mock.calls)).not.toContain('private-token')
  })

  it('registers and removes the service with a real Cordis plugin lifecycle', async () => {
    const app = new Context()
    const f = fixture()
    const fork = app.plugin((ctx) => registerCompanionMemory(ctx, f.runtime))
    await fork
    const service = app.get(COMPANION_MEMORY_SERVICE) as CompanionMemoryService
    expect(service.protocol).toBe('powercontext.companion-memory.v1')
    await fork.dispose()
    expect(app.get(COMPANION_MEMORY_SERVICE)).toBeUndefined()
    expect(await service.prepare({ sessionId: 'active' })).toMatchObject({ status: 'unavailable' })
  })
})
