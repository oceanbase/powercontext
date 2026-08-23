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
import powercontextPi from '../extensions/powercontext.ts'
import { PowerContextClient, type FetchFn } from '../src/client.ts'
import { registerTools } from '../src/tools.ts'
import type { PluginRuntime } from '../src/recall.ts'

function createRuntime(fetch: FetchFn): PluginRuntime {
  return {
    client: new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch,
    }),
    config: {
      baseUrl: 'http://127.0.0.1:8000',
      scopeId: undefined,
      authorization: undefined,
      capturePrompts: true,
      requestTimeoutMs: 1000,
      httpBudgetMs: 4000,
      maxBytes: 8000,
      flushOnCapture: false,
      flushMaxCalls: 4,
    },
    resolveScope: async () => 'project:demo',
  } as PluginRuntime
}

type RegisteredTool<Params> = {
  execute: (
    id: string,
    params: Params,
    signal: AbortSignal,
    update: () => void,
    context: Record<string, unknown>,
  ) => Promise<{ details: { code?: string; data?: unknown; ok: boolean } }>
}

function registeredTool<Params>(tools: Array<Record<string, unknown>>, name: string): RegisteredTool<Params> {
  const tool = tools.find((candidate) => candidate.name === name)
  if (!tool) throw new Error(`Expected ${name} to be registered.`)
  return tool as unknown as RegisteredTool<Params>
}

describe('Pi native tool surface', () => {
  it('registers the explicit Memory and Handoff tools', () => {
    const tools: Array<{ name: string }> = []
    powercontextPi({
      on: vi.fn(),
      registerCommand: vi.fn(),
      registerTool: (tool: { name: string }) => tools.push(tool),
    } as never)

    expect(new Set(tools.map((tool) => tool.name))).toEqual(new Set([
      'pc_search',
      'pc_remember',
      'pc_memory_list',
      'pc_memory_get',
      'pc_memory_revise',
      'pc_memory_retire',
      'pc_prepare_context',
      'pc_capture_source',
      'pc_handoff_activate',
      'pc_handoff_prepare',
      'pc_handoff_finalize',
      'pc_handoff_commit',
      'pc_handoff_continue',
    ]))
    expect(tools.map((tool) => tool.name)).not.toContain('pc_call')
  })

  it('requires confirmation for explicit durable writes and refuses them without a UI', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => (
      new Response(JSON.stringify({ entry: { text: 'remembered' } }))
    ))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const remember = registeredTool<{ kind: string; text: string }>(registered, 'pc_remember')

    const denied = await remember.execute('call-1', {
      kind: 'agent-note',
      text: 'keep API async',
    }, new AbortController().signal, () => undefined, {
      cwd: '/workspace/repo',
      hasUI: false,
      ui: { confirm: vi.fn() },
    })
    expect(denied.details).toMatchObject({ ok: false, code: 'confirmation_required' })
    expect(fetch).not.toHaveBeenCalled()

    const confirm = vi.fn(async () => true)
    const approved = await remember.execute('call-2', {
      kind: 'agent-note',
      text: 'keep API async',
    }, new AbortController().signal, () => undefined, {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: { confirm },
    })
    expect(approved.details).toMatchObject({ ok: true })
    expect(confirm).toHaveBeenCalled()
    const init = fetch.mock.calls[0]?.[1]
    expect(JSON.parse(String(init?.body))).toEqual({
      kind: 'agent-note',
      text: 'keep API async',
      scope_id: 'project:demo',
    })
  })

  it('registers the /pc status and diagnostic command', () => {
    const registerCommand = vi.fn()
    powercontextPi({
      on: vi.fn(),
      registerTool: vi.fn(),
      registerCommand,
    } as never)

    expect(registerCommand).toHaveBeenCalledWith('pc', expect.objectContaining({
      description: expect.stringContaining('PowerContext'),
      handler: expect.any(Function),
    }))
  })
})
