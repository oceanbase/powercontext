import { describe, expect, it, vi } from 'vitest'
import powercontextPi from '../extensions/powercontext.ts'
import { PowerContextClient } from '../src/client.ts'
import { registerTools } from '../src/tools.ts'
import type { PluginRuntime } from '../src/recall.ts'

describe('Pi native tool surface', () => {
  it('registers the explicit Memory and Handoff tools only', () => {
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
    const fetch = vi.fn(async () => new Response(JSON.stringify({ entry: { text: 'remembered' } })))
    const runtime = {
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
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const remember = registered.find((tool) => tool.name === 'pc_remember') as unknown as {
      execute: (
        id: string,
        params: { kind: string; text: string },
        signal: AbortSignal,
        update: () => void,
        context: Record<string, unknown>,
      ) => Promise<{ details: { code?: string; ok: boolean } }>
    }

    const denied = await remember.execute('call-1', { kind: 'agent-note', text: 'keep API async' }, new AbortController().signal, () => undefined, {
      cwd: '/workspace/repo',
      hasUI: false,
      ui: { confirm: vi.fn() },
    })
    expect(denied.details).toMatchObject({ ok: false, code: 'confirmation_required' })
    expect(fetch).not.toHaveBeenCalled()

    const confirm = vi.fn(async () => true)
    const approved = await remember.execute('call-2', { kind: 'agent-note', text: 'keep API async' }, new AbortController().signal, () => undefined, {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: { confirm },
    })
    expect(approved.details).toMatchObject({ ok: true })
    expect(confirm).toHaveBeenCalled()
    expect(fetch).toHaveBeenCalled()
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
