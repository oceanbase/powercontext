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
  it('registers the explicit Memory, Handoff, Candidate Review, and Report tools', () => {
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
      'pc_handoff_report_get',
      'pc_handoff_report_workspace_get',
      'pc_artifact_candidate_list',
      'pc_artifact_candidate_get',
      'pc_artifact_candidate_approve',
      'pc_artifact_candidate_reject',
      'pc_artifact_candidate_revise',
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
    const remember = registeredTool<{ expected_revision?: number; kind: string; text: string }>(registered, 'pc_remember')

    const denied = await remember.execute('call-1', {
      kind: 'agent-note',
      text: 'keep API async',
      expected_revision: 3,
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
      expected_revision: 3,
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
      expected_revision: 3,
      scope_id: 'project:demo',
    })
  })

  it('forwards an explicit handoff context budget after confirmation', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ status: 'generated' })))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const activate = registeredTool<{
      boundary_source: Record<string, unknown>
      evidence?: Array<Record<string, unknown>>
      max_bytes?: number
      objective: string
    }>(registered, 'pc_handoff_activate')
    const confirm = vi.fn(async () => true)

    const result = await activate.execute('call-1', {
      boundary_source: { name: 'content', source_id: 'source-1' },
      objective: 'Transfer the validated state.',
      max_bytes: 1024,
    }, new AbortController().signal, () => undefined, {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: { confirm },
    })
    expect(result.details).toMatchObject({ ok: true })
    expect(confirm).toHaveBeenCalled()
    const init = fetch.mock.calls[0]?.[1]
    expect(JSON.parse(String(init?.body))).toEqual({
      boundary_source: { name: 'content', source_id: 'source-1' },
      objective: 'Transfer the validated state.',
      evidence: [],
      max_bytes: 1024,
      scope_id: 'project:demo',
    })
  })

  it('confirms candidate decisions before sending the scoped mutation', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => (
      new Response(JSON.stringify({ candidate_id: 'candidate-1', version: 2 }))
    ))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const approve = registeredTool<{ candidate_id: string; expected_version: number }>(
      registered,
      'pc_artifact_candidate_approve',
    )

    const denied = await approve.execute('call-1', { candidate_id: 'candidate-1', expected_version: 1 },
      new AbortController().signal, () => undefined, {
        cwd: '/workspace/repo',
        hasUI: false,
        ui: { confirm: vi.fn() },
      })
    expect(denied.details).toMatchObject({ ok: false, code: 'confirmation_required' })
    expect(fetch).not.toHaveBeenCalled()

    const confirm = vi.fn(async () => true)
    const approved = await approve.execute('call-2', { candidate_id: 'candidate-1', expected_version: 1 },
      new AbortController().signal, () => undefined, {
        cwd: '/workspace/repo',
        hasUI: true,
        ui: { confirm },
      })
    expect(approved.details).toMatchObject({ ok: true })
    expect(confirm).toHaveBeenCalled()
    expect(fetch).toHaveBeenCalledOnce()
    const init = fetch.mock.calls[0]?.[1]
    expect(init).toBeDefined()
    expect(JSON.parse(String(init?.body))).toEqual({
      candidate_id: 'candidate-1',
      expected_version: 1,
      scope_id: 'project:demo',
    })
  })

  it('gets Handoff Reports without deriving a project scope', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response('# Handoff report'))
    const runtime = createRuntime(fetch)
    runtime.resolveScope = async () => {
      throw new Error('scope should not be resolved for a report')
    }
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const getReport = registeredTool<{ project_id: string }>(registered, 'pc_handoff_report_get')

    const result = await getReport.execute('call-1', { project_id: 'report-project' }, new AbortController().signal,
      () => undefined, {
        cwd: '/workspace/repo',
        hasUI: false,
        ui: { confirm: vi.fn() },
      })
    expect(result.details).toMatchObject({ ok: true, data: { markdown: '# Handoff report' } })
    expect(fetch).toHaveBeenCalledWith('http://127.0.0.1:8000/v1/handoff-reports/get', expect.anything())
    const init = fetch.mock.calls[0]?.[1]
    expect(JSON.parse(String(init?.body))).toEqual({
      project_id: 'report-project',
      include_evidence_checks: true,
      format: 'markdown',
      include_archived: false,
      download: false,
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
