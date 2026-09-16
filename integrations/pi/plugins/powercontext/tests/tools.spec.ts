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
import type { TSchema } from 'typebox'
import { Value } from 'typebox/value'
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
      allowInsecureHttp: false,
      scopeId: undefined,
      authorization: undefined,
      capturePrompts: true,
      requestTimeoutMs: 1000,
      httpBudgetMs: 4000,
      maxBytes: 8000,
      flushOnCapture: false,
      flushMaxCalls: 4,
      diagnostics: 'off',
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
      'pc_work_contract',
      'pc_handoff_current',
      'pc_handoff_acknowledge',
      'pc_task_outcome',
      'pc_handoff_prepare',
      'pc_handoff_finalize',
      'pc_handoff_commit',
      'pc_handoff_continue',
      'pc_experience_get',
      'pc_skill_get',
      'pc_topic_search',
      'pc_topic_get',
      'pc_review_list',
      'pc_review_get',
      'pc_external_scan',
      'pc_external_list',
      'pc_external_resolve',
      'pc_external_import',
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

  it('requires confirmation and filters secrets for all structured work writes', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ ok: true })))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const context = {
      cwd: '/workspace/repo',
      hasUI: false,
      ui: { confirm: vi.fn(async () => true) },
    }
    const signal = new AbortController().signal

    const workWrites: Array<[string, Record<string, unknown>]> = [
      ['pc_work_contract', { source_id: 'contract-1', contract: { objective: 'ship feature' } }],
      ['pc_handoff_current', { source_id: 'boundary-1', handoff: { objective: 'continue feature' } }],
      ['pc_handoff_acknowledge', {
        source_id: 'receipt-1', receiver: 'pi', status: 'accepted', selection: 'exact',
        receiver_checks: { live_state: 'confirmed', capability: 'confirmed', authorization: 'confirmed' },
        revision: { family: 'handoff', artifact_id: 'handoff-1', revision: 1 },
      }],
      ['pc_task_outcome', { source_id: 'outcome-1', outcome: { status: 'succeeded' } }],
    ]

    for (const [name, params] of workWrites) {
      const result = await registeredTool<Record<string, unknown>>(registered, name).execute(
        `call-${name}`, params, signal, () => undefined, context,
      )
      expect(result.details).toMatchObject({ ok: false, code: 'confirmation_required' })
    }
    expect(fetch).not.toHaveBeenCalled()

    context.hasUI = true
    const confirm = vi.fn(async () => true)
    context.ui.confirm = confirm
    await registeredTool<{ source_id: string; outcome: Record<string, unknown> }>(registered, 'pc_task_outcome').execute(
      'call-secret', { source_id: 'outcome-2', outcome: { summary: 'token=secret' } }, signal, () => undefined, context,
    )
    expect(confirm).toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })

  it('keeps external skill discovery read-only and confirms imports', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ ok: true })))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const confirm = vi.fn(async () => true)
    const context = { cwd: '/workspace/repo', hasUI: true, ui: { confirm } }
    const signal = new AbortController().signal
    const fingerprint = 'a'.repeat(64)

    const denied = await registeredTool<Record<string, unknown>>(registered, 'pc_external_import').execute(
      'call-import-no-ui', { external_skill_id: 'skill-1', fingerprint, mode: 'import' },
      signal, () => undefined, { ...context, hasUI: false },
    )
    expect(denied.details).toMatchObject({ ok: false, code: 'confirmation_required' })

    await registeredTool<Record<string, unknown>>(registered, 'pc_external_scan').execute(
      'call-scan', {}, signal, () => undefined, context,
    )
    await registeredTool<{ include_unavailable: boolean }>(registered, 'pc_external_list').execute(
      'call-list', { include_unavailable: true }, signal, () => undefined, context,
    )
    await registeredTool<{ external_skill_id: string; fingerprint: string }>(registered, 'pc_external_resolve').execute(
      'call-resolve', { external_skill_id: 'skill-1', fingerprint }, signal, () => undefined, context,
    )
    await registeredTool<Record<string, unknown>>(registered, 'pc_external_import').execute(
      'call-import', { external_skill_id: 'skill-1', fingerprint, mode: 'import', reason: null },
      signal, () => undefined, context,
    )

    expect(confirm).toHaveBeenCalledTimes(1)
    expect(fetch.mock.calls.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
      ['http://127.0.0.1:8000/v1/external-skills/scan', { scope_id: 'project:demo' }],
      ['http://127.0.0.1:8000/v1/external-skills/list', { include_unavailable: true, scope_id: 'project:demo' }],
      ['http://127.0.0.1:8000/v1/external-skills/resolve', { external_skill_id: 'skill-1', fingerprint, scope_id: 'project:demo' }],
      ['http://127.0.0.1:8000/v1/external-skills/import', {
        external_skill_id: 'skill-1', fingerprint, mode: 'import', reason: null, scope_id: 'project:demo',
      }],
    ])
  })

  it('routes structured work payloads to their scoped APIs unchanged after confirmation', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => new Response(JSON.stringify({ ok: true })))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const context = {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: { confirm: vi.fn(async () => true) },
    }
    const signal = new AbortController().signal
    const sourceCitation = { kind: 'source', source_ref: { name: 'git', source_id: 'commit-1' } }
    const claim = { text: 'tests passed', basis: 'verified', evidence: [sourceCitation] }
    const contract = {
      schema: 'powercontext.work-contract.v1', trust: 'untrusted_input', objective: 'ship feature',
      facts: [claim], in_scope: ['implementation'], exclusions: ['unrelated cleanup'],
      completion_criteria: ['tests pass'], authorization_notes: ['user authorized implementation'], open_questions: [],
    }
    const handoff = {
      schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input', objective: 'continue feature',
      state: [claim], disposition: 'continuable', next_action: claim, omissions: [],
    }
    const outcome = {
      schema: 'powercontext.task-outcome.v1', trust: 'untrusted_observation', objective: 'ship feature',
      status: 'succeeded', summary: 'implemented', handoff_receipt_ref: null, observations: [claim], checks: [],
      produced_artifacts: [], remaining_work: [],
    }

    await registeredTool<{ source_id: string; contract: Record<string, unknown> }>(registered, 'pc_work_contract').execute(
      'call-contract', { source_id: 'contract-1', contract }, signal, () => undefined, context,
    )
    await registeredTool<{ source_id: string; handoff: Record<string, unknown> }>(registered, 'pc_handoff_current').execute(
      'call-current', { source_id: 'boundary-1', handoff }, signal, () => undefined, context,
    )
    await registeredTool<Record<string, unknown>>(registered, 'pc_handoff_acknowledge').execute(
      'call-acknowledge', {
        source_id: 'receipt-1', receiver: 'pi', status: 'accepted', selection: 'exact',
        receiver_checks: { live_state: 'confirmed', capability: 'confirmed', authorization: 'confirmed' },
        revision: { family: 'handoff', artifact_id: 'handoff-1', revision: 1 },
      }, signal, () => undefined, context,
    )
    await registeredTool<{ source_id: string; outcome: Record<string, unknown> }>(registered, 'pc_task_outcome').execute(
      'call-outcome', { source_id: 'outcome-1', outcome }, signal, () => undefined, context,
    )

    expect(context.ui.confirm).toHaveBeenCalledTimes(4)
    expect(fetch.mock.calls.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
      ['http://127.0.0.1:8000/v1/work/contracts/create', { source_id: 'contract-1', contract, scope_id: 'project:demo' }],
      ['http://127.0.0.1:8000/v1/work/handoffs/prepare-current', { source_id: 'boundary-1', handoff, scope_id: 'project:demo' }],
      ['http://127.0.0.1:8000/v1/work/handoffs/acknowledge', {
        source_id: 'receipt-1', receiver: 'pi', status: 'accepted', selection: 'exact',
        receiver_checks: { live_state: 'confirmed', capability: 'confirmed', authorization: 'confirmed' },
        revision: { family: 'handoff', artifact_id: 'handoff-1', revision: 1 }, scope_id: 'project:demo',
      }],
      ['http://127.0.0.1:8000/v1/work/outcomes/record', { source_id: 'outcome-1', outcome, scope_id: 'project:demo' }],
    ])
  })

  it('accepts the check results the OpenAPI contract marks optional', () => {
    const registered: Array<Record<string, unknown>> = []
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, createRuntime(vi.fn()))
    const parameters = (
      registered.find((tool) => tool.name === 'pc_task_outcome') as { parameters: TSchema }
    ).parameters
    const outcome = {
      schema: 'powercontext.task-outcome.v1',
      trust: 'untrusted_observation',
      objective: 'ship feature',
      status: 'succeeded',
      summary: 'implemented',
      observations: [{
        text: 'tests passed',
        basis: 'verified',
        evidence: [{ kind: 'source', source_ref: { name: 'git', source_id: 'commit-1' } }],
      }],
      checks: [{ name: 'tests', status: 'passed', basis: 'declared', evidence: [] }],
      produced_artifacts: [],
      remaining_work: [],
    }

    expect(Value.Check(parameters, { source_id: 'outcome-1', outcome })).toBe(true)
    expect(Value.Check(parameters, {
      source_id: 'outcome-1',
      outcome: { ...outcome, checks: [{ ...outcome.checks[0], details: null }] },
    })).toBe(true)
    expect(Value.Check(parameters, {
      source_id: 'outcome-1',
      outcome: { ...outcome, checks: [{ name: 'tests', status: 'passed', evidence: [] }] },
    })).toBe(false)
  })

  it('reads exact artifacts and inspects candidates without requesting confirmation', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => (
      new Response(JSON.stringify({ status: 'ok' }))
    ))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const context = {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: { confirm: vi.fn(async () => false) },
    }
    const signal = new AbortController().signal

    await registeredTool<{ artifact: Record<string, unknown> }>(registered, 'pc_experience_get').execute(
      'call-1', { artifact: { family: 'experience', artifact_id: 'exp-1', revision: 2 } }, signal,
      () => undefined, context,
    )
    await registeredTool<{ artifact: Record<string, unknown> }>(registered, 'pc_skill_get').execute(
      'call-2', { artifact: { family: 'skill', artifact_id: 'skill-1', revision: 3 } }, signal,
      () => undefined, context,
    )
    await registeredTool<{ status?: string; family?: string; cursor?: string; limit?: number }>(
      registered, 'pc_review_list',
    ).execute('call-3', {
      status: 'approved', family: 'skill', cursor: 'next-page', limit: 120,
    }, signal, () => undefined, context)
    await registeredTool<{ candidate_id: string }>(registered, 'pc_review_get').execute(
      'call-4', { candidate_id: 'candidate-1' }, signal, () => undefined, context,
    )

    expect(context.ui.confirm).not.toHaveBeenCalled()
    expect(fetch.mock.calls.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
      ['http://127.0.0.1:8000/v1/experience/get', {
        artifact: { family: 'experience', artifact_id: 'exp-1', revision: 2 },
        scope_id: 'project:demo',
      }],
      ['http://127.0.0.1:8000/v1/skill/get', {
        artifact: { family: 'skill', artifact_id: 'skill-1', revision: 3 },
        scope_id: 'project:demo',
      }],
      ['http://127.0.0.1:8000/v1/artifact-candidates/list', {
        status: 'approved', family: 'skill', cursor: 'next-page', limit: 100, scope_id: 'project:demo',
      }],
      ['http://127.0.0.1:8000/v1/artifact-candidates/get', {
        candidate_id: 'candidate-1', scope_id: 'project:demo',
      }],
    ])
  })

  it('searches and reads Topic Memory through the read-only current-Scope APIs', async () => {
    const registered: Array<Record<string, unknown>> = []
    const fetch = vi.fn(async (_url: string, _init?: RequestInit) => (
      new Response(JSON.stringify({ mode: 'fts', hits: [] }))
    ))
    const runtime = createRuntime(fetch)
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, runtime)
    const context = {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: { confirm: vi.fn(async () => false) },
    }
    const signal = new AbortController().signal

    await registeredTool<{ query: string; limit?: number }>(registered, 'pc_topic_search').execute(
      'call-1', { query: 'deployment decisions', limit: 50 }, signal, () => undefined, context,
    )
    await registeredTool<{ query: string; limit?: number }>(registered, 'pc_topic_search').execute(
      'call-2', { query: 'deployment decisions', limit: 0 }, signal, () => undefined, context,
    )
    await registeredTool<{ query: string; limit?: number }>(registered, 'pc_topic_search').execute(
      'call-3', { query: 'deployment decisions' }, signal, () => undefined, context,
    )
    await registeredTool<{ artifact: Record<string, unknown> }>(registered, 'pc_topic_get').execute(
      'call-4', { artifact: { family: 'topic-memory', artifact_id: 'topic-1', revision: 4 } }, signal,
      () => undefined, context,
    )

    expect(context.ui.confirm).not.toHaveBeenCalled()
    expect(fetch.mock.calls.map(([url, init]) => [url, JSON.parse(String(init?.body))])).toEqual([
      ['http://127.0.0.1:8000/v1/topic-memory/search', {
        query: 'deployment decisions', limit: 20, scope_id: 'project:demo',
      }],
      ['http://127.0.0.1:8000/v1/topic-memory/search', {
        query: 'deployment decisions', limit: 1, scope_id: 'project:demo',
      }],
      ['http://127.0.0.1:8000/v1/topic-memory/search', {
        query: 'deployment decisions', limit: 10, scope_id: 'project:demo',
      }],
      ['http://127.0.0.1:8000/v1/topic-memory/get', {
        artifact: { family: 'topic-memory', artifact_id: 'topic-1', revision: 4 },
        scope_id: 'project:demo',
      }],
    ])
  })

  it('publishes the Topic Memory search schema without retrieval controls', () => {
    const registered: Array<Record<string, unknown>> = []
    registerTools({ registerTool: (tool: Record<string, unknown>) => registered.push(tool) } as never, createRuntime(vi.fn()))
    const search = registeredTool<Record<string, unknown>>(registered, 'pc_topic_search') as unknown as {
      parameters: { properties: Record<string, Record<string, unknown>> }
    }

    expect(search.parameters.properties.limit).toMatchObject({ type: 'number' })
    expect(search.parameters.properties).not.toHaveProperty('mode')
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
