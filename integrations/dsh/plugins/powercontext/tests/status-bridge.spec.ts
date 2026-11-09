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

import type { IncomingMessage, ServerResponse } from 'node:http'
import { describe, expect, it, vi } from 'vitest'
import type { ClientSuccess } from '../src/client.ts'
import type { ResolvedConfig } from '../src/config.ts'
import type { PluginRuntime } from '../src/invoke.ts'
import { RuntimeStatus } from '../src/status.ts'
import {
  StatusBridge, createStatusRouteHandler, deploymentRejection, isLoopbackAddress, localDeploymentFacts,
  registerStatusBridge, resolveDeployment, type StatusBridgeOptions,
} from '../src/status-bridge.ts'

function config(overrides: Partial<ResolvedConfig> = {}): ResolvedConfig {
  return {
    sources: { baseUrl: 'default', authorization: 'default', scopeId: 'default' },
    baseUrl: 'http://127.0.0.1:8000',
    allowInsecureHttp: false,
    authorization: undefined,
    scopeId: undefined,
    ownerLocal: undefined,
    timeoutMs: 4_000,
    requestTimeoutMs: 1_000,
    maxBytes: 8_000,
    capturePrompts: true,
    flushOnCapture: false,
    flushMaxCalls: 4,
    ...overrides,
  }
}

function success(value: unknown, status = 200): ClientSuccess {
  return { kind: 'json', value, status, requestId: undefined }
}

function clientWith(overrides: {
  liveness?: () => Promise<ClientSuccess>
  readiness?: () => Promise<ClientSuccess>
  capabilities?: () => Promise<ClientSuccess>
} = {}) {
  return {
    request: vi.fn(async (operation: string) => {
      if (operation === 'get_liveness') {
        return overrides.liveness?.() ?? success({ status: 'ok' })
      }
      if (operation === 'get_readiness') {
        return overrides.readiness?.() ?? success({ status: 'ready', checks: { database: 'ready', runtime: 'ready' } })
      }
      if (operation === 'get_capabilities') {
        return overrides.capabilities?.() ?? success({
          memory_extraction: true,
          handoff_generation: false,
          source_types: ['content'],
          artifact_families: ['atomic-memory'],
          search_modes: ['hybrid'],
          context_versions: ['powercontext.prepared-context.v1'],
        })
      }
      throw new Error(`unexpected operation ${operation}`)
    }),
  }
}

function options(overrides: Partial<StatusBridgeOptions> = {}): StatusBridgeOptions {
  return {
    config: config(),
    client: clientWith() as unknown as StatusBridgeOptions['client'],
    resolveScope: async () => 'scope-a',
    sessions: { get: id => id === 'session-1' ? { header: { cwd: '/workspace/repo' } } : undefined },
    ...overrides,
  }
}

function responseMock() {
  const headers: Record<string, string> = {}
  let body = ''
  const response = {
    statusCode: 0,
    writableEnded: false,
    setHeader(name: string, value: string) { headers[name.toLowerCase()] = value },
    end(value?: string) { body = value ?? ''; response.writableEnded = true },
    get body() { return body },
    get headers() { return headers },
  }
  return response as typeof response & ServerResponse
}

function requestMock(overrides: Partial<IncomingMessage> = {}): IncomingMessage {
  return {
    method: 'GET',
    url: '/powercontext/status',
    headers: { host: '127.0.0.1:3080' },
    socket: { remoteAddress: '127.0.0.1' },
    ...overrides,
  } as unknown as IncomingMessage
}

function connection(rejection?: 401 | 403) {
  return { requestRejection: () => rejection }
}

describe('status bridge', () => {
  it('reports readiness, the active Scope, and RuntimeStatus stages separately', async () => {
    const now = 1_800_000_000_000
    const status = new RuntimeStatus(() => now)
    const attempt = status.begin('session-1', '/workspace/repo', '7')
    attempt.scope('scope-a')
    attempt.record('prepare', { state: 'ready', content_bytes: 120 })
    attempt.record('capture', { state: 'accepted' })
    attempt.skip('flush', 'flush_disabled')
    attempt.record('injection', { state: 'appended' })
    const bridge = new StatusBridge(options({ status, now: () => now }))
    const payload = await bridge.status('session-1')

    expect(payload.connection.state).toBe('ready')
    expect(payload.session?.scope_id).toBe('scope-a')
    expect(payload.session?.cwd).toBe('/workspace/repo')
    expect(payload.runtime.freshness).toBe('current')
    expect(payload.runtime.stages.prepare.state).toBe('ready')
    expect(payload.runtime.stages.capture.state).toBe('accepted')
    expect(payload.runtime.stages.flush.state).toBe('skipped')
    expect(payload.runtime.stages.injection.state).toBe('appended')
    expect(payload.capabilities.prepared_context).toBe(true)
    expect(payload.notices).toEqual([])
    bridge.dispose()
  })

  it('reports the verified deployment mode instead of inferring locality from loopback', async () => {
    const bridge = new StatusBridge(options({
      deployment: { mode: 'owner_local_web', ownerLocal: 'explicit' },
    }))
    const payload = await bridge.status()
    expect(payload.deployment).toMatchObject({
      kind: 'local', mode: 'owner_local_web', owner_local: 'explicit', ssh: false,
    })
    bridge.dispose()
  })

  it('never serializes the configured credential or an arbitrary bridge URL', async () => {
    const bridge = new StatusBridge(options({
      config: config({ authorization: 'Bearer test-secret-token' }),
    }))
    const payload = await bridge.status()
    const serialized = JSON.stringify(payload)
    expect(serialized).not.toContain('test-secret-token')
    expect(serialized).not.toContain('Bearer')
    expect(serialized).not.toContain('authorization":"')
    expect(payload.configuration.authorization_configured).toBe(true)
    bridge.dispose()
  })

  it('turns a readiness failure into a red failure with safe dependency names', async () => {
    const bridge = new StatusBridge(options({
      client: clientWith({
        readiness: async () => success({
          status: 'not_ready', checks: { database: 'unavailable', runtime: 'ready' },
        }, 503),
      }) as unknown as StatusBridgeOptions['client'],
    }))
    const payload = await bridge.status()
    expect(payload.connection.state).toBe('failed')
    expect(payload.connection.code).toBe('not_ready')
    expect(payload.connection.message).toContain('database')
    expect(payload.server.readiness.dependencies).toEqual({ database: 'unavailable', runtime: 'ready' })
    expect(payload.capabilities.prepared_context).toBe(false)
    bridge.dispose()
  })

  it('fails required capability when the PreparedContext schema is missing', async () => {
    const bridge = new StatusBridge(options({
      client: clientWith({
        capabilities: async () => success({
          memory_extraction: true, handoff_generation: false,
          source_types: [], artifact_families: [], search_modes: [], context_versions: ['other'],
        }),
      }) as unknown as StatusBridgeOptions['client'],
    }))
    const payload = await bridge.status()
    expect(payload.connection.state).toBe('failed')
    expect(payload.connection.code).toBe('unsupported_context_schema')
    expect(payload.capabilities.prepared_context).toBe(false)
    bridge.dispose()
  })

  it('does not create a session or resolve Scope without a conversation', async () => {
    const resolveScope = vi.fn(async () => 'scope-a')
    const bridge = new StatusBridge(options({ resolveScope }))
    const payload = await bridge.status()
    expect(payload.session).toBeNull()
    expect(payload.runtime.freshness).toBe('not_yet_observed')
    expect(resolveScope).not.toHaveBeenCalled()
    expect(payload.notices).toContain('no_active_session')
    bridge.dispose()
  })

  it('does not leak another session or create one when the selector is unknown', async () => {
    const resolveScope = vi.fn(async () => 'scope-a')
    const bridge = new StatusBridge(options({ resolveScope }))
    const payload = await bridge.status('missing-session')
    expect(payload.session).toBeNull()
    expect(payload.notices).toContain('session_unavailable')
    expect(resolveScope).not.toHaveBeenCalled()
    bridge.dispose()
  })

  it('keeps Scope unresolved when resolution fails while connection stays readable', async () => {
    const now = 1_800_000_000_000
    const status = new RuntimeStatus(() => now)
    const attempt = status.begin('session-1', '/workspace/repo', '7')
    attempt.record('capture', { state: 'accepted' })
    const bridge = new StatusBridge(options({
      status, now: () => now, resolveScope: async () => { throw new Error('offline') },
    }))
    const payload = await bridge.status('session-1')
    expect(payload.session?.scope_state).toBe('unresolved')
    expect(payload.session?.scope_code).toBe('diagnostic_error')
    expect(payload.runtime.freshness).toBe('stale')
    expect(payload.runtime.stale_reason).toBe('scope_unverified')
    bridge.dispose()
  })

  it('reuses a bounded observation instead of probing on every refresh', async () => {
    let now = 1_000
    const client = clientWith()
    const bridge = new StatusBridge(options({ client: client as unknown as StatusBridgeOptions['client'], now: () => now }))
    await bridge.status()
    expect(client.request).toHaveBeenCalledTimes(3)
    now += 5_000
    await bridge.status()
    expect(client.request).toHaveBeenCalledTimes(3)
    now += 10_000
    await bridge.status()
    expect(client.request).toHaveBeenCalledTimes(6)
    bridge.dispose()
  })

  it('returns connecting while the first bounded probe is still in flight', async () => {
    vi.useFakeTimers()
    let resolveLiveness: ((value: ClientSuccess) => void) | undefined
    const liveness = new Promise<ClientSuccess>((resolve) => { resolveLiveness = resolve })
    const bridge = new StatusBridge(options({
      client: { request: vi.fn(async () => liveness) } as unknown as StatusBridgeOptions['client'],
    }))
    const pending = bridge.status()
    await vi.advanceTimersByTimeAsync(2_000)
    const payload = await pending
    expect(payload.connection.state).toBe('connecting')
    expect(payload.connection.in_flight).toBe(true)
    resolveLiveness?.(success({ status: 'ok' }))
    bridge.dispose()
    vi.useRealTimers()
  })
})

describe('status route security', () => {
  it('rejects a connection/origin failure before touching the bridge', async () => {
    const bridge = { status: vi.fn() } as unknown as StatusBridge
    const response = responseMock()
    await createStatusRouteHandler(bridge, connection(401))(requestMock(), response)
    expect(response.statusCode).toBe(401)
    expect(bridge.status).not.toHaveBeenCalled()
    expect(response.headers['cache-control']).toBe('no-store')
  })

  it('rejects a non-loopback request even when DSH accepts the caller', async () => {
    const bridge = { status: vi.fn() } as unknown as StatusBridge
    const response = responseMock()
    await createStatusRouteHandler(bridge, connection())(requestMock({
      socket: { remoteAddress: '10.0.0.8' } as IncomingMessage['socket'],
    }), response)
    expect(response.statusCode).toBe(403)
    expect(response.body).toContain('local_only')
    expect(bridge.status).not.toHaveBeenCalled()
  })

  it('answers GET only and returns a no-store status payload', async () => {
    const bridge = { status: vi.fn(async () => ({ schema: 'x' })) } as unknown as StatusBridge
    const accepted = responseMock()
    await createStatusRouteHandler(bridge, connection())(requestMock(), accepted)
    expect(accepted.statusCode).toBe(200)
    expect(accepted.headers['cache-control']).toBe('no-store')
    expect(accepted.headers['content-type']).toContain('application/json')
    expect(accepted.body).toContain('"schema":"x"')

    const rejected = responseMock()
    await createStatusRouteHandler(bridge, connection())(requestMock({ method: 'POST' }), rejected)
    expect(rejected.statusCode).toBe(405)
    expect(rejected.headers.allow).toBe('GET')
    expect(bridge.status).toHaveBeenCalledTimes(1)
  })

  it('passes only one exact session selector and ignores other query parameters', async () => {
    const bridge = { status: vi.fn(async () => ({ schema: 'x' })) } as unknown as StatusBridge
    const response = responseMock()
    await createStatusRouteHandler(bridge, connection())(requestMock({
      url: '/powercontext/status?session_id=session-1&base_url=https://evil.example&session_id=session-2',
    }), response)
    expect(bridge.status).toHaveBeenCalledWith(undefined)
    expect(response.statusCode).toBe(200)
  })

  it('rejects an unconfigured local Web host even though the socket is loopback', async () => {
    const bridge = { status: vi.fn() } as unknown as StatusBridge
    const response = responseMock()
    await createStatusRouteHandler(bridge, connection(), {
      ssh: false, desktop: false, ownerLocal: undefined,
    })(requestMock(), response)
    expect(response.statusCode).toBe(403)
    expect(response.body).toContain('owner_local_unverified')
    expect(bridge.status).not.toHaveBeenCalled()
  })

  it('rejects an SSH-forwarded host even when its DSH connection is authenticated', async () => {
    const bridge = { status: vi.fn() } as unknown as StatusBridge
    const response = responseMock()
    await createStatusRouteHandler(bridge, connection(), {
      ssh: true, desktop: true, ownerLocal: true,
    })(requestMock(), response)
    expect(response.statusCode).toBe(403)
    expect(response.body).toContain('ssh_untrusted')
    expect(bridge.status).not.toHaveBeenCalled()
  })

  it('admits a local Web host only with the explicit owner assertion', async () => {
    const bridge = { status: vi.fn(async () => ({ schema: 'x' })) } as unknown as StatusBridge
    const rejected = responseMock()
    await createStatusRouteHandler(bridge, connection(), {
      ssh: false, desktop: false, ownerLocal: false,
    })(requestMock(), rejected)
    expect(rejected.statusCode).toBe(403)
    expect(rejected.body).toContain('owner_local_disabled')

    const accepted = responseMock()
    await createStatusRouteHandler(bridge, connection(), {
      ssh: false, desktop: false, ownerLocal: true,
    })(requestMock(), accepted)
    expect(accepted.statusCode).toBe(200)
  })

  it('resolves automatic Desktop and explicit Web without treating SSH as local', () => {
    expect(resolveDeployment({ ssh: false, desktop: true, ownerLocal: undefined }))
      .toEqual({ mode: 'desktop', ownerLocal: 'automatic' })
    expect(resolveDeployment({ ssh: false, desktop: false, ownerLocal: true }))
      .toEqual({ mode: 'owner_local_web', ownerLocal: 'explicit' })
    expect(resolveDeployment({ ssh: true, desktop: true, ownerLocal: true })).toBeNull()
    expect(resolveDeployment({ ssh: false, desktop: false, ownerLocal: undefined })).toBeNull()
    expect(deploymentRejection(requestMock(), { ssh: true, desktop: true, ownerLocal: true }))
      .toMatchObject({ code: 'ssh_untrusted' })
  })

  it('reads SSH markers from the launch environment and Desktop identity from the managed profile', () => {
    const ctx = {
      get: (name: string) => name === 'launchEnvironment'
        ? { getFrom: (key: string, sources: readonly string[]) => key === 'SSH_CONNECTION' && sources.includes('process')
            ? { value: '10.0.0.1 1234 10.0.0.2 22' } : undefined }
        : name === 'profileContext' ? { name: 'desktop' } : undefined,
    }
    expect(localDeploymentFacts(ctx as never, true).ssh).toBe(true)
    expect(localDeploymentFacts(ctx as never, true).ownerLocal).toBe(true)
    expect(localDeploymentFacts(ctx as never, true).desktop).toBe(true)

    const web = { get: (name: string) => name === 'profileContext' ? { name: 'web' } : undefined, }
    expect(localDeploymentFacts(web as never, undefined).desktop).toBe(false)
    expect(resolveDeployment(localDeploymentFacts(web as never, undefined))).toBeNull()
  })

  it('recognizes every IPv4 loopback form but not a public address', () => {
    expect(isLoopbackAddress('127.0.0.1')).toBe(true)
    expect(isLoopbackAddress('127.12.0.4')).toBe(true)
    expect(isLoopbackAddress('::1')).toBe(true)
    expect(isLoopbackAddress('::ffff:127.0.0.1')).toBe(true)
    expect(isLoopbackAddress('10.0.0.8')).toBe(false)
    expect(isLoopbackAddress('::ffff:10.0.0.8')).toBe(false)
    expect(isLoopbackAddress(undefined)).toBe(false)
  })
})

describe('optional host services', () => {
  it('registers nothing until both host services are available and disposes on unload', async () => {
    const effects: Array<() => void> = []
    const routeDisposer = vi.fn()
    const inject = vi.fn((_deps: string[], callback: (host: unknown) => void) => callback({
      get: () => ({ get: () => ({ header: { cwd: '/workspace/repo' } }) }),
      effect: (factory: () => () => void) => {
        const dispose = factory()
        effects.push(dispose)
        return dispose
      },
      webServer: { register: vi.fn(() => routeDisposer) },
      connection: connection(),
    }))
    const ctx = { inject, effect: vi.fn(), get: vi.fn() } as never
    const runtime = {
      config: config(),
      client: clientWith() as unknown as PluginRuntime['client'],
      resolveScope: async () => 'scope-a',
      log: vi.fn(),
    } as unknown as PluginRuntime

    registerStatusBridge(ctx, runtime)
    expect(inject).toHaveBeenCalledWith(['webServer', 'connection'], expect.any(Function))
    expect(effects).toHaveLength(2)
    for (const dispose of effects) dispose()
    expect(routeDisposer).toHaveBeenCalledTimes(1)
  })

  it('stays headless when the optional host services never arrive', () => {
    const ctx = { inject: vi.fn(), effect: vi.fn(), get: vi.fn() } as never
    const runtime = {
      config: config(),
      client: clientWith() as unknown as PluginRuntime['client'],
      resolveScope: async () => 'scope-a',
      log: vi.fn(),
    } as unknown as PluginRuntime
    expect(() => registerStatusBridge(ctx, runtime)).not.toThrow()
    expect(ctx.effect).not.toHaveBeenCalled()
  })
})
