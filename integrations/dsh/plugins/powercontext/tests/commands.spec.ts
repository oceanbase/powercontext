import { describe, expect, it } from 'vitest'
import { handlePcCommand, registerCommands } from '../src/commands.ts'
import { PowerContextClient } from '../src/client.ts'
import type { PluginRuntime } from '../src/invoke.ts'
import { resolveConfig } from '../src/config.ts'
import { buildSourceId } from '../src/capture.ts'
import { deriveScopeId } from '../src/scope.ts'

function runtime(fetchImpl: typeof fetch): PluginRuntime {
  const config = resolveConfig({ baseUrl: 'http://127.0.0.1:8000' })
  return {
    client: new PowerContextClient({ baseUrl: config.baseUrl, requestTimeoutMs: 1000, fetch: fetchImpl }),
    config,
    resolveScope: async () => 'project:demo',
    log: () => undefined,
  }
}

describe('handlePcCommand', () => {
  it('prints scope on bare /pc', async () => {
    const result = await handlePcCommand('', runtime(async () => new Response('{}')), 'project:demo')
    expect(result.kind).toBe('success')
    expect(result.text).toContain('scope=project:demo')
  })

  it('requires version arguments for review approve', async () => {
    const result = await handlePcCommand('review approve only-id', runtime(async () => new Response('{}')), 'project:demo')
    expect(result.kind).toBe('error')
    expect(result.text).toContain('Usage: /pc review approve')
  })
})

describe('registerCommands missing session cwd', () => {
  function registerHandler(pluginRuntime: PluginRuntime) {
    let handler: ((invocation: {
      rawInput: string
      signal: AbortSignal
      agent: { session: { header: { cwd?: string } } }
    }) => Promise<{ kind: string; text: string }>) | undefined
    registerCommands({
      get: (name) => name === 'commands'
        ? { register: (definition: { handler: typeof handler }) => { handler = definition.handler } }
        : undefined,
    }, pluginRuntime)
    if (!handler) throw new Error('expected /pc handler')
    return handler
  }

  it('returns an unscoped error when cwd is missing and scopeId is not configured', async () => {
    const pluginRuntime = runtime(async () => new Response('{}'))
    pluginRuntime.config = { ...pluginRuntime.config, scopeId: undefined }
    pluginRuntime.resolveScope = (cwd) => deriveScopeId(cwd)
    const handler = registerHandler(pluginRuntime)
    const result = await handler({
      rawInput: 'search public API',
      signal: AbortSignal.timeout(1000),
      agent: { session: { header: {} } },
    })
    expect(result.kind).toBe('error')
    expect(result.text).toContain('scopeId')
  })

  it('uses configured scopeId when cwd is missing', async () => {
    const pluginRuntime = runtime(async () => new Response('{}'))
    pluginRuntime.config = { ...pluginRuntime.config, scopeId: 'project:demo' }
    pluginRuntime.resolveScope = (cwd) => deriveScopeId(cwd, { configuredScopeId: 'project:demo' })
    const handler = registerHandler(pluginRuntime)
    const result = await handler({
      rawInput: '',
      signal: AbortSignal.timeout(1000),
      agent: { session: { header: { cwd: undefined } } },
    })
    expect(result.kind).toBe('success')
    expect(result.text).toContain('scope=project:demo')
  })
})

describe('config env overrides', () => {
  it('reads POWERCONTEXT_DSH_* over plugin config', () => {
    const resolved = resolveConfig(
      { baseUrl: 'http://127.0.0.1:8000', capturePrompts: true },
      {
        POWERCONTEXT_DSH_BASE_URL: 'http://example.local:9000/',
        POWERCONTEXT_DSH_SCOPE_ID: 'project:from-env',
        POWERCONTEXT_DSH_CAPTURE_PROMPTS: 'false',
      },
    )
    expect(resolved.baseUrl).toBe('http://example.local:9000')
    expect(resolved.scopeId).toBe('project:from-env')
    expect(resolved.capturePrompts).toBe(false)
  })
})

describe('capture source id', () => {
  it('is stable for the same prompt identity', () => {
    const first = buildSourceId('scope', 's1', '1', 'hello')
    const second = buildSourceId('scope', 's1', '1', 'hello')
    const other = buildSourceId('scope', 's1', '2', 'hello')
    expect(first).toBe(second)
    expect(first).toMatch(/^dsh-user-prompt:[a-f0-9]{64}$/)
    expect(first).not.toBe(other)
  })
})
