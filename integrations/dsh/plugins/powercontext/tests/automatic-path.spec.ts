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

import type { Context } from '@deepseek-ai/cordis'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apply } from '../src/index.ts'
import type { PluginConfig } from '../src/config.ts'
import type { PreStepDecision, PromptMessage } from '../src/recall.ts'

const peers = vi.hoisted(() => ({ createUserMessage: vi.fn((input: unknown) => input) }))
vi.mock('../src/peers.ts', () => ({
  loadPeer: async (name: string) => name === '@deepseek-ai/dsh-llm'
    ? peers
    : { defineTool: (input: unknown) => input },
}))

const SCOPE = '/v1/scope-bindings/resolve'
const PREPARE = '/v1/context/prepare'
const CAPTURE = '/v1/sources/content'
const FLUSH = '/v1/memory/flush'
const TEXT = 'Use UTF-8. 保留原始引用 [memory:example@1].'
const PRIVATE = 'sensitive-fixture-marker'
const userMessage: PromptMessage = { content: [{ type: 'text', text: 'Continue the API work.' }], source: { kind: 'user' } }
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
const failure = (status: number, code?: unknown) => response({ error: { code, message: PRIVATE } }, status)
const ready = () => response({
  schema: 'powercontext.prepared-context.v1', status: 'ready', content: TEXT, content_bytes: Buffer.byteLength(TEXT),
})
const waitForAbort = (signal: AbortSignal) => new Promise<Response>((_resolve, reject) => {
  if (signal.aborted) reject(signal.reason)
  else signal.addEventListener('abort', () => reject(signal.reason), { once: true })
})

async function fixture(
  handler: (path: string, init: RequestInit) => Promise<Response> | Response = () => ready(),
  config: PluginConfig = {},
) {
  const requests: Array<{ path: string; body: Record<string, unknown> }> = []
  const logger = { warn: vi.fn(), debug: vi.fn() }
  type Hook = (payload: unknown, next: () => Promise<PreStepDecision>) => Promise<PreStepDecision>
  let hook: Hook | undefined
  const registry = { register: () => () => {}, section: () => () => {} }
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    requests.push({ path, body: JSON.parse(String(init.body ?? '{}')) })
    return handler(path, init)
  }))
  await apply({
    tools: registry,
    get: () => registry,
    on: (name: string, listener: Hook) => { if (name === 'agent/pre-step') hook = listener },
    logger,
  } as unknown as Context, {
    baseUrl: 'http://127.0.0.1:8765',
    timeoutMs: 1000,
    requestTimeoutMs: 200,
    ...config,
  })
  if (!hook) throw new Error('automatic hook was not registered')
  const run = (options: {
    signal?: AbortSignal; next?: () => Promise<PreStepDecision>; messages?: PromptMessage[]; cwd?: string
  } = {}) => hook!({
    agent: { session: { header: { id: 'test-session', cwd: options.cwd } } },
    messages: options.messages ?? [userMessage],
    turn: 1,
    signal: options.signal ?? new AbortController().signal,
  }, options.next ?? (async () => ({ kind: 'enter', messages: [userMessage] })))
  return { run, requests, logger, diagnostics: () => logger.warn.mock.calls.map(([line]) => JSON.parse(line)) }
}

function successfulRequest(path: string) {
  if (path === SCOPE) return response({ scope_id: 'scope-test' })
  if (path === PREPARE) return ready()
  if (path === CAPTURE) return response({ status: 'accepted', position: 1 }, 202)
  return response({ current_cursor: 1 })
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  peers.createUserMessage.mockReset().mockImplementation((input: unknown) => input)
})

describe('registered automatic path', () => {
  it.each([
    [404, undefined, 'version_mismatch', undefined],
    [404, 'scope_not_found', 'invalid_response', 'scope_not_found'],
    [404, PRIVATE, 'invalid_response', undefined],
    [404, { secret: PRIVATE }, 'invalid_response', undefined],
    [401, 'unauthorized', 'authentication_failed', 'unauthorized'],
    [403, 'forbidden', 'invalid_response', 'forbidden'],
    [503, undefined, 'server_unavailable', undefined],
  ])('reports Scope HTTP %s (%s) without reading or writing another Scope', async (status, code, outcome, publicCode) => {
    const h = await fixture(() => failure(status as number, code))
    expect(await h.run()).toEqual({ kind: 'enter', messages: [userMessage] })
    expect(h.requests.map(({ path }) => path)).toEqual([SCOPE])
    expect(h.diagnostics()).toEqual([expect.objectContaining({
      event: 'scope_resolve', outcome, http_status: status,
      ...(publicCode ? { error_code: publicCode } : {}),
    })])
    if (!publicCode) expect(h.diagnostics()[0]).not.toHaveProperty('error_code')
    expect(JSON.stringify(h.diagnostics())).not.toContain(PRIVATE)
    expect(JSON.stringify(h.diagnostics())).not.toContain(SCOPE)
  })

  it('reports unresolved Scope accurately and accepts the default Scope without cwd', async () => {
    const h = await fixture(() => response({}))
    await h.run()
    expect(h.diagnostics()).toEqual([expect.objectContaining({
      event: 'scope_resolve', outcome: 'skipped', reason: 'scope_unresolved',
    })])
    const valid = await fixture(successfulRequest)
    await valid.run()
    expect(valid.requests[0].body.binding_keys).toEqual([])
    expect(valid.requests.map(({ path }) => path)).toContain(CAPTURE)
  })

  it('reports connection failure without leaking exception text', async () => {
    const h = await fixture(() => { throw new Error(PRIVATE) })
    await h.run()
    expect(h.diagnostics()).toEqual([{
      component: 'powercontext.dsh', event: 'scope_resolve',
      outcome: 'server_unavailable', recovery: 'powercontext doctor',
    }])
  })

  it('passes cancellation into an in-flight Scope request', async () => {
    const controller = new AbortController()
    const h = await fixture((_path, init) => {
      controller.abort()
      return waitForAbort(init.signal!)
    }, { requestTimeoutMs: 1000 })
    await h.run({ signal: controller.signal })
    expect(h.requests.map(({ path }) => path)).toEqual([SCOPE])
    expect(h.diagnostics()[0]).toMatchObject({ event: 'scope_resolve', outcome: 'server_unavailable' })
  })

  it('does not start a request when already cancelled', async () => {
    const h = await fixture(successfulRequest)
    await h.run({ signal: AbortSignal.abort() })
    expect(h.requests).toEqual([])
  })

  it('reports the total deadline during Scope resolution', async () => {
    const h = await fixture((_path, init) => waitForAbort(init.signal!), { timeoutMs: 20, requestTimeoutMs: 200 })
    await h.run()
    expect(h.diagnostics()[0]).toMatchObject({ event: 'scope_resolve', outcome: 'server_unavailable' })
    expect(h.requests.map(({ path }) => path)).toEqual([SCOPE])
  })

  it('keeps capture independent after a prepare request timeout', async () => {
    const h = await fixture((path, init) => path === PREPARE ? waitForAbort(init.signal!) : successfulRequest(path),
      { requestTimeoutMs: 20 })
    expect(await h.run()).toEqual({ kind: 'enter', messages: [userMessage] })
    expect(h.requests.map(({ path }) => path)).toContain(CAPTURE)
    expect(h.diagnostics()[0]).toMatchObject({ event: 'context_prepare', outcome: 'server_unavailable' })
  })

  it.each([PREPARE, CAPTURE, FLUSH])('stops later stages after cancellation at %s', async (stage) => {
    const controller = new AbortController()
    const h = await fixture((path) => {
      if (path === stage) controller.abort()
      return successfulRequest(path)
    }, { flushOnCapture: true })
    expect(await h.run({ signal: controller.signal })).toEqual({ kind: 'enter', messages: [userMessage] })
    expect(h.requests.map(({ path }) => path)).toEqual([SCOPE, PREPARE, CAPTURE, FLUSH].slice(0,
      [SCOPE, PREPARE, CAPTURE, FLUSH].indexOf(stage) + 1))
  })

  it.each([CAPTURE, FLUSH])('keeps prepared content when %s fails', async (stage) => {
    const h = await fixture((path) => path === stage ? failure(503) : successfulRequest(path), { flushOnCapture: true })
    const result = await h.run()
    expect(result.messages).toHaveLength(2)
    expect(JSON.stringify(result.messages)).toContain(TEXT)
    expect(h.diagnostics()[0]).toMatchObject({ event: stage === CAPTURE ? 'capture_content_source' : 'flush_memory' })
    expect(h.requests.filter(({ path }) => path === CAPTURE)).toHaveLength(1)
  })

  it.each([
    ['debug', false], ['warn', false], ['debug', true], ['warn', true],
  ] as const)('keeps prepared content when the %s writer fails (async=%s)', async (writer, asynchronous) => {
    const h = await fixture((path) => writer === 'warn' && path === CAPTURE ? failure(503) : successfulRequest(path))
    h.logger[writer].mockImplementation(() => {
      if (asynchronous) return Promise.reject(new Error(PRIVATE))
      throw new Error(PRIVATE)
    })
    const result = await h.run()
    expect(result.messages).toHaveLength(2)
    expect(JSON.stringify(result.messages)).toContain(TEXT)
    expect(JSON.stringify(result.messages)).not.toContain(PRIVATE)
  })

  it('adds one snapshot with exact text and preserves the downstream decision', async () => {
    const h = await fixture(successfulRequest)
    const next = vi.fn(async () => ({ kind: 'enter', messages: [userMessage], startsRequestSeries: true }))
    const result = await h.run({ next })
    expect(result).toHaveProperty('startsRequestSeries', true)
    expect(next).toHaveBeenCalledOnce()
    expect(result.messages).toHaveLength(2)
    const message = result.messages![1] as { source: unknown; content: Array<{ text: string }> }
    expect(message.source).toEqual({
      kind: 'plugin', plugin: 'powercontext-dsh', form: 'snapshot',
      sections: [{ name: 'PowerContext', text: message.content[0].text }],
    })
    expect(message.content[0].text).toContain('untrusted historical evidence')
    expect(message.content[0].text.endsWith(TEXT)).toBe(true)
  })

  it('reports message construction failure without calling downstream twice', async () => {
    const h = await fixture(successfulRequest)
    peers.createUserMessage.mockImplementation(() => { throw new Error(PRIVATE) })
    const next = vi.fn(async () => ({ kind: 'enter', messages: [userMessage] }))
    expect(await h.run({ next })).toEqual({ kind: 'enter', messages: [userMessage] })
    expect(next).toHaveBeenCalledOnce()
    expect(h.diagnostics()).toEqual([{
      component: 'powercontext.dsh', event: 'context_inject', outcome: 'invalid_response',
    }])
  })

  it('does not swallow a downstream exception or turn rejection into entry', async () => {
    const h = await fixture(successfulRequest)
    const error = new Error('host failure')
    await expect(h.run({ next: async () => { throw error } })).rejects.toBe(error)
    expect(await h.run({ next: async () => ({ kind: 'reject' }) })).toEqual({ kind: 'reject' })
  })

  it('treats empty as normal and never captures injected content', async () => {
    const h = await fixture((path) => path === PREPARE ? response({
      schema: 'powercontext.prepared-context.v1', status: 'empty', content: null, content_bytes: 0,
    }) : successfulRequest(path))
    const injected = { content: [{ type: 'text', text: 'Historical context only' }], source: { kind: 'plugin' } }
    expect(await h.run({ messages: [injected] })).toEqual({ kind: 'enter', messages: [userMessage] })
    expect(h.diagnostics()).toEqual([])
    expect(h.requests.map(({ path }) => path)).not.toContain(CAPTURE)
  })

  it.each([
    () => new Response('{'),
    () => response({ schema: 'wrong' }),
    () => response({ schema: 'powercontext.prepared-context.v1', status: 'ready', content: TEXT, content_bytes: 1 }),
  ])('rejects invalid prepared content while preserving capture', async (invalid) => {
    const h = await fixture((path) => path === PREPARE ? invalid() : successfulRequest(path))
    expect(await h.run()).toEqual({ kind: 'enter', messages: [userMessage] })
    expect(h.diagnostics()[0]).toMatchObject({ event: 'context_prepare', outcome: 'invalid_response' })
    expect(h.requests.map(({ path }) => path)).toContain(CAPTURE)
  })

  it('bounds repeated Scope diagnostics across separate invocations', async () => {
    let now = 1000
    vi.spyOn(Date, 'now').mockImplementation(() => now)
    const h = await fixture(() => failure(503))
    await h.run()
    await h.run()
    expect(h.diagnostics()).toHaveLength(1)
    now += 60_000
    await h.run()
    expect(h.diagnostics()).toHaveLength(2)
  })
})
