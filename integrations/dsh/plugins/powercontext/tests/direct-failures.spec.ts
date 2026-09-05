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

import { describe, expect, it } from 'vitest'
import { PowerContextClient, type FetchFn } from '../src/client.ts'
import { registerCommands, type CommandResult } from '../src/commands.ts'
import { resolveConfig } from '../src/config.ts'
import { createDiagnosticEmitter } from '../src/diagnostics.ts'
import type { PluginRuntime, ToolResult } from '../src/invoke.ts'
import { resolveScopeId } from '../src/scope.ts'
import { registerTools } from '../src/tools.ts'

const RESOLVE_PATH = '/v1/scope-bindings/resolve'
const PRIVATE = 'private-response-marker'

function fixture(fetchImpl: FetchFn, baseUrl = 'http://127.0.0.1:8000', requestTimeoutMs = 1000) {
  const calls: Array<{ path: string; body: Record<string, unknown> }> = []
  const events: Record<string, unknown>[] = []
  const config = resolveConfig({ baseUrl, requestTimeoutMs }, {})
  const client = new PowerContextClient({
    baseUrl: config.baseUrl,
    requestTimeoutMs: config.requestTimeoutMs,
    fetch: async (url, init) => {
      calls.push({ path: new URL(url).pathname, body: JSON.parse(String(init.body ?? '{}')) })
      return fetchImpl(url, init)
    },
  })
  const runtime: PluginRuntime = {
    client,
    config,
    resolveScope: (cwd, signal) => resolveScopeId(client, cwd, config.scopeId, signal),
    log: createDiagnosticEmitter(line => events.push(JSON.parse(line))),
  }
  const tools: Array<{
    name: string
    execute: (args: Record<string, unknown>, exec: unknown) => Promise<ToolResult>
  }> = []
  registerTools({ tools: { register: tool => tools.push(tool as never) }, on: () => undefined }, runtime, value => value)
  let command!: (invocation: {
    rawInput: string
    signal: AbortSignal
    agent: { session: { header: { cwd: string } } }
  }) => Promise<CommandResult>
  registerCommands({
    get: () => ({ register: (definition: { handler: typeof command }) => { command = definition.handler } }),
  }, runtime)
  const invocation = (signal = new AbortController().signal) => ({
    signal, agent: { session: { header: { cwd: '/workspace' } } },
  })
  return {
    calls, events, runtime,
    tool: (name: string, args: Record<string, unknown> = {}, signal?: AbortSignal) =>
      tools.find(tool => tool.name === name)!.execute(args, invocation(signal)),
    command: (rawInput: string, signal?: AbortSignal) => command({ ...invocation(signal), rawInput }),
  }
}

function domainResponse(status: number, code: unknown): Response {
  return Response.json({ error: { code, message: PRIVATE } }, {
    status, headers: { 'X-PowerContext-Request-ID': 'request-1' },
  })
}

describe.each(['tool', 'command'] as const)('registered %s failure boundary', entry => {
  async function remember(h: ReturnType<typeof fixture>, signal?: AbortSignal): Promise<ToolResult> {
    if (entry === 'tool') return h.tool('pc_remember', { kind: 'decision', text: 'Keep the API stable.' }, signal)
    const result = await h.command('remember Keep the API stable.', signal)
    expect(result.kind).toBe('error')
    return JSON.parse(result.text)
  }

  it.each([
    ['missing route', () => Response.json({ detail: 'Not Found' }, { status: 404 }), 'version_mismatch', 'version_mismatch'],
    ['missing Scope', () => domainResponse(404, 'scope_not_found'), 'not_found', undefined],
    ['authentication', () => domainResponse(401, 'unauthorized'), 'authentication_failed', 'authentication_failed'],
    ['unavailable', () => domainResponse(503, 'runtime_not_ready'), 'unavailable', 'server_unavailable'],
    ['invalid response', () => new Response('{broken', { status: 200 }), 'invalid_response', 'invalid_response'],
  ] as const)('contains a %s failure before a write', async (_name, response, code, outcome) => {
    const h = fixture(async () => response())
    const result = await remember(h)
    expect(result).toMatchObject({ ok: false, code })
    if (code === 'not_found') expect(result).toMatchObject({ error_code: 'scope_not_found', request_id: 'request-1' })
    expect(h.calls.map(call => call.path)).toEqual([RESOLVE_PATH])
    expect(h.events.map(event => event.outcome)).toEqual(outcome ? [outcome] : [])
    expect(JSON.stringify([result, h.events])).not.toContain(PRIVATE)
    expect(JSON.stringify(h.events)).not.toContain('http://')
  })

  it('keeps no resolved Scope distinct from a failed Server', async () => {
    const h = fixture(async () => Response.json({}))
    h.runtime.resolveScope = async () => undefined
    await expect(remember(h)).resolves.toMatchObject({
      ok: false, code: 'unscoped',
    })
    expect(h.calls).toEqual([])
    expect(h.events).toEqual([])
  })

  it('preserves a resource 404 and its domain code after resolving Scope', async () => {
    const h = fixture(async url => new URL(url).pathname === RESOLVE_PATH
      ? Response.json({ scope_id: 'scope-workspace' })
      : domainResponse(404, 'memory_not_found'))
    const result = entry === 'tool'
      ? await h.tool('pc_memory_get', { citation: {} })
      : JSON.parse((await h.command('search API')).text)
    expect(result).toMatchObject({
      ok: false, code: 'not_found', error_code: 'memory_not_found', status: 404, request_id: 'request-1',
    })
    expect(h.events).toEqual([])
  })

  it.each([
    [409, 'revision_conflict'],
    [422, 'invalid_request'],
  ])('preserves existing HTTP %s business codes without Server text', async (status, code) => {
    const h = fixture(async url => new URL(url).pathname === RESOLVE_PATH
      ? Response.json({ scope_id: 'scope-workspace' })
      : domainResponse(status as number, code))
    const result = await remember(h)
    expect(result).toMatchObject({ ok: false, code, status })
    expect(JSON.stringify([result, h.events])).not.toContain(PRIVATE)
    expect(h.events).toEqual([])
  })

  it.each([PRIVATE, 123, null, { toString: PRIVATE }])('does not expose an unrecognized 404 code %j or infer a route mismatch', async code => {
    const h = fixture(async () => domainResponse(404, code))
    const result = await remember(h)
    expect(result).toMatchObject({ ok: false, code: 'not_found' })
    expect(result).not.toHaveProperty('error_code')
    expect(JSON.stringify([result, h.events])).not.toContain(PRIVATE)
  })

  it.each(['scope', 'operation'])('contains diagnostic writer errors during %s failure', async stage => {
    const h = fixture(async url => {
      if (stage === 'operation' && new URL(url).pathname === RESOLVE_PATH) {
        return Response.json({ scope_id: 'scope-workspace' })
      }
      throw new TypeError(PRIVATE)
    })
    h.runtime.log = () => { throw new Error(PRIVATE) }
    await expect(remember(h)).resolves.toMatchObject({
      ok: false, code: 'unavailable',
    })
  })

  it('contains a rejected diagnostic callback and omits unrecognized diagnostic codes', async () => {
    const h = fixture(async () => domainResponse(401, PRIVATE))
    const result = await remember(h)
    expect(result.code).toBe('authentication_failed')
    expect(h.events).toEqual([expect.objectContaining({ outcome: 'authentication_failed' })])
    expect(JSON.stringify([result, h.events])).not.toContain(PRIVATE)
    h.runtime.log = async () => { throw new Error(PRIVATE) }
    await expect(remember(h)).resolves.toMatchObject({ ok: false, code: 'authentication_failed' })
  })

  it('bounds repeated direct-operation diagnostics', async () => {
    const h = fixture(async url => new URL(url).pathname === RESOLVE_PATH
      ? Response.json({ scope_id: 'scope-workspace' })
      : domainResponse(503, 'runtime_not_ready'))
    await remember(h)
    await remember(h)
    expect(h.events).toEqual([expect.objectContaining({
      event: entry === 'tool' ? 'tool_call' : 'command', outcome: 'server_unavailable', recovery: 'powercontext doctor',
    })])
  })

  it('cancels Scope resolution before performing a later write', async () => {
    let started!: () => void
    const entered = new Promise<void>(resolve => { started = resolve })
    const h = fixture(async (_url, init) => new Promise<Response>((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true })
      started()
    }))
    const controller = new AbortController()
    const result = remember(h, controller.signal)
    await entered
    controller.abort()
    const bounded = await Promise.race([
      result,
      new Promise(resolve => setTimeout(() => resolve('did not cancel'), 200)),
    ])
    expect(bounded).toMatchObject({ ok: false, code: 'unavailable' })
    expect(h.calls.map(call => call.path)).toEqual([RESOLVE_PATH])
    expect(h.events.map(event => event.outcome)).toEqual(['server_unavailable'])
  })

  it('bounds a stalled Scope request with the existing per-request timeout', async () => {
    const h = fixture(async (_url, init) => new Promise<Response>((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true })
    }), undefined, 20)
    await expect(remember(h)).resolves.toMatchObject({ ok: false, code: 'unavailable' })
    expect(h.calls.map(call => call.path)).toEqual([RESOLVE_PATH])
    expect(h.events.map(event => event.outcome)).toEqual(['server_unavailable'])
  })

  it('does not dispatch a write when cancellation arrives as Scope resolution completes', async () => {
    const controller = new AbortController()
    const h = fixture(async () => Response.json({}))
    h.runtime.resolveScope = async () => {
      controller.abort()
      return 'scope-workspace'
    }
    await expect(remember(h, controller.signal)).resolves.toMatchObject({ ok: false, code: 'unavailable' })
    expect(h.calls).toEqual([])
    expect(h.events.map(event => event.outcome)).toEqual(['server_unavailable'])
  })
})

describe('registered /pc command routing', () => {
  it('checks health and capabilities without a working Scope endpoint', async () => {
    const h = fixture(async url => new URL(url).pathname === RESOLVE_PATH
      ? Response.json({ detail: 'Not Found' }, { status: 404 })
      : Response.json({ status: 'ready' }))
    expect((await h.command('doctor')).kind).toBe('success')
    expect((await h.command('capabilities')).kind).toBe('success')
    expect(h.calls.map(call => call.path)).toEqual(['/health/live', '/health/ready', '/v1/capabilities'])
  })

  it('keeps both Doctor results when one health endpoint fails', async () => {
    const h = fixture(async url => new URL(url).pathname === '/health/ready'
      ? domainResponse(503, 'runtime_not_ready')
      : Response.json({ status: 'alive' }))
    const result = await h.command('doctor')
    expect(result.kind).toBe('error')
    expect(JSON.parse(result.text).data).toMatchObject({
      live: { ok: true }, ready: { ok: false, code: 'unavailable' },
    })
  })

  it.each(['unknown', 'search', 'remember', 'review approve only-id', 'review reject id 1', 'skills'])(
    'validates "%s" without resolving Scope', async rawInput => {
      const h = fixture(async () => { throw new TypeError('Server unavailable') })
      const result = await h.command(rawInput)
      expect(result.kind).toBe('error')
      expect(result.text).toMatch(/Usage:|Unknown/)
      expect(h.calls).toEqual([])
    },
  )

  it('keeps bare status available and redacts endpoint secrets when Scope fails', async () => {
    const h = fixture(
      async () => domainResponse(404, 'scope_not_found'),
      'http://user:private-response-marker@example.test/prefix?token=private-response-marker#private-response-marker',
    )
    h.runtime.config.scopeId = 'configured-but-unresolved'
    const result = await h.command('')
    expect(result.kind).toBe('error')
    expect(result.text).toContain('scope=unresolved')
    expect(result.text).toContain('scope_not_found')
    expect(result.text).toContain('/pc doctor')
    expect(result.text).not.toContain(PRIVATE)
    expect(result.text).not.toContain('configured-but-unresolved')
    expect(h.calls.map(call => call.path)).toEqual(['/prefix'])
  })

  it('contains a scoped command failure without writing or selecting another Scope', async () => {
    const h = fixture(async () => domainResponse(404, 'scope_not_found'))
    const result = await h.command('remember Keep the API stable.')
    expect(result.kind).toBe('error')
    expect(JSON.parse(result.text)).toMatchObject({
      ok: false, code: 'not_found', error_code: 'scope_not_found',
    })
    expect(h.calls.map(call => call.path)).toEqual([RESOLVE_PATH])
  })

  it('keeps stats restricted to the resolved Scope', async () => {
    const h = fixture(async url => new URL(url).pathname === RESOLVE_PATH
      ? Response.json({ scope_id: 'scope-workspace' })
      : Response.json({}))
    expect((await h.command('stats')).kind).toBe('success')
    expect(h.calls.find(call => call.path === '/v1/stats')?.body).toMatchObject({
      selection: { mode: 'exact', scope_ids: ['scope-workspace'] },
    })
  })
})
