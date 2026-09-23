import { InvalidResponseError } from '../src/errors.ts'
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
import type { CommandResult } from '../src/commands.ts'
import type { PreStepDecision, PromptMessage } from '../src/recall.ts'
import { STATUS_SESSION_LIMIT, STATUS_STALE_AFTER_MS } from '../src/status.ts'
import { MAX_SOURCE_LENGTH } from '../src/errors.ts'

const peers = vi.hoisted(() => ({ createUserMessage: vi.fn((value: unknown) => value) }))
vi.mock('../src/peers.ts', () => ({ loadPeer: async (name: string) => name === '@deepseek-ai/dsh-llm'
  ? peers : { defineTool: (value: unknown) => value } }))

const PRIVATE = 'private-user-and-response-marker'
const SCOPE = '/v1/scope-bindings/resolve'
const PREPARE = '/v1/context/prepare'
const CAPTURE = '/v1/sources/content'
const FLUSH = '/v1/memory/flush'
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status })
const textMessage = (text: string, kind = 'user') => ({ content: [{ type: 'text', text }], source: { kind } }) as PromptMessage
const prepared = (text: string | null = PRIVATE) => response({ schema: 'powercontext.prepared-context.v1',
  status: text ? 'ready' : 'empty', content: text, content_bytes: text ? Buffer.byteLength(text) : 0 })
const success = (path: string): Response => path === SCOPE ? response({ scope_id: 'scope-one' })
  : path === PREPARE ? prepared() : path === CAPTURE ? response({ position: 1 }, 202)
  : path === FLUSH ? response({ current_cursor: 1 }) : response({ status: 'ok' })
const waitForAbort = (signal: AbortSignal) => new Promise<Response>((_resolve, reject) => {
  if (signal.aborted) reject(signal.reason)
  else signal.addEventListener('abort', () => reject(signal.reason), { once: true })
})

async function fixture(handler: (path: string, init: RequestInit) => Response | Promise<Response> = success,
  config: PluginConfig = {}) {
  type Hook = (payload: unknown, next: () => Promise<PreStepDecision>) => Promise<PreStepDecision>
  type Command = (invocation: unknown) => Promise<CommandResult>
  let hook: Hook | undefined
  let command: Command | undefined
  const requests: string[] = []
  const registry = { register: (definition: { name: string; handler?: Command }) => {
    if (definition.name === 'pc') command = definition.handler
  }, section: () => {} }
  vi.stubGlobal('fetch', async (url: string, init: RequestInit) => {
    const path = new URL(url).pathname
    requests.push(path)
    return handler(path, init)
  })
  await apply({ tools: registry, get: () => registry,
    on: (name: string, listener: Hook) => { if (name === 'agent/pre-step') hook = listener },
    logger: { debug: () => { throw new Error(PRIVATE) }, warn: () => { throw new Error(PRIVATE) } },
  } as unknown as Context, { baseUrl: 'http://127.0.0.1:8765', timeoutMs: 1000,
    requestTimeoutMs: 100, ...config })
  if (!hook || !command) throw new Error('expected the registered hook and command')
  let turn = 0
  const owner = (sessionId = 'session-one', cwd = '/workspace') => ({ session: { header: { id: sessionId, cwd } } })
  const run = (options: { sessionId?: string; cwd?: string; messages?: PromptMessage[]; signal?: AbortSignal;
    next?: () => Promise<PreStepDecision> } = {}) => hook!({ agent: owner(options.sessionId, options.cwd),
    turn: ++turn, messages: options.messages ?? [textMessage(PRIVATE)],
    signal: options.signal ?? new AbortController().signal,
  }, options.next ?? (async () => ({ kind: 'enter', messages: [] })))
  const status = async (sessionId?: string, cwd?: string) => {
    const result = await command!({ rawInput: '', agent: owner(sessionId, cwd), signal: new AbortController().signal })
    return { ...result, automatic: JSON.parse(result.text.split('\nautomatic=')[1]) }
  }
  const doctor = () => command!({ rawInput: 'doctor', agent: owner(), signal: new AbortController().signal })
  return { run, status, doctor, requests }
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
  peers.createUserMessage.mockReset().mockImplementation(value => value)
})

describe('registered /pc automatic status', () => {
  it('reports unobserved before running and distinguishes preparation, acceptance and insertion without logging', async () => {
    const h = await fixture(success, { flushOnCapture: true })
    expect((await h.status()).automatic.freshness).toBe('not_yet_observed')
    expect(h.requests).toEqual([SCOPE])
    await h.run()
    const at = h.requests.length
    const status = await h.status()
    expect(h.requests.slice(at)).toEqual([SCOPE])
    expect(status.automatic).toMatchObject({ freshness: 'current', turn: '1', observed_scope: 'scope-one', stages: {
      scope: { state: 'resolved' }, prepare: { state: 'ready', content_bytes: Buffer.byteLength(PRIVATE) },
      capture: { state: 'accepted', http_status: 202 }, flush: { state: 'completed', code: 'cursor_reached' },
      injection: { state: 'appended' },
    } })
    expect(status.text).not.toContain(PRIVATE)
  })

  it('reports an empty retrieval as normal and preserves capture acceptance independently', async () => {
    const h = await fixture(path => path === PREPARE ? prepared(null) : success(path))
    await h.run()
    expect((await h.status()).automatic.stages).toMatchObject({ prepare: { state: 'empty', content_bytes: 0 },
      capture: { state: 'accepted' }, injection: { state: 'skipped', code: 'no_prepared_content' } })
  })

  it.each([PREPARE, CAPTURE, FLUSH])('keeps other stage outcomes when %s fails', async (path) => {
    const h = await fixture(p => p === path ? response({ error: { code: 'runtime_not_ready', message: PRIVATE } }, 503)
      : success(p), { flushOnCapture: true })
    await h.run()
    const status = await h.status()
    const failed = path === PREPARE ? 'prepare' : path === CAPTURE ? 'capture' : 'flush'
    expect(status.automatic.stages[failed]).toMatchObject({ state: 'unavailable', http_status: 503, code: 'runtime_not_ready' })
    if (path !== PREPARE) expect(status.automatic.stages.injection.state).toBe('appended')
    if (path !== CAPTURE) expect(status.automatic.stages.capture.state).toBe('accepted')
    if (path === CAPTURE) expect(status.automatic.stages.flush).toMatchObject({ state: 'skipped', code: 'capture_not_confirmed' })
    expect(status.text).not.toContain(PRIVATE)
  })

  it.each([
    [401, 'unauthorized', 'authentication_failed'], [403, 'forbidden', 'authorization_failed'],
    [404, undefined, 'required_route_missing'], [404, 'scope_not_found', 'scope_not_found'],
  ])('locates Scope HTTP %s failures without claiming automatic work ran', async (httpStatus, code, expected) => {
    const h = await fixture(() => response({ error: { code, message: PRIVATE } }, httpStatus))
    await h.run()
    const status = await h.status()
    expect(status.kind).toBe('error')
    expect(status.automatic.stages.scope).toMatchObject({ operation: 'resolve_scope_binding',
      state: 'unavailable', code: expected, http_status: httpStatus })
    for (const stage of ['prepare', 'capture', 'flush']) {
      expect(status.automatic.stages[stage]).toMatchObject({ state: 'skipped', code: 'scope_failed' })
    }
    expect(status.text).not.toContain(PRIVATE)
    expect(h.requests.every(p => p === SCOPE)).toBe(true)
  })

  it('distinguishes no resolved Scope from a failed resolver', async () => {
    const h = await fixture(() => response({}))
    await h.run()
    expect((await h.status()).automatic.stages.scope).toMatchObject({ state: 'skipped', code: 'scope_unresolved' })
  })

  it.each([
    [[], {}, 'no_messages'], [[textMessage(' ')], {}, 'empty_input'],
    [[textMessage('plugin context', 'plugin')], {}, 'no_user_text'],
    [[textMessage(PRIVATE)], { capturePrompts: false }, 'capture_disabled'],
    [[textMessage('x'.repeat(MAX_SOURCE_LENGTH + 1))], {}, 'source_too_long'],
    [[textMessage('api_key=sk-1234567890abcdefghijklmnopqrstuvwxyz')], {}, 'sensitive_content'],
  ] as const)('reports a precise capture skip reason %#', async (messages, config, reason) => {
    const h = await fixture(success, config)
    await h.run({ messages: [...messages] })
    expect((await h.status()).automatic.stages.capture).toMatchObject({ state: 'skipped', code: reason })
    expect(h.requests).not.toContain(CAPTURE)
  })

  it('reports capture request timeouts as unconfirmed writes while preserving successful preparation', async () => {
    const h = await fixture((path, init) => path === CAPTURE ? waitForAbort(init.signal!) : success(path),
      { requestTimeoutMs: 20 })
    await h.run()
    expect((await h.status()).automatic.stages).toMatchObject({
      capture: { state: 'unavailable', code: 'request_timeout', confirmation: 'unconfirmed' },
      prepare: { state: 'ready' }, injection: { state: 'appended' },
    })
  })

  it('distinguishes pre-start cancellation and an in-flight automatic deadline', async () => {
    const h = await fixture()
    await h.run({ signal: AbortSignal.abort() })
    expect(h.requests).toEqual([])
    expect((await h.status()).automatic.stages.capture).toMatchObject({ state: 'skipped', code: 'cancelled' })
    const slow = await fixture((path, init) => path === PREPARE ? waitForAbort(init.signal!) : success(path),
      { timeoutMs: 20, requestTimeoutMs: 100 })
    await slow.run()
    expect((await slow.status()).automatic.stages).toMatchObject({ prepare: { state: 'unavailable', code: 'request_timeout' },
      capture: { state: 'skipped', code: 'deadline_exceeded' }, injection: { state: 'skipped', code: 'deadline_exceeded' } })
  })

  it('recognizes caller cancellation even when its private reason is not an AbortError', async () => {
    const controller = new AbortController()
    const h = await fixture((path, init) => {
      if (path !== CAPTURE) return success(path)
      controller.abort(new Error(PRIVATE))
      return waitForAbort(init.signal!)
    })
    await h.run({ signal: controller.signal })
    const status = await h.status()
    expect(status.automatic.stages.capture).toMatchObject({ state: 'unavailable', code: 'cancelled', confirmation: 'unconfirmed' })
    expect(status.text).not.toContain(PRIVATE)
  })

  it('preserves an observed HTTP rejection even if cancellation arrives with the response', async () => {
    const controller = new AbortController()
    const h = await fixture(path => {
      if (path !== CAPTURE) return success(path)
      controller.abort()
      return response({ error: { code: 'unauthorized', message: PRIVATE } }, 401)
    })
    await h.run({ signal: controller.signal })
    expect((await h.status()).automatic.stages.capture).toMatchObject({ state: 'unavailable', http_status: 401,
      code: 'authentication_failed', confirmation: 'rejected' })
  })

  it('reports protocol failures without echoing the returned context', async () => {
    const h = await fixture(path => {
      if (path !== PREPARE) return success(path)
      throw new InvalidResponseError(PREPARE, 'req-protocol-fixture', 200)
    })
    await h.run()
    const status = await h.status()
    expect(status.automatic.stages.prepare).toMatchObject({ state: 'unavailable', code: 'invalid_response',
      http_status: 200, request_id: 'req-protocol-fixture' })
    expect(status.automatic.stages.capture.state).toBe('accepted')
    expect(status.text).not.toContain(PRIVATE)
  })

  it('does not call a ready result injected when downstream rejects, cancels, or fails', async () => {
    const h = await fixture()
    await h.run({ next: async () => ({ kind: 'reject' }) })
    expect((await h.status()).automatic.stages.injection).toMatchObject({ state: 'skipped', code: 'downstream_rejected' })
    const controller = new AbortController()
    await h.run({ signal: controller.signal, next: async () => { controller.abort(); return { kind: 'enter', messages: [] } } })
    expect((await h.status()).automatic.stages.injection).toMatchObject({ state: 'skipped', code: 'cancelled' })
    await expect(h.run({ next: async () => { throw new Error(PRIVATE) } })).rejects.toThrow(PRIVATE)
    expect((await h.status()).automatic.stages.injection).toMatchObject({ state: 'unavailable', code: 'downstream_failed' })
    peers.createUserMessage.mockImplementation(() => { throw new Error(PRIVATE) })
    await h.run()
    const status = await h.status()
    expect(status.automatic.stages.prepare.state).toBe('ready')
    expect(status.automatic.stages.injection).toMatchObject({ state: 'unavailable', code: 'message_wrap_failed' })
    expect(status.text).not.toContain(PRIVATE)
  })

  it('does not present exhausted or unobservable processing as completed', async () => {
    const h = await fixture(path => path === FLUSH ? response({ current_cursor: 0 }) : success(path),
      { flushOnCapture: true, flushMaxCalls: 1 })
    await h.run()
    expect((await h.status()).automatic.stages).toMatchObject({ capture: { state: 'accepted' },
      flush: { state: 'incomplete', code: 'flush_budget_exhausted' } })
    const missing = await fixture(path => path === CAPTURE ? response({}, 202) : success(path), { flushOnCapture: true })
    await missing.run()
    expect((await missing.status()).automatic.stages.flush).toMatchObject({ state: 'skipped', code: 'source_position_missing' })
  })

  it('marks aged or unverified history stale', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
    let scoped = true
    const h = await fixture(path => path === SCOPE && !scoped ? response({}, 401) : success(path))
    await h.run()
    now.mockReturnValue(1_700_000_000_000 + STATUS_STALE_AFTER_MS)
    expect((await h.status()).automatic).toMatchObject({ freshness: 'stale', stale_reason: 'age_limit' })
    scoped = false
    expect((await h.status()).automatic).toMatchObject({ freshness: 'stale', stale_reason: 'scope_unverified',
      observed_scope: 'scope-one', stages: { capture: { state: 'accepted' } } })
    await h.run()
    expect((await h.status()).automatic.stages.capture).toMatchObject({ state: 'skipped', code: 'scope_failed' })
  })

  it('isolates sessions/workspaces and labels a changed Scope instead of reusing success as current', async () => {
    let scopeId = 'scope-one'
    const h = await fixture(path => path === SCOPE ? response({ scope_id: scopeId }) : success(path))
    await h.run()
    expect((await h.status('other-session')).automatic.freshness).toBe('not_yet_observed')
    expect((await h.status('session-one', '/other-workspace')).automatic.freshness).toBe('not_yet_observed')
    scopeId = 'scope-two'
    await h.run({ sessionId: 'other-session' })
    expect((await h.status('other-session')).automatic.observed_scope).toBe('scope-two')
    expect((await h.status()).automatic).toMatchObject({ observed_scope: 'scope-one', freshness: 'stale', stale_reason: 'scope_changed' })
  })

  it('does not replace a newer same-session attempt when an older request completes late', async () => {
    vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)
    let release!: (value: Response) => void
    let entered!: () => void
    const started = new Promise<void>(resolve => { entered = resolve })
    const held = new Promise<Response>(resolve => { release = resolve })
    let first = true
    const h = await fixture(path => {
      if (path === PREPARE && first) { first = false; entered(); return held }
      return success(path)
    })
    const older = h.run()
    await started
    await h.run({ messages: [] })
    const latest = (await h.status()).automatic
    release(prepared())
    await older
    expect((await h.status()).automatic.stages).toEqual(latest.stages)
    expect((await h.status()).automatic.turn).toBe('2')
  })

  it('evicts old sessions at the documented bound and starts a fresh plugin unobserved', async () => {
    const h = await fixture()
    for (let i = 0; i <= STATUS_SESSION_LIMIT; i++) await h.run({ sessionId: `session-${i}`, messages: [] })
    expect((await h.status('session-0')).automatic.freshness).toBe('not_yet_observed')
    expect((await h.status(`session-${STATUS_SESSION_LIMIT}`)).automatic.stages.capture.code).toBe('no_messages')
    const restarted = await fixture()
    expect((await restarted.status(`session-${STATUS_SESSION_LIMIT}`)).automatic.freshness).toBe('not_yet_observed')
  })
})


vi.mock('../src/client.ts', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../src/client.ts')>()
  const { createClientDouble } = await import('../../../../shared/testing/client.ts')
  const errors = await import('../src/errors.ts')
  const { OPERATIONS } = await import('../src/operations.generated.ts')
  return { ...actual, PowerContextClient: createClientDouble(actual.PowerContextClient, errors, OPERATIONS) }
})
