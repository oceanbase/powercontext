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

import { createServer, type RequestListener } from 'node:http'
import { afterEach, expect, it } from 'vitest'
import { protectMcpEndpoint } from '../src/mcp-transport.ts'

const cleanups: Array<() => Promise<void>> = []
afterEach(async () => { for (const close of cleanups.splice(0).reverse()) await close() })

async function endpoint(handler: RequestListener): Promise<string> {
  const server = createServer(handler)
  await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
  cleanups.push(async () => {
    server.closeAllConnections()
    await new Promise<void>(resolve => server.close(() => resolve()))
  })
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('fixture has no address')
  return `http://127.0.0.1:${address.port}/mcp/`
}

async function protectedEndpoint(target: string): Promise<string> {
  return protectMcpEndpoint({ effect: (start: () => () => Promise<void>) => {
    cleanups.push(start())
  } } as never, target)
}

it.each([301, 302, 303, 307, 308])('refuses MCP redirects (%s) without forwarding Scope, query or authorization', async status => {
  const received: string[] = []
  const redirected = await endpoint((req, res) => { received.push(req.url!); res.end('unexpected') })
  let sentBody = ''
  const configured = await endpoint(async (req, res) => {
    for await (const chunk of req) sentBody += chunk.toString()
    res.writeHead(status, { Location: redirected }).end()
  })
  const gateway = await protectedEndpoint(configured)
  const payload = { jsonrpc: '2.0', id: 1, method: 'tools/call',
    params: { name: 'search_memory', arguments: { scope_id: 'scope-a', query: 'private-query-fixture' } } }
  const response = await fetch(gateway, { method: 'POST', headers: { Authorization: 'Bearer fixture-only' },
    body: JSON.stringify(payload) })
  expect(response.status).toBe(502)
  expect(response.headers.has('location')).toBe(false)
  expect(await response.json()).toMatchObject({ error: { code: 'redirect_rejected' } })
  expect(JSON.parse(sentBody)).toEqual(payload)
  expect(received).toEqual([])
})

it.each([
  [401, 'authentication_failed'],
  [404, 'not_found'],
  [503, 'unavailable'],
] as const)('replaces HTTP %s diagnostics with controlled status and code', async (status, code) => {
  const marker = 'private-response-marker: api_key=FAKE_NATIVE_ERROR_CANARY'
  const configured = await endpoint((_req, res) => {
    res.writeHead(status, 'private-reason-phrase', {
      'Content-Type': 'text/html', 'X-Private-Diagnostic': marker, 'Mcp-Session-Id': marker,
    })
    res.end(`<html>${marker}</html>`)
  })
  const response = await fetch(await protectedEndpoint(configured), { method: 'POST', body: '{}' })
  expect(response.status).toBe(status)
  expect(response.statusText).not.toContain('private-reason-phrase')
  expect(response.headers.get('content-type')).toBe('application/json')
  expect(response.headers.has('x-private-diagnostic')).toBe(false)
  expect(response.headers.has('mcp-session-id')).toBe(false)
  const body = await response.text()
  expect(JSON.parse(body)).toMatchObject({ error: { code, status } })
  expect(body).not.toContain('private-response-marker')
  expect(body).not.toContain('FAKE_NATIVE_ERROR_CANARY')
})

it('rejects a stalled HTTP error body without waiting for or forwarding its diagnostics', async () => {
  let closed!: () => void
  const disconnected = new Promise<void>(resolve => { closed = resolve })
  const configured = await endpoint((_req, res) => {
    res.once('close', closed)
    res.writeHead(503, { 'Content-Type': 'application/json' })
    res.flushHeaders()
    res.write('{"error":{"message":"api_key=FAKE_NATIVE_ERROR_CANARY')
  })
  const response = await fetch(await protectedEndpoint(configured), { signal: AbortSignal.timeout(1000) })
  expect(response.status).toBe(503)
  expect(await response.json()).toMatchObject({ error: { code: 'unavailable', status: 503 } })
  await disconnected
})

it('preserves native MCP SSE frames, session headers and authorization at the configured endpoint', async () => {
  const frames = 'event: message\ndata: {"jsonrpc":"2.0","id":1,"result":{"tools":[]}}\n\n'
  let authorization: string | undefined
  let session: string | undefined
  const configured = await endpoint((req, res) => {
    authorization = req.headers.authorization
    session = req.headers['mcp-session-id'] as string
    res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Mcp-Session-Id': 'session-response' })
    res.end(frames)
  })
  const response = await fetch(await protectedEndpoint(configured), {
    headers: { Authorization: 'Bearer fixture-only', 'Mcp-Session-Id': 'session-request' },
  })
  expect(await response.text()).toBe(frames)
  expect(response.headers.get('mcp-session-id')).toBe('session-response')
  expect(authorization).toBe('Bearer fixture-only')
  expect(session).toBe('session-request')
})

it('closes a pending upstream MCP connection when the host disposes the boundary', async () => {
  let started!: () => void
  let closed!: () => void
  const pending = new Promise<void>(resolve => { started = resolve })
  const disconnected = new Promise<void>(resolve => { closed = resolve })
  const configured = await endpoint((_req, res) => {
    res.on('close', closed)
    started()
  })
  const gateway = await protectedEndpoint(configured)
  const response = fetch(gateway).catch(() => undefined)
  await pending
  await cleanups.pop()!()
  await disconnected
  expect(await response).toBeUndefined()
  await expect(fetch(gateway)).rejects.toThrow()
})
