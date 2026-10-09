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

import { afterEach, describe, expect, it, vi } from 'vitest'
import { mcpConfig, mcpEndpoint, POWERCONTEXT_MCP_SERVER_NAME, registerMcp, registerMcpPolicy } from '../src/mcp.ts'
import type { McpScopeResolver } from '../src/mcp.ts'
import { MCP_OPERATIONS } from '../src/mcp-operations.generated.ts'

vi.mock('../src/mcp-transport.ts', () => ({ protectMcpEndpoint: async (_ctx: unknown, endpoint: string) => endpoint }))

type PolicyHook = (exec: unknown, next: () => Promise<unknown>) => Promise<unknown>

function policyHook(resolveScope: McpScopeResolver = async () => 'scope-a'): PolicyHook {
  let hook: PolicyHook | undefined
  registerMcpPolicy({ on: (_event, listener) => { hook = listener as unknown as PolicyHook } }, resolveScope)
  if (!hook) throw new Error('MCP policy hook was not registered')
  return hook
}

afterEach(() => vi.useRealTimers())

describe('PowerContext MCP bridge', () => {
  it('derives the streamable HTTP endpoint from the normalized Server URL', () => {
    expect(mcpEndpoint('https://powercontext.example/')).toBe('https://powercontext.example/mcp/')
    expect(mcpEndpoint('https://powercontext.example')).toBe('https://powercontext.example/mcp/')
  })

  it('passes the configured authorization to the native DSH MCP client', () => {
    expect(mcpConfig({
      baseUrl: 'https://powercontext.example',
      authorization: 'Bearer test-token',
    })).toEqual({
      transport: 'streamable-http',
      serverName: POWERCONTEXT_MCP_SERVER_NAME,
      url: 'https://powercontext.example/mcp/',
      headers: { Authorization: 'Bearer test-token' },
      failOnStartupError: false,
      toolCallTimeoutMs: 60_000,
    })
    expect(mcpConfig({ baseUrl: 'https://powercontext.example', authorization: undefined }).headers).toEqual({})
  })

  it('delegates registration to @deepseek-ai/dsh-mcp-client', async () => {
    const apply = vi.fn(async () => undefined)
    const load = vi.fn(async <T>(_specifier: string) => ({ apply }) as T)
    const ctx = {} as never

    await registerMcp(ctx, { baseUrl: 'https://powercontext.example', authorization: undefined },
      async <T>(specifier: string) => await load(specifier) as T)

    expect(load).toHaveBeenCalledWith('@deepseek-ai/dsh-mcp-client')
    expect(apply).toHaveBeenCalledWith(ctx, expect.objectContaining({
      serverName: POWERCONTEXT_MCP_SERVER_NAME,
      url: 'https://powercontext.example/mcp/',
    }))
  })

  it('lets startup finish within five seconds and allows late native MCP registration', async () => {
    vi.useFakeTimers()
    let complete!: () => void
    let toolsAvailable = false
    const load = async <T>() => ({ apply: async () => {
      await new Promise<void>(resolve => { complete = resolve })
      toolsAvailable = true
    } }) as T
    let hostReady = false
    const startup = registerMcp({} as never, {
      baseUrl: 'https://powercontext.example', authorization: undefined,
    }, load).then(() => { hostReady = true })
    await vi.advanceTimersByTimeAsync(5000)
    expect(hostReady).toBe(true)
    expect(toolsAvailable).toBe(false)
    complete()
    await vi.advanceTimersByTimeAsync(0)
    await startup
    expect(toolsAvailable).toBe(true)
  })

  it('handles a late MCP initialization failure without rejecting host startup', async () => {
    vi.useFakeTimers()
    let fail!: (error: Error) => void
    const warn = vi.fn()
    const load = async <T>() => ({ apply: () => new Promise<void>((_resolve, reject) => { fail = reject }) }) as T
    const startup = registerMcp({ logger: { warn } } as never, {
      baseUrl: 'https://powercontext.example', authorization: undefined,
    }, load)
    await vi.advanceTimersByTimeAsync(5000)
    await expect(startup).resolves.toBeUndefined()
    fail(new Error('private-fixture-marker'))
    await vi.advanceTimersByTimeAsync(0)
    expect(warn.mock.calls.map(([line]) => JSON.parse(line))).toContainEqual(expect.objectContaining({
      event: 'mcp_connect', outcome: 'invalid_response',
    }))
    expect(warn.mock.calls.map(([line]) => line).join('\n')).not.toContain('private-fixture-marker')
  })

  it.each(['search_memory', 'get_scope', 'get_handoff_report'])(
    'denies %s when the host Scope is unavailable', async (operation) => {
      for (const resolveScope of [async () => undefined, async () => { throw new Error('private-fixture-marker') }]) {
        const next = vi.fn(async () => ({ kind: 'allow' as const }))
        const result = await policyHook(resolveScope)({
          name: `mcp__powercontext__${operation}`,
          arguments: { scope_id: 'scope-b', selection: { mode: 'exact', scope_ids: ['scope-b'] } },
          signal: new AbortController().signal,
        }, next)
        expect(result).toMatchObject({ kind: 'deny' })
        expect((result as { reason: string }).reason).toContain('host Scope could not be resolved')
        expect((result as { reason: string }).reason).not.toContain('private-fixture-marker')
        expect(next).not.toHaveBeenCalled()
      }
    },
  )

  it('asks before candidate approval even when the MCP server only advertises annotations', async () => {
    const next = vi.fn(async () => ({ kind: 'allow' as const }))
    const result = await policyHook()({
      name: 'mcp__powercontext__approve_artifact_candidate',
      arguments: { scope_id: 'scope-a', candidate_id: 'candidate-1' },
      signal: new AbortController().signal,
    }, next)
    expect(result).toMatchObject({ kind: 'ask' })
    expect((result as { reason: string }).reason).toContain('explicitly requested')
    expect(next).not.toHaveBeenCalled()
  })

  it.each([
    ['remember_memory', { text: 'api_key=FAKE_REVIEW_MARKER' }],
    ['revise_memory_entry', { text: 'api_key=FAKE_REVIEW_MARKER' }],
    ['capture_content_source', { content: 'api_key=FAKE_REVIEW_MARKER' }],
  ])(
    'rejects secret content in %s before Scope resolution or approval', async (operation, content) => {
      const resolveScope = vi.fn(async () => 'scope-a')
      const next = vi.fn(async () => ({ kind: 'allow' as const }))
      const result = await policyHook(resolveScope)({
        name: `mcp__powercontext__${operation}`,
        arguments: { scope_id: 'scope-a', ...content },
        signal: new AbortController().signal,
      }, next)
      expect(result).toMatchObject({ kind: 'deny', reason: expect.stringContaining('secret_rejected') })
      expect(resolveScope).not.toHaveBeenCalled()
      expect(next).not.toHaveBeenCalled()
    },
  )

  it.each([
    ['handoff_current_work', { handoff: { state: [{ text: 'api_key=FAKE_HANDOFF_SECRET_CANARY', evidence: [] }] } }],
    ['handoff_current_work', { handoff: { omissions: ['-----BEGIN PRIVATE KEY-----'] } }],
    ['activate_handoff', { objective: 'api_key=FAKE_HANDOFF_SECRET_CANARY' }],
    ['finalize_handoff', { draft: { next_action: { text: 'sk-FAKE_HANDOFF_SECRET_CANARY', evidence: [] } } }],
    ['commit_handoff', { handoff: { content: { state: [{ text: 'api_key=FAKE_HANDOFF_SECRET_CANARY' }] } } }],
  ])('rejects nested or objective secret content in %s before Scope resolution or approval', async (operation, content) => {
    const resolveScope = vi.fn(async () => 'scope-a')
    const next = vi.fn(async () => ({ kind: 'allow' as const }))
    const result = await policyHook(resolveScope)({
      name: `mcp__powercontext__${operation}`,
      arguments: { scope_id: 'scope-a', ...content },
      signal: new AbortController().signal,
    }, next)
    expect(result).toMatchObject({ kind: 'deny', reason: expect.stringContaining('secret_rejected') })
    expect((result as { reason: string }).reason).not.toContain('FAKE_HANDOFF_SECRET_CANARY')
    expect(resolveScope).not.toHaveBeenCalled()
    expect(next).not.toHaveBeenCalled()
  })

  it.each([
    ['handoff_current_work', { handoff: { state: [{ text: 'Check the deployment', evidence: [] }], omissions: [] } }],
    ['finalize_handoff', { draft: { next_action: { text: 'Check the deployment', evidence: [] } } }],
    ['commit_handoff', { handoff: { content: { state: [{ text: 'Check the deployment' }] } } }],
  ])('keeps ordinary Handoff content in %s subject to write approval', async (operation, content) => {
    const result = await policyHook()({
      name: `mcp__powercontext__${operation}`,
      arguments: { scope_id: 'scope-a', ...content },
      signal: new AbortController().signal,
    }, async () => ({ kind: 'allow' }))
    expect(result).toMatchObject({ kind: 'ask' })
  })

  it('does not reject a read-only search for a secret marker', async () => {
    const result = await policyHook()({
      name: 'mcp__powercontext__search_memory',
      arguments: { scope_id: 'scope-a', query: 'api_key' },
      signal: new AbortController().signal,
    }, async () => ({ kind: 'allow' }))
    expect(result).toEqual({ kind: 'allow' })
  })

  it.each(Object.entries(MCP_OPERATIONS))('applies the MCP annotation approval boundary for %s', async (operation, metadata) => {
    const next = vi.fn(async () => ({ kind: 'allow' as const }))
    const result = await policyHook()({
      name: `mcp__powercontext__${operation}`,
      arguments: { scope_id: 'scope-a', selection: { mode: 'exact', scope_ids: ['scope-a'] } },
      signal: new AbortController().signal,
    }, next)
    expect(result).toMatchObject({ kind: metadata.readOnly ? 'allow' : 'ask' })
  })

  it.each(['prepare_handoff', 'unknown_future_operation'])('refuses %s outside the native MCP catalog', async (operation) => {
    const next = vi.fn(async () => ({ kind: 'allow' as const }))
    const result = await policyHook()({ name: `mcp__powercontext__${operation}`, arguments: {} }, next)
    expect(result).toMatchObject({ kind: 'deny', reason: expect.stringContaining('unavailable') })
    expect(next).not.toHaveBeenCalled()
  })

  it('refuses a native MCP call that names a different host-resolved Scope', async () => {
    const next = vi.fn(async () => ({ kind: 'allow' as const }))
    const result = await policyHook()({
      name: 'mcp__powercontext__search_memory',
      arguments: { scope_id: 'scope-b', query: 'Aurora' },
      signal: new AbortController().signal,
    }, next)
    expect(result).toMatchObject({ kind: 'deny' })
    expect((result as { reason: string }).reason).toContain('"scope-a"')
    expect(next).not.toHaveBeenCalled()
  })

  it('allows a read-only native MCP call with the exact host-resolved Scope', async () => {
    const next = vi.fn(async () => ({ kind: 'allow' as const }))
    const result = await policyHook()({
      name: 'mcp__powercontext__search_memory',
      arguments: { scope_id: 'scope-a', query: 'Aurora' },
      signal: new AbortController().signal,
    }, next)
    expect(result).toEqual({ kind: 'allow' })
    expect(next).toHaveBeenCalledOnce()
  })

  it.each(['get_scope', 'list_dream_runs', 'get_dream_run', 'create_dream_run'])(
    'refuses another Scope in the path arguments of native MCP %s', async (operation) => {
      const next = vi.fn(async () => ({ kind: 'allow' as const }))
      const result = await policyHook()({
        name: `mcp__powercontext__${operation}`,
        arguments: { scope_id: 'scope-b', run_id: 'run-1' },
        signal: new AbortController().signal,
      }, next)
      expect(result).toMatchObject({ kind: 'deny' })
      expect((result as { reason: string }).reason).toContain('"scope-a"')
      expect(next).not.toHaveBeenCalled()
    },
  )

  it.each([
    ['get_scope', 'allow'],
    ['list_dream_runs', 'allow'],
    ['get_dream_run', 'allow'],
    ['create_dream_run', 'ask'],
  ])('preserves the approval policy for native MCP %s in the host Scope', async (operation, decision) => {
    const next = vi.fn(async () => ({ kind: 'allow' as const }))
    const result = await policyHook()({
      name: `mcp__powercontext__${operation}`,
      arguments: { scope_id: 'scope-a', run_id: 'run-1' },
      signal: new AbortController().signal,
    }, next)
    expect(result).toMatchObject({ kind: decision })
    if (decision === 'ask') expect(next).not.toHaveBeenCalled()
    else expect(next).toHaveBeenCalledOnce()
  })
})
