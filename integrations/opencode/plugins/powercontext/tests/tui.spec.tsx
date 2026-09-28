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

import { mkdtemp, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

import { createRoot } from 'solid-js'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { formatPowerContextStatus, loadStatuslineStatus, PowerContextTuiPlugin, withTimeout } from '../src/tui.tsx'

const tuiNodes = vi.hoisted(() => ({ children: new WeakMap<object, unknown[]>() }))

vi.mock('@opentui/solid', () => ({
  createElement: () => ({}),
  setProp: () => undefined,
  insert: (node: object, child: unknown) => {
    const children = tuiNodes.children.get(node) ?? []
    children.push(child)
    tuiNodes.children.set(node, children)
  },
}))

function flattenTui(node: unknown): string {
  if (typeof node === 'string') return node
  if (Array.isArray(node)) return node.map(flattenTui).join('')
  if (!node || typeof node !== 'object') return ''
  const children = tuiNodes.children.get(node) ?? []
  return children.map((child) => (typeof child === 'function' ? flattenTui(child()) : flattenTui(child))).join('')
}

afterEach(() => {
  delete process.env.POWERCONTEXT_OPENCODE_SCOPE_ID
  delete process.env.POWERCONTEXT_OPENCODE_BASE_URL
  delete process.env.POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP
  delete process.env.POWERCONTEXT_CLIENT_CONFIG_FILE
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

function statsWindow(reduction: number) {
  return {
    recall: {
      totals: {
        preparations: 5,
        ready_preparations: 4,
        comparable_preparations: 3,
        baseline_tokens: 3_000,
        recalled_tokens: 1_800,
        token_reduction: reduction,
      },
    },
  }
}

function tuiApi(overrides: Record<string, unknown> = {}): any {
  return {
    keymap: { registerLayer: () => () => undefined },
    slots: { register: () => 'powercontext-statusline' },
    lifecycle: { signal: new AbortController().signal, onDispose: () => () => undefined },
    route: { current: { name: 'session', params: { sessionID: 'session-1' } } },
    state: {
      path: { directory: '/tmp/startup' },
      session: { get: () => ({ directory: '/tmp/project' }) },
    },
    theme: { current: { success: 'green', error: 'red', textMuted: 'muted' } },
    ui: {
      DialogPrompt: (props: unknown) => ({ kind: 'prompt', props }),
      DialogAlert: (props: unknown) => ({ kind: 'alert', props }),
      toast: vi.fn(),
      dialog: { replace: vi.fn(), clear: vi.fn(), setSize: vi.fn() },
    },
    ...overrides,
  }
}

describe('PowerContextTuiPlugin', () => {
  it('registers /pc and renders the DSH-compatible command result', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const calls: Array<{ url: string; body: any }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) })
      if (url.endsWith('/v1/scope-bindings/resolve')) {
        return Response.json({ scope_id: 'project:test' })
      }
      return Response.json({}, { status: 404 })
    }))
    const layers: any[] = []
    const slotPlugins: any[] = []
    let rendered: any
    const dialog = {
      replace: (render: () => unknown) => { rendered = render() },
      clear: () => { rendered = undefined },
      setSize: vi.fn(),
      size: 'medium',
      depth: 0,
      open: false,
    }
    const api = {
      keymap: {
        registerLayer: (layer: unknown) => {
          layers.push(layer)
          return () => undefined
        },
      },
      slots: {
        register: (plugin: unknown) => {
          slotPlugins.push(plugin)
          return 'powercontext-statusline'
        },
      },
      lifecycle: { signal: new AbortController().signal },
      route: { current: { name: 'session', params: { sessionID: 'session-1' } } },
      state: {
        path: { directory: '/tmp/startup' },
        session: { get: () => ({ directory: '/tmp/project' }) },
      },
      ui: {
        DialogPrompt: (props: unknown) => ({ kind: 'prompt', props }),
        DialogAlert: (props: unknown) => ({ kind: 'alert', props }),
        toast: vi.fn(),
        dialog,
      },
    } as any

    await PowerContextTuiPlugin(api, undefined, {} as any)

    const command = layers[0]?.commands[0]
    expect(command).toMatchObject({
      name: 'powercontext.pc',
      namespace: 'palette',
      slashName: 'pc',
      slashAliases: ['powercontext'],
    })
    expect(slotPlugins[0]?.slots.session_prompt_right).toBeTypeOf('function')
    command.run()
    expect(rendered.kind).toBe('prompt')
    rendered.props.onConfirm('')
    await vi.waitFor(() => expect(rendered.kind).toBe('alert'))
    expect(rendered.props.message).toContain('scope=project:test')
    const resolveCalls = calls.filter((call) => call.url.endsWith('/v1/scope-bindings/resolve'))
    expect(resolveCalls.length).toBeGreaterThan(0)
    expect(resolveCalls[0]?.body.explicit_scope_id).toBe('project:test')
    expect(resolveCalls[0]?.body.binding_keys[0]).toEqual({
      integration: 'opencode',
      kind: 'session',
      external_id: 'session-1',
    })
    expect(resolveCalls[0]?.body.binding_keys[1]).toMatchObject({ integration: 'opencode', kind: 'workspace' })
    expect(dialog.setSize).toHaveBeenCalledWith('large')
  })

  it('loads statusline savings through the contract stats endpoint', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const calls: Array<{ url: string; body: any }> = []
    const statsBody = {
      recall: {
        totals: {
          preparations: 5,
          ready_preparations: 4,
          comparable_preparations: 3,
          baseline_tokens: 3_000,
          recalled_tokens: 1_800,
          token_reduction: 1_200,
        },
      },
    }
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) })
      if (url.endsWith('/v1/scope-bindings/resolve')) return Response.json({ scope_id: 'project:test' })
      if (url.endsWith('/v1/stats')) return Response.json(statsBody)
      return Response.json({}, { status: 404 })
    }))
    const { PowerContextClient } = await import('../src/client.ts')
    const { resolveConfig } = await import('../src/config.ts')
    const runtime = {
      config: resolveConfig(),
      client: new PowerContextClient({ baseUrl: 'http://127.0.0.1:9', requestTimeoutMs: 1_000 }),
    }

    const state = await loadStatuslineStatus(runtime as any, 'session-1', '/tmp/project')

    expect(state.connected).toBe(true)
    expect(state.label).toBe('PC online · saved 1.2k today · saved 1.2k in 30d')
    const statsCalls = calls.filter((call) => call.url.endsWith('/v1/stats'))
    expect(statsCalls).toHaveLength(2)
    for (const call of statsCalls) {
      expect(call.body.selection).toEqual({ mode: 'exact', scope_ids: ['project:test'] })
    }
    expect(statsCalls.map((call) => call.body.period)).toEqual(expect.arrayContaining(['today', '30d']))
  })

  it('reports today and 30-day savings with honest cost wording', () => {
    const saved = {
      recall: {
        totals: {
          preparations: 5,
          ready_preparations: 4,
          comparable_preparations: 3,
          baseline_tokens: 3_000,
          recalled_tokens: 1_800,
          token_reduction: 1_200,
        },
      },
    }
    const cost = {
      recall: {
        totals: {
          preparations: 2,
          ready_preparations: 2,
          comparable_preparations: 1,
          baseline_tokens: 1_000,
          recalled_tokens: 1_250,
          token_reduction: -250,
        },
      },
    }
    expect(formatPowerContextStatus(saved, saved)).toBe('PC online · saved 1.2k today · saved 1.2k in 30d')
    expect(formatPowerContextStatus(cost, saved)).toBe('PC online · cost 250 today · saved 1.2k in 30d')
    expect(formatPowerContextStatus({}, {})).toBe('PC online · no data today · no data in 30d')
  })

  it('turns a stalled status request into an offline result', async () => {
    await expect(withTimeout(new Promise<never>(() => {}), 5)).rejects.toThrow('timed out')
    await expect(withTimeout(Promise.resolve('ok'), 5)).resolves.toBe('ok')
  })

  it('activates with an environment-approved remote HTTP endpoint', async () => {
    process.env.POWERCONTEXT_OPENCODE_BASE_URL = 'http://192.0.2.10:8000'
    process.env.POWERCONTEXT_OPENCODE_ALLOW_INSECURE_HTTP = 'true'

    await expect(PowerContextTuiPlugin(tuiApi(), undefined, {} as any)).resolves.toBeUndefined()
  })

  it('activates with a setup-saved insecure HTTP consent', async () => {
    const directory = await mkdtemp(join(tmpdir(), 'pc-tui-'))
    const configPath = join(directory, 'clients.json')
    await writeFile(configPath, JSON.stringify({
      version: 1,
      hosts: { opencode: { server_url: 'http://192.0.2.11:8000', allow_insecure_http: true } },
    }))
    process.env.POWERCONTEXT_CLIENT_CONFIG_FILE = configPath

    await expect(PowerContextTuiPlugin(tuiApi(), undefined, {} as any)).resolves.toBeUndefined()
  })

  it('keeps authentication, version, transport, and protocol failures distinct', async () => {
    const { PowerContextClient } = await import('../src/client.ts')
    const cases: Array<[number, string]> = [
      [401, 'PC auth failed'],
      [404, 'PC version mismatch'],
      [503, 'PC offline · run powercontext doctor'],
      [500, 'PC invalid response'],
    ]
    for (const [status, label] of cases) {
      const client = new PowerContextClient({
        baseUrl: 'http://127.0.0.1:9',
        requestTimeoutMs: 1_000,
        fetch: async (url: string) => (url.endsWith('/v1/scope-bindings/resolve')
          ? Response.json({ scope_id: 'project:test' })
          : Response.json({ error: { code: 'failure' } }, { status })),
      })
      const runtime = { config: { scopeId: 'project:test' }, client }

      const state = await loadStatuslineStatus(runtime as any, 'session-1', '/tmp/project')

      expect(state.connected).toBe(false)
      expect(state.label).toBe(label)
    }
  })

  it('rejects a malformed stats payload instead of rendering no data', async () => {
    const { PowerContextClient } = await import('../src/client.ts')
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:9',
      requestTimeoutMs: 1_000,
      fetch: async (url: string) => (url.endsWith('/v1/scope-bindings/resolve')
        ? Response.json({ scope_id: 'project:test' })
        : Response.json({})),
    })
    const runtime = { config: { scopeId: 'project:test' }, client }

    const state = await loadStatuslineStatus(runtime as any, 'session-1', '/tmp/project')

    expect(state.connected).toBe(false)
    expect(state.label).toBe('PC invalid response')
  })

  it('keeps a legitimate zero-statistics window online', async () => {
    const { PowerContextClient } = await import('../src/client.ts')
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:9',
      requestTimeoutMs: 1_000,
      fetch: async (url: string) => (url.endsWith('/v1/scope-bindings/resolve')
        ? Response.json({ scope_id: 'project:test' })
        : Response.json(statsWindow(0))),
    })
    const runtime = { config: { scopeId: 'project:test' }, client }

    const state = await loadStatuslineStatus(runtime as any, 'session-1', '/tmp/project')

    expect(state.connected).toBe(true)
    expect(state.label).toBe('PC online · saved 0 today · saved 0 in 30d')
  })

  it('classifies a malformed non-JSON stats response as an invalid response', async () => {
    const { PowerContextClient } = await import('../src/client.ts')
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:9',
      requestTimeoutMs: 1_000,
      fetch: async (url: string) => (url.endsWith('/v1/scope-bindings/resolve')
        ? Response.json({ scope_id: 'project:test' })
        : new Response('not-json', { status: 200 })),
    })
    const runtime = { config: { scopeId: 'project:test' }, client }

    const state = await loadStatuslineStatus(runtime as any, 'session-1', '/tmp/project')

    expect(state.connected).toBe(false)
    expect(state.label).toBe('PC invalid response')
  })

  it('renders the same two-window wording as the tested formatter', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/v1/scope-bindings/resolve')) return Response.json({ scope_id: 'project:test' })
      const body = JSON.parse(String(init.body))
      return Response.json(body.period === 'today' ? statsWindow(1_200) : statsWindow(-250))
    }))
    const slotPlugins: any[] = []
    const api = tuiApi({ slots: { register: (plugin: unknown) => { slotPlugins.push(plugin) } } })

    await PowerContextTuiPlugin(api, undefined, {} as any)
    let dispose!: () => void
    let box: any
    createRoot((rootDispose) => {
      dispose = rootDispose
      box = slotPlugins[0].slots.session_prompt_right({}, { session_id: 'session-1' })
    })
    await vi.waitFor(() => expect(flattenTui(box)).toContain('saved 1.2k today'))
    const rendered = flattenTui(box)
    dispose()

    expect(rendered).toBe(`●${formatPowerContextStatus(statsWindow(1_200), statsWindow(-250))}`)
  })

  it('aborts an in-flight Scope resolution when the status view is disposed', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    let resolveSignal: AbortSignal | undefined
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      if (url.endsWith('/v1/scope-bindings/resolve')) {
        resolveSignal = init.signal ?? undefined
        return await new Promise<Response>((_resolve, reject) => {
          init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')))
        })
      }
      return Response.json(statsWindow(0))
    }))
    const slotPlugins: any[] = []
    const api = tuiApi({ slots: { register: (plugin: unknown) => { slotPlugins.push(plugin) } } })

    await PowerContextTuiPlugin(api, undefined, {} as any)
    let dispose!: () => void
    createRoot((rootDispose) => {
      dispose = rootDispose
      slotPlugins[0].slots.session_prompt_right({}, { session_id: 'session-1' })
    })
    await vi.waitFor(() => expect(resolveSignal).toBeDefined())

    dispose()

    await vi.waitFor(() => expect(resolveSignal?.aborted).toBe(true))
  })

  it('stops status polling when the status view is disposed', async () => {
    vi.useFakeTimers()
    let resolveCount = 0
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (url.endsWith('/v1/scope-bindings/resolve')) {
        resolveCount += 1
        return Response.json({ scope_id: 'project:test' })
      }
      return Response.json(statsWindow(1_200))
    }))
    const slotPlugins: any[] = []
    const api = tuiApi({ slots: { register: (plugin: unknown) => { slotPlugins.push(plugin) } } })

    await PowerContextTuiPlugin(api, undefined, {} as any)
    let dispose!: () => void
    createRoot((rootDispose) => {
      dispose = rootDispose
      slotPlugins[0].slots.session_prompt_right({}, { session_id: 'session-1' })
    })
    await vi.advanceTimersByTimeAsync(50)
    expect(resolveCount).toBeGreaterThan(0)
    const before = resolveCount

    dispose()
    await vi.advanceTimersByTimeAsync(60_000)

    expect(resolveCount).toBe(before)
  })
})
