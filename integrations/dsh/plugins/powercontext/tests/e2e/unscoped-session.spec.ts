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
import { resolveScopeId } from '../../src/scope.ts'
import { registerTools } from '../../src/tools.ts'
import { startPowerContextServer } from '../../scripts/e2e-server.mjs'

let scopeId = ''
const TEXT = 'Optional session cwd must not invent a harness working directory.'

type RecordedCall = { path: string; body: JsonObject | undefined }

type PcHandler = (invocation: {
  rawInput: string
  signal: AbortSignal
  agent: { session: { header: { cwd?: string } } }
}) => Promise<{ kind: string; text: string }>

type RegisteredTool = {
  name: string
  execute: (args: Record<string, unknown>, exec: unknown) => Promise<unknown>
}

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
      resolveScope: (cwd) => resolveScopeId(client, cwd, config.scopeId),
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

function toolNamed(runtime: PluginRuntime, name: string): RegisteredTool {
  const registered: RegisteredTool[] = []
  registerTools(
    {
      tools: { register: (tool) => registered.push(tool as RegisteredTool) },
      on: () => undefined,
    },
    runtime,
    (definition) => definition,
  )
  const tool = registered.find((entry) => entry.name === name)
  if (!tool) throw new Error(`expected tool ${name}`)
  return tool
}

async function recallWithoutCwd(runtime: PluginRuntime, query: string) {
  const next = async () => ({ kind: 'enter' as const, messages: [] })
  return runRecallPreStep({
    messages: [{ content: [{ type: 'text', text: query }], source: { kind: 'user' } }],
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
    const search = toolNamed(runtime, 'pc_search')

    const recalled = await recallWithoutCwd(runtime, TEXT)
    const pc = await command({
      rawInput: 'search optional cwd',
      signal: AbortSignal.timeout(5000),
      agent: sessionWithoutCwd(),
    })
    const tool = await search.execute({ query: 'optional cwd' }, {
      signal: AbortSignal.timeout(5000),
      agent: sessionWithoutCwd(),
    })

    expect(recalled).toEqual({ kind: 'enter', messages: [] })
    expect(events.some((event) => event.event === 'context_prepare')).toBe(true)
    expect(pc.kind).toBe('success')
    expect(tool).toMatchObject({ ok: true })
    expect(await runtime.resolveScope(undefined)).toMatch(/^scp_/)
    expect(calls.some((call) => call.path === '/v1/scope-bindings/resolve')).toBe(true)
    expect(calls.every((call) => !String(call.body?.scope_id ?? '').startsWith('local:'))).toBe(true)
  })

  it('uses configured scopeId against a live Server and omits a fabricated cwd', async () => {
    const { fetchImpl, calls } = trackingFetch()
    const { runtime } = createPluginRuntime(server.baseUrl, scopeId, fetchImpl)
    const command = pcHandler(runtime)
    const search = toolNamed(runtime, 'pc_search')

    const remembered = await command({
      rawInput: `remember ${TEXT}`,
      signal: AbortSignal.timeout(5000),
      agent: sessionWithoutCwd(),
    })
    expect(remembered.kind).toBe('success')

    const found = await search.execute({ query: 'optional cwd harness working directory' }, {
      signal: AbortSignal.timeout(5000),
      agent: sessionWithoutCwd(),
    }) as { ok: boolean; data?: { hits?: Array<{ text?: string }> } }
    expect(found.ok).toBe(true)
    expect(found.data?.hits?.some((hit) => hit.text === TEXT)).toBe(true)

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
})
