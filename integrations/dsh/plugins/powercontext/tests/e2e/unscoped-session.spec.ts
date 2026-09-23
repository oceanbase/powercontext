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

import { afterAll, afterEach, beforeAll, describe, expect, it } from 'vitest'
import { PowerContextClient, type JsonObject } from '../../src/client.ts'
import { registerCommands } from '../../src/commands.ts'
import { resolveConfig } from '../../src/config.ts'
import type { PluginRuntime } from '../../src/invoke.ts'
import { runRecallPreStep } from '../../src/recall.ts'
import { resolveScopeId } from '../../src/scope.ts'
import { registerTools } from '../../src/tools.ts'
import { startPowerContextServer } from '../../scripts/e2e-server.mjs'

let scopeId = ''
const TEXT = 'Optional session cwd must not invent a harness working directory.'

type RecordedCall = { operation: string; body: JsonObject | undefined }

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

class RecordingClient extends PowerContextClient {
  readonly calls: RecordedCall[] = []

  override async request(...args: Parameters<PowerContextClient['request']>) {
    const [operation, body] = args
    this.calls.push({ operation, body })
    return super.request(...args)
  }
}

const clients: RecordingClient[] = []
afterEach(() => { for (const client of clients.splice(0)) client.close() })

function createPluginRuntime(
  baseUrl: string,
  scopeId: string | undefined,
): { runtime: PluginRuntime; events: Record<string, unknown>[]; calls: RecordedCall[] } {
  const events: Record<string, unknown>[] = []
  const config = resolveConfig({
    baseUrl,
    scopeId,
    requestTimeoutMs: 5000,
    timeoutMs: 8000,
    capturePrompts: true,
  }, {})
  const client = new RecordingClient({
    baseUrl: config.baseUrl,
    requestTimeoutMs: config.requestTimeoutMs,
  })
  clients.push(client)
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
    calls: client.calls,
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
    const { runtime, events, calls } = createPluginRuntime(server.baseUrl, undefined)
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
    expect(calls.some((call) => call.operation === 'resolve_scope_binding')).toBe(true)
    expect(calls.every((call) => !String(call.body?.scope_id ?? '').startsWith('local:'))).toBe(true)
  })

  it('uses configured scopeId against a live Server and omits a fabricated cwd', async () => {
    const { runtime, calls } = createPluginRuntime(server.baseUrl, scopeId)
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

    const capture = calls.find((call) => call.operation === 'capture_content_source')
    expect(capture?.body).toMatchObject({
      scope_id: scopeId,
      metadata: { origin: 'dsh', event: 'user_prompt_submit', session_id: 'session-unscoped' },
    })
    expect((capture?.body?.metadata as { cwd?: string } | undefined)?.cwd).toBeUndefined()
    expect(calls.every((call) => !String(call.body?.scope_id ?? '').startsWith('local:'))).toBe(true)
  })

  it('keeps diagnostics usable when an explicit Scope does not exist', async () => {
    const { runtime, calls } = createPluginRuntime(server.baseUrl, 'scp_00000000000000000000000000')
    const command = pcHandler(runtime)
    const invocation = () => ({ signal: AbortSignal.timeout(5000), agent: sessionWithoutCwd() })

    const remembered = await toolNamed(runtime, 'pc_remember').execute({ kind: 'agent-note', text: TEXT }, invocation())
    expect(remembered).toMatchObject({ ok: false, code: 'not_found', error_code: 'scope_not_found', status: 404 })
    const searched = await command({ ...invocation(), rawInput: 'search optional cwd' })
    expect(searched.kind).toBe('error')
    expect(JSON.parse(searched.text)).toMatchObject({ code: 'not_found', error_code: 'scope_not_found' })

    const status = await command({ ...invocation(), rawInput: '' })
    expect(status.kind).toBe('error')
    expect(status.text).toContain('scope=unresolved')
    const doctor = await command({ ...invocation(), rawInput: 'doctor' })
    expect(doctor.kind).toBe('success')
    expect(JSON.parse(doctor.text).checks).toMatchObject({
      liveness: { status: 'ok' }, readiness: { status: 'ok' }, capabilities: { status: 'ok' },
    })
    expect((await command({ ...invocation(), rawInput: 'capabilities' })).kind).toBe('success')
    expect(calls.some(call => call.operation === 'prepare_context')).toBe(false)
    expect(calls.some(call => call.operation === 'capture_content_source')).toBe(false)
  })
})
