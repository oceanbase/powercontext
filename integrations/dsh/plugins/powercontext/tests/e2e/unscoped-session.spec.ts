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

import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { PowerContextClient, type FetchFn, type JsonObject } from '../../src/client.ts'
import { registerCommands } from '../../src/commands.ts'
import { resolveConfig } from '../../src/config.ts'
import type { PluginRuntime } from '../../src/invoke.ts'
import { runRecallPreStep } from '../../src/recall.ts'
import { registerMcpPolicy } from '../../src/mcp.ts'
import { resolveScopeId } from '../../src/scope.ts'
import { startPowerContextServer } from '../../scripts/e2e-server.mjs'

let scopeId = ''
const TEXT = 'Optional session cwd must not invent a harness working directory.'

type RecordedCall = { path: string; body: JsonObject | undefined }

type PcHandler = (invocation: {
  rawInput: string
  signal: AbortSignal
  agent: { session: { header: { cwd?: string } } }
}) => Promise<{ kind: string; text: string }>

function sessionWithoutCwd() {
  return { session: { header: { id: 'session-unscoped', cwd: undefined } } }
}

function parseBody(body: BodyInit | null | undefined): JsonObject | undefined {
  if (typeof body !== 'string') return undefined
  try {
    const value = JSON.parse(body) as unknown
    return value && typeof value === 'object' && !Array.isArray(value)
      ? value as JsonObject
      : undefined
  } catch {
    return undefined
  }
}

function trackingFetch(): { fetchImpl: FetchFn; calls: RecordedCall[] } {
  const calls: RecordedCall[] = []
  const fetchImpl: FetchFn = async (input, init) => {
    calls.push({
      path: new URL(input).pathname,
      body: parseBody(init.body),
    })
    return fetch(input, init)
  }
  return { fetchImpl, calls }
}

function createPluginRuntime(
  baseUrl: string,
  scopeId: string | undefined,
  fetchImpl: FetchFn,
): { runtime: PluginRuntime; events: Record<string, unknown>[] } {
  const events: Record<string, unknown>[] = []
  const config = resolveConfig({
    baseUrl,
    scopeId,
    requestTimeoutMs: 5000,
    timeoutMs: 8000,
    capturePrompts: true,
  }, {})
  const client = new PowerContextClient({
        baseUrl: config.baseUrl,
        requestTimeoutMs: config.requestTimeoutMs,
        fetch: fetchImpl,
      })
  return {
    runtime: {
      client,
      config,
      resolveScope: (cwd, signal) => resolveScopeId(client, cwd, config.scopeId, signal),
      log: (event) => {
        events.push(event)
      },
    },
    events,
  }
}

function pcHandler(runtime: PluginRuntime): PcHandler {
  let handler: PcHandler | undefined
  registerCommands({
    get: (name) => name === 'commands'
      ? { register: (definition: { handler: PcHandler }) => { handler = definition.handler } }
      : undefined,
  }, runtime)
  if (!handler) throw new Error('expected /pc handler')
  return handler
}

function mcpPolicy(runtime: PluginRuntime) {
  type Hook = (exec: unknown, next: () => Promise<{ kind: 'allow' }>) => Promise<{ kind: string; reason?: string }>
  let handler: Hook | undefined
  registerMcpPolicy({ on: (_event, hook) => { handler = hook as unknown as Hook } },
    ({ cwd, signal }) => runtime.resolveScope(cwd, signal))
  if (!handler) throw new Error('expected native MCP policy')
  return (operation: string, args: JsonObject) => handler!({
    name: `mcp__powercontext__${operation}`, arguments: args, agent: sessionWithoutCwd(),
    signal: AbortSignal.timeout(5000),
  }, async () => ({ kind: 'allow' }))
}

// Exercise the live Server's public MCP wire contract without a dependency on the separate DSH runtime package.
async function mcpSearch(baseUrl: string, args: JsonObject): Promise<unknown> {
  let session = ''
  const request = async (id: number, method: string, params: unknown) => {
    const response = await fetch(`${baseUrl}/mcp/`, {
      method: 'POST', signal: AbortSignal.timeout(5000),
      headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
        ...(session ? { 'Mcp-Session-Id': session, 'Mcp-Protocol-Version': '2025-03-26' } : {}) },
      body: JSON.stringify({ jsonrpc: '2.0', id, method, params }),
    })
    expect(response.ok).toBe(true)
    session = response.headers.get('mcp-session-id') ?? session
    const text = await response.text()
    const messages = response.headers.get('content-type')?.includes('text/event-stream')
      ? text.split('\n').filter(line => line.startsWith('data: ')).map(line => JSON.parse(line.slice(6)))
      : [JSON.parse(text)]
    return messages.find(message => message.id === id).result
  }
  try {
    await request(1, 'initialize', {
      protocolVersion: '2025-03-26', capabilities: {}, clientInfo: { name: 'dsh-live-regression', version: '1' },
    })
    const result = await request(2, 'tools/call', { name: 'search_memory', arguments: args })
    expect(result.isError).not.toBe(true)
    return result.structuredContent ?? JSON.parse(result.content.find((block: { type: string }) => block.type === 'text').text)
  } finally {
    if (session) await fetch(`${baseUrl}/mcp/`, { method: 'DELETE', headers: { 'Mcp-Session-Id': session }, signal: AbortSignal.timeout(5000) })
  }
}

async function recallWithoutCwd(runtime: PluginRuntime, query: string) {
  const messages = [{ content: [{ type: 'text', text: query }], source: { kind: 'user' as const } }]
  const next = async () => ({ kind: 'enter' as const, messages })
  return runRecallPreStep({
    messages,
    next,
    cwd: undefined,
    sessionId: 'session-unscoped',
    turnId: '1',
    client: runtime.client,
    config: runtime.config,
    resolveScope: runtime.resolveScope,
    wrapContent: (text) => ({ role: 'user', content: [{ type: 'text', text }] }),
    log: runtime.log,
  })
}

describe('plugin runtime with header.cwd === undefined', () => {
  let server: Awaited<ReturnType<typeof startPowerContextServer>>

  beforeAll(async () => {
    server = await startPowerContextServer()
    const response = await fetch(`${server.baseUrl}/v1/scopes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        title: 'DSH E2E Scope',
        summary: 'Scope used by the DSH binding integration test.',
        idempotency_key: 'dsh-e2e-scope-binding',
      }),
    })
    scopeId = String((await response.json() as { scope_id: string }).scope_id)
  }, 60_000)

  afterAll(async () => {
    await server?.stop()
  })

  it('uses the Server default without treating the process directory as a Scope', async () => {
    const { fetchImpl, calls } = trackingFetch()
    const { runtime, events } = createPluginRuntime(server.baseUrl, undefined, fetchImpl)
    const command = pcHandler(runtime)

    const recalled = await recallWithoutCwd(runtime, TEXT)
    const pc = await command({
      rawInput: 'search optional cwd',
      signal: AbortSignal.timeout(5000),
      agent: sessionWithoutCwd(),
    })

    expect(recalled).toEqual({
      kind: 'enter',
      messages: [{ content: [{ type: 'text', text: TEXT }], source: { kind: 'user' } }],
    })
    expect(events.some((event) => event.event === 'context_prepare')).toBe(true)
    expect(pc.kind).toBe('success')
    expect(await runtime.resolveScope(undefined)).toMatch(/^scp_/)
    const search = { scope_id: await runtime.resolveScope(undefined), query: 'optional cwd' }
    expect(await mcpPolicy(runtime)('search_memory', search)).toEqual({ kind: 'allow' })
    expect(await mcpSearch(server.baseUrl, search)).toMatchObject({ hits: expect.any(Array) })
    expect(calls.some((call) => call.path === '/v1/scope-bindings/resolve')).toBe(true)
    expect(calls.every((call) => !String(call.body?.scope_id ?? '').startsWith('local:'))).toBe(true)
  })

  it('uses configured scopeId against a live Server and omits a fabricated cwd', async () => {
    const { fetchImpl, calls } = trackingFetch()
    const { runtime } = createPluginRuntime(server.baseUrl, scopeId, fetchImpl)
    const command = pcHandler(runtime)

    const remembered = await command({
      rawInput: `remember ${TEXT}`,
      signal: AbortSignal.timeout(5000),
      agent: sessionWithoutCwd(),
    })
    expect(remembered.kind).toBe('success')
    const search = { scope_id: scopeId, query: TEXT }
    expect(await mcpPolicy(runtime)('search_memory', search)).toEqual({ kind: 'allow' })
    expect(await mcpSearch(server.baseUrl, search)).toMatchObject({ hits: expect.arrayContaining([
      expect.objectContaining({ text: TEXT }),
    ]) })

    const recalled = await recallWithoutCwd(runtime, 'optional cwd harness working directory')
    expect(recalled.kind).toBe('enter')

    const capture = calls.find((call) => call.path === '/v1/sources/content')
    expect(capture?.body).toMatchObject({
      scope_id: scopeId,
      metadata: { origin: 'dsh', event: 'user_prompt_submit', session_id: 'session-unscoped' },
    })
    expect((capture?.body?.metadata as { cwd?: string } | undefined)?.cwd).toBeUndefined()
    expect(calls.every((call) => !String(call.body?.scope_id ?? '').startsWith('local:'))).toBe(true)
  })

  it('keeps diagnostics usable when an explicit Scope does not exist', async () => {
    const { fetchImpl, calls } = trackingFetch()
    const { runtime } = createPluginRuntime(server.baseUrl, 'scp_00000000000000000000000000', fetchImpl)
    const command = pcHandler(runtime)
    const invocation = () => ({ signal: AbortSignal.timeout(5000), agent: sessionWithoutCwd() })

    const searched = await command({ ...invocation(), rawInput: 'search optional cwd' })
    expect(searched.kind).toBe('error')
    expect(JSON.parse(searched.text)).toMatchObject({ code: 'not_found', error_code: 'scope_not_found', status: 404 })
    const denied = await mcpPolicy(runtime)('remember_memory', { scope_id: scopeId, text: TEXT })
    expect(denied).toMatchObject({ kind: 'deny', reason: expect.stringContaining('host Scope could not be resolved') })
    expect(denied.reason).toContain('No MCP request was sent')
    expect(calls.some(call => call.path.startsWith('/mcp') || call.path === '/v1/memory/remember')).toBe(false)

    const status = await command({ ...invocation(), rawInput: '' })
    expect(status.kind).toBe('error')
    expect(status.text).toContain('scope=unresolved')
    const doctor = await command({ ...invocation(), rawInput: 'doctor' })
    expect(doctor.kind).toBe('error')
    expect(JSON.parse(doctor.text).checks).toMatchObject({
      liveness: { state: 'ok' }, readiness: { state: 'ok' },
      scope: { state: 'failed', code: 'scope_not_found' },
      prepare: { state: 'skipped', code: 'scope_unavailable' },
    })
    expect((await command({ ...invocation(), rawInput: 'capabilities' })).kind).toBe('success')
    expect(calls.some(call => call.path === '/v1/context/prepare')).toBe(false)
    expect(calls.some(call => call.path === '/v1/sources/content')).toBe(false)
  })
})
