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

import { createServer, type ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { apply } from '../src/index.ts'
import { PowerContextClient } from '../src/client.ts'
import type { CommandResult } from '../src/commands.ts'
import type { PreStepDecision } from '../src/recall.ts'
import { operationFailure } from '../src/doctor.ts'
import { toToolResult } from '../src/invoke.ts'

vi.mock('../src/peers.ts', () => ({ loadPeer: async (name: string) => name === '@deepseek-ai/dsh-llm'
  ? { createUserMessage: (value: unknown) => value } : { defineTool: (value: unknown) => value } }))

const CAPTURE = '/v1/sources/content'
const FLUSH = '/v1/memory/flush'
const PREPARE = '/v1/context/prepare'
const REQUEST_ID = 'req-review-http'
const PRIVATE = 'private-http-body-marker'
const cleanup: Array<() => Promise<void>> = []

function json(res: ServerResponse, value: unknown, status = 200) {
  res.writeHead(status, { 'Content-Type': 'application/json', 'X-PowerContext-Request-ID': REQUEST_ID })
  res.end(JSON.stringify(value))
}

function fault(status: number, mode: 'complete' | 'stall' | 'disconnect' | 'oversized' = 'complete', requestId = REQUEST_ID) {
  return (res: ServerResponse) => {
    res.writeHead(status, { 'Content-Type': 'application/json', 'X-PowerContext-Request-ID': requestId })
    if (mode === 'complete') return res.end(JSON.stringify({ error: { message: PRIVATE } }))
    res.flushHeaders()
    if (mode === 'oversized') return res.end('x'.repeat(1_048_577))
    res.write('{"error":{"message":"' + PRIVATE)
    if (mode === 'disconnect') setTimeout(() => res.destroy(), 20)
  }
}

async function fixture(target: string, respond: (res: ServerResponse, occurrence: number) => void) {
  const requests: string[] = []
  const logs: Record<string, unknown>[] = []
  let occurrence = 0
  const server = createServer((req, res) => {
    req.resume()
    const path = new URL(req.url!, 'http://localhost').pathname
    requests.push(path)
    if (path === target) { respond(res, ++occurrence); return }
    json(res, path === '/v1/scope-bindings/resolve' ? { scope_id: 'review-scope' }
      : path === PREPARE ? { schema: 'powercontext.prepared-context.v1', status: 'ready', content: 'fixture', content_bytes: 7 }
      : path === CAPTURE ? { position: 2 } : path === FLUSH ? { current_cursor: 2 } : { status: 'ok' }, path === CAPTURE ? 202 : 200)
  })
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  cleanup.push(async () => {
    server.closeAllConnections()
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  })
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('Expected local HTTP address')
  const baseUrl = `http://127.0.0.1:${address.port}`
  await fetch(baseUrl + '/warmup')
  type Hook = (payload: unknown, next: () => Promise<PreStepDecision>) => Promise<PreStepDecision>
  type Command = (invocation: unknown) => Promise<CommandResult>
  let hook!: Hook
  let pc!: Command
  const registry = { register: (definition: { name: string; handler?: Command }) => {
    if (definition.name === 'pc') pc = definition.handler!
  }, section: () => {} }
  await apply({ tools: registry, get: () => registry,
    on: (name: string, listener: Hook) => { if (name === 'agent/pre-step') hook = listener },
    logger: { debug: () => {}, warn: (line: string) => logs.push(JSON.parse(line)) },
  } as unknown as Context, { baseUrl, authorization: 'Bearer http-fixture', timeoutMs: 10000,
    requestTimeoutMs: 250, flushOnCapture: true, flushMaxCalls: 3 })
  const agent = { session: { header: { id: 'http-review', cwd: '/review-workspace' } } }
  const run = (signal = new AbortController().signal) => hook({ agent, signal, turn: 1,
    messages: [{ content: [{ type: 'text', text: 'fixture prompt' }], source: { kind: 'user' } }],
  }, async () => ({ kind: 'enter', messages: [] }))
  const command = (rawInput = '') => pc({ agent, rawInput, signal: new AbortController().signal })
  const status = async () => JSON.parse((await command()).text.split('\nautomatic=')[1])
  return { run, status, command, requests, logs, baseUrl,
    client: new PowerContextClient({ baseUrl, requestTimeoutMs: 250 }) }
}

afterEach(async () => {
  for (const close of cleanup.splice(0)) await close()
  vi.restoreAllMocks()
})

describe('registered /pc with real HTTP response failures', () => {
  it.each([CAPTURE, FLUSH])('distinguishes a complete 401 rejection at %s from an unknown write', async path => {
    const h = await fixture(path, fault(401))
    await h.run()
    const stages = (await h.status()).stages
    expect(stages[path === CAPTURE ? 'capture' : 'flush']).toMatchObject({ state: 'unavailable',
      code: 'authentication_failed', http_status: 401, request_id: REQUEST_ID, confirmation: 'rejected' })
    if (path === CAPTURE) expect(stages.flush.code).toBe('capture_rejected')
    else expect(stages.capture.state).toBe('accepted')
    expect(stages.injection.state).toBe('appended')
  })

  it.each([CAPTURE, FLUSH])('preserves a 401 and request ID when the body stalls at %s', async path => {
    const h = await fixture(path, fault(401, 'stall'))
    const caller = new AbortController()
    await h.run(caller.signal)
    const status = await h.status()
    expect(caller.signal.aborted).toBe(false)
    expect(status.stages[path === CAPTURE ? 'capture' : 'flush']).toMatchObject({ state: 'unavailable',
      code: 'authentication_failed', http_status: 401, request_id: REQUEST_ID, confirmation: 'rejected',
      failure_phase: 'response_body', response_body_error: 'request_timeout' })
    expect(h.logs).toContainEqual(expect.objectContaining({ outcome: 'authentication_failed', http_status: 401,
      request_id: REQUEST_ID, response_body_error: 'request_timeout' }))
    expect(status.stages.injection.state).toBe('appended')
    expect(JSON.stringify([status, h.logs])).not.toContain(PRIVATE)
  })

  it.each(['complete', 'stall'] as const)('reports authorization rejection with a %s 403 body', async mode => {
    const h = await fixture(CAPTURE, fault(403, mode))
    await h.run()
    expect((await h.status()).stages.capture).toMatchObject({ code: 'authorization_failed',
      http_status: 403, confirmation: 'rejected' })
  })

  it('keeps a stalled 202 unconfirmed and never starts flush from incomplete acceptance', async () => {
    const h = await fixture(CAPTURE, fault(202, 'stall'))
    await h.run()
    expect((await h.status()).stages).toMatchObject({ capture: { state: 'unavailable', code: 'request_timeout',
      http_status: 202, request_id: REQUEST_ID, confirmation: 'unconfirmed', response_body_error: 'request_timeout' },
    flush: { state: 'skipped', code: 'capture_not_confirmed' }, injection: { state: 'appended' } })
    expect(h.requests).not.toContain(FLUSH)
  })

  it('keeps a request with no response uncertain without inventing HTTP observations', async () => {
    const h = await fixture(CAPTURE, () => {})
    await h.run()
    const capture = (await h.status()).stages.capture
    expect(capture).toMatchObject({ code: 'request_timeout', confirmation: 'unconfirmed' })
    expect(capture).not.toHaveProperty('http_status')
    expect(capture).not.toHaveProperty('request_id')
    expect(capture).not.toHaveProperty('failure_phase')
  })

  it('does not infer rollback from a complete HTTP 500', async () => {
    const h = await fixture(CAPTURE, fault(500))
    await h.run()
    expect((await h.status()).stages.capture).toMatchObject({ http_status: 500, confirmation: 'unconfirmed' })
  })

  it.each([['disconnect', 'connection_failed'], ['oversized', 'response_too_large']] as const)(
    'retains authentication rejection when the body is %s', async (mode, code) => {
      const h = await fixture(CAPTURE, fault(401, mode))
      await h.run()
      expect((await h.status()).stages.capture).toMatchObject({ code: 'authentication_failed', http_status: 401,
        request_id: REQUEST_ID, confirmation: 'rejected', failure_phase: 'response_body', response_body_error: code })
    })

  it('does not turn an unread 404 body into proof of a missing required route', async () => {
    const h = await fixture(PREPARE, fault(404, 'stall'))
    await h.run()
    expect((await h.status()).stages.prepare).toMatchObject({ code: 'unclassified_not_found', http_status: 404,
      request_id: REQUEST_ID, response_body_error: 'request_timeout' })
    expect(h.logs.every(event => event.outcome !== 'version_mismatch')).toBe(true)
  })

  it('preserves prior capture and processing when a later flush request is rejected', async () => {
    const h = await fixture(FLUSH, (res, occurrence) => occurrence === 1
      ? json(res, { current_cursor: 1 }) : fault(401)(res))
    await h.run()
    const status = await h.status()
    expect(status.stages.capture.state).toBe('accepted')
    expect(status.stages.flush).toMatchObject({ code: 'authentication_failed', confirmation: 'rejected' })
    expect(status.coverage).toContain('earlier capture or flush work is not rolled back')
  })

  it('withholds an unsafe request ID from status and diagnostics after a body timeout', async () => {
    const h = await fixture(CAPTURE, fault(401, 'stall', 'private token=' + PRIVATE))
    await h.run()
    const status = await h.status()
    expect(status.stages.capture).not.toHaveProperty('request_id')
    expect(JSON.stringify([status, h.logs])).not.toContain(PRIVATE)
  })

  it.each([401, 202])('preserves HTTP %s through cancellation with a private reason', async httpStatus => {
    const controller = new AbortController()
    const nativeFetch = globalThis.fetch
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (...args) => {
      const response = await nativeFetch(...args)
      if (new URL(String(args[0])).pathname === CAPTURE) controller.abort(new Error(PRIVATE))
      return response
    })
    const h = await fixture(CAPTURE, fault(httpStatus, 'stall'))
    await h.run(controller.signal)
    const status = await h.status()
    expect(status.stages.capture).toMatchObject({ code: httpStatus === 401 ? 'authentication_failed' : 'cancelled', http_status: httpStatus,
      request_id: REQUEST_ID, confirmation: httpStatus === 401 ? 'rejected' : 'unconfirmed', response_body_error: 'cancelled' })
    expect(JSON.stringify(status)).not.toContain(PRIVATE)
  })

  it('keeps body failure evidence for direct tools and Doctor API discovery', async () => {
    const h = await fixture('/openapi.json', fault(401, 'stall'))
    const error = await h.client.readOpenApi().catch(error => error)
    expect(toToolResult(error)).toMatchObject({ ok: false, code: 'authentication_failed', status: 401,
      request_id: REQUEST_ID, response_body_error: 'request_timeout' })
    expect(operationFailure('openapi_document', error)).toMatchObject({ code: 'authentication_failed',
      http_status: 401, request_id: REQUEST_ID, response_body_error: 'request_timeout' })
    const report = JSON.parse((await h.command('doctor')).text)
    expect(report.checks.routes).toMatchObject({ code: 'authentication_failed', http_status: 401,
      request_id: REQUEST_ID, response_body_error: 'request_timeout' })
  })
})
