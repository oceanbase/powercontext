import { describe, expect, it, vi } from 'vitest'
import { registerTools } from '../src/tools.ts'
import type { PluginRuntime } from '../src/invoke.ts'

describe('agent tool surface', () => {
  it('registers only explicit agent tools and does not expose generic or destructive operations', () => {
    const registered: Array<Record<string, unknown>> = []
    let preExecute: ((exec: { name: string }, next: () => Promise<unknown>) => Promise<unknown>) | undefined
    const runtime = {
      client: {} as never,
      config: { maxBytes: 8000 },
      resolveScope: vi.fn(async () => 'project:demo'),
      log: vi.fn(),
    } as unknown as PluginRuntime

    registerTools(
      {
        tools: { register: (tool) => registered.push(tool as Record<string, unknown>) },
        on: (event, handler) => {
          if (event === 'tools/pre-execute') preExecute = handler as never
        },
      },
      runtime,
      (definition) => definition,
    )

    const names = registered.map((tool) => tool.name)
    expect(names).toEqual([
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
      'pc_experience_generate',
      'pc_experience_get',
      'pc_skill_generate',
      'pc_skill_get',
      'pc_review_list',
      'pc_review_get',
    ])
    expect(names).not.toContain('pc_call')
    expect(names).not.toContain('purge_handoff_report_activities')
    expect(names).not.toContain('detach_handoff_report_workspace')
    expect(names).not.toContain('approve_artifact_candidate')
    expect(preExecute).toBeTypeOf('function')
  })

  it('requires one-time approval for named mutations and delegates reads', async () => {
    let preExecute: ((exec: { name: string }, next: () => Promise<unknown>) => Promise<unknown>) | undefined
    const runtime = {
      client: {} as never,
      config: { maxBytes: 8000 },
      resolveScope: vi.fn(async () => 'project:demo'),
      log: vi.fn(),
    } as unknown as PluginRuntime
    registerTools(
      {
        tools: { register: vi.fn() },
        on: (event, handler) => {
          if (event === 'tools/pre-execute') preExecute = handler as never
        },
      },
      runtime,
      (definition) => definition,
    )
    const next = vi.fn(async () => ({ kind: 'allow' }))

    await expect(preExecute?.({ name: 'pc_remember' }, next)).resolves.toEqual({
      kind: 'ask',
      reason: 'PowerContext tool "pc_remember" changes durable project context.',
    })
    expect(next).not.toHaveBeenCalled()
    await expect(preExecute?.({ name: 'pc_search' }, next)).resolves.toEqual({ kind: 'allow' })
    expect(next).toHaveBeenCalledOnce()
  })
})
