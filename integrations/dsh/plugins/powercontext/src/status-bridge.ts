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
import { isAbsolute } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import { combineSignals, type PowerContextClient } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { operationFailure } from './doctor.ts'
import { PLUGIN_VERSION } from './errors.ts'
import { PREPARED_CONTEXT_SCHEMA } from './prepared-context.ts'
import type { PluginRuntime } from './invoke.ts'
import { RuntimeStatus } from './status.ts'
import { createDashboardRouteHandler, DASHBOARD_ROUTE } from './dashboard-bridge.ts'
import {
  STATUS_SCHEMA, STATUS_STALE_AFTER_MS, packageVersion,
  type BridgeConnectionState, type ProbeState, type StatusCapabilitiesPayload, type StatusPayload,
  type StatusProbePayload, type StatusRuntimePayload, type StatusSessionPayload,
} from './status-contract.ts'

export const STATUS_ROUTE_PATH = '/powercontext/status'
/** Longest the bridge holds a status response open for its first bounded probe. */
export const STATUS_FIRST_PROBE_WAIT_MS = 2_000
/** A completed observation is reusable for this long before another probe starts. */
const READY_PROBE_TTL_MS = 10_000
const FAILED_PROBE_TTL_MS = 5_000
const SCOPE_TTL_MS = 30_000
const MAX_SESSION_ID = 256

interface SessionLike {
  readonly header?: { readonly cwd?: string }
}

interface SessionsService {
  get(id: string): SessionLike | undefined
}

interface ProbeObservation {
  state: ProbeState
  code?: string
  message?: string
  recovery?: string
  http_status?: number
  request_id?: string
  dependencies?: Record<string, string>
  observed_at: number | null
}

interface ScopeObservation {
  scopeId: string | null
  state: 'resolved' | 'unresolved' | 'not_attempted'
  code?: string
  message?: string
  observed_at: number
}

interface ResolvedSession {
  id: string
  cwd?: string
  unavailable: boolean
}

export interface DeploymentFacts {
  /** SSH markers inherited by the DSH host process. */
  readonly ssh: boolean
  /** The DSH Desktop Host runs under Electron; a plain Web Host does not. */
  readonly desktop: boolean
  /** Explicit owner assertion for a local Web host; undefined means unconfigured. */
  readonly ownerLocal: boolean | undefined
}

export interface DeploymentMode {
  readonly mode: 'desktop' | 'owner_local_web'
  readonly ownerLocal: 'automatic' | 'explicit'
}

export type DeploymentRejectionCode =
  | 'local_only' | 'ssh_untrusted' | 'owner_local_unverified' | 'owner_local_disabled'

export interface DeploymentRejection {
  readonly status: 403
  readonly code: DeploymentRejectionCode
  readonly message: string
}

export interface StatusBridgeOptions {
  config: ResolvedConfig
  client: Pick<PowerContextClient, 'request'>
  status?: RuntimeStatus
  resolveScope: (cwd?: string, signal?: AbortSignal) => Promise<string | undefined>
  sessions?: SessionsService
  now?: () => number
  pluginVersion?: string
  deployment?: DeploymentMode
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function safeText(value: unknown, max = 1_024): string | undefined {
  return typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : undefined
}

function safeScopeId(value: string | undefined): string | null {
  return value && /^[a-zA-Z0-9_-]{1,128}$/.test(value) ? value : null
}

function safeDependencies(value: unknown): Record<string, string> | undefined {
  if (!record(value)) return undefined
  const dependencies: Record<string, string> = {}
  for (const [name, state] of Object.entries(value)) {
    if (!/^[a-zA-Z0-9_.:-]{1,64}$/.test(name) || typeof state !== 'string') continue
    dependencies[name] = state.slice(0, 128)
  }
  return Object.keys(dependencies).length > 0 ? dependencies : undefined
}

function unknownProbe(): ProbeObservation {
  return { state: 'unknown', observed_at: null }
}

function okProbe(observedAt: number): ProbeObservation {
  return { state: 'ok', observed_at: observedAt }
}

function failedProbe(operation: string, error: unknown, observedAt: number): ProbeObservation {
  const failure = operationFailure(operation, error)
  return {
    state: 'failed',
    ...(failure.code ? { code: failure.code } : {}),
    ...(failure.message ? { message: failure.message } : {}),
    ...(failure.recovery ? { recovery: failure.recovery } : {}),
    ...(failure.http_status === undefined ? {} : { http_status: failure.http_status }),
    ...(failure.request_id === undefined ? {} : { request_id: failure.request_id }),
    observed_at: observedAt,
  }
}

function probePayload(probe: ProbeObservation, now: number): StatusProbePayload {
  return {
    state: probe.state,
    ...(probe.code === undefined ? {} : { code: probe.code }),
    ...(probe.message === undefined ? {} : { message: probe.message }),
    ...(probe.recovery === undefined ? {} : { recovery: probe.recovery }),
    ...(probe.http_status === undefined ? {} : { http_status: probe.http_status }),
    ...(probe.request_id === undefined ? {} : { request_id: probe.request_id }),
    observed_at: probe.observed_at === null ? null : new Date(probe.observed_at).toISOString(),
    age_ms: probe.observed_at === null ? null : Math.max(0, now - probe.observed_at),
    ...(probe.dependencies === undefined ? {} : { dependencies: probe.dependencies }),
  }
}

/** Loopback only: the bridge is for the owner's local DSH host, never a remote/shared one. */
/** Resolve the owner-local mode a request may use, or null when it must be rejected. */
export function resolveDeployment(facts: DeploymentFacts): DeploymentMode | null {
  if (facts.ssh) return null
  if (facts.desktop) return { mode: 'desktop', ownerLocal: 'automatic' }
  if (facts.ownerLocal === true) return { mode: 'owner_local_web', ownerLocal: 'explicit' }
  return null
}

/**
 * Reject a request unless it reaches a verified local owner surface. Loopback
 * alone is not proof: an SSH-forwarded host or loopback reverse proxy shares
 * the address. Desktop is automatic; plain Web requires an explicit owner
 * assertion. DSH requestRejection still runs first and is never weakened.
 */
export function deploymentRejection(
  request: Pick<IncomingMessage, 'socket'>,
  facts: DeploymentFacts,
): DeploymentRejection | undefined {
  if (!isLoopbackAddress(request.socket.remoteAddress)) {
    return {
      status: 403, code: 'local_only',
      message: 'PowerContext status is available only to the local DSH owner.',
    }
  }
  if (facts.ssh) {
    return {
      status: 403, code: 'ssh_untrusted',
      message: 'This DSH host was launched through SSH; remote and shared hosts are not supported.',
    }
  }
  if (facts.desktop || facts.ownerLocal === true) return undefined
  if (facts.ownerLocal === false) {
    return {
      status: 403, code: 'owner_local_disabled',
      message: 'Owner-local Web access is explicitly disabled for this PowerContext plugin.',
    }
  }
  return {
    status: 403, code: 'owner_local_unverified',
    message: 'Set ownerLocal: true or POWERCONTEXT_DSH_OWNER_LOCAL=true only when this Web host is your own local deployment.',
  }
}

/** Read supported launch facts without assuming every composition provides the snapshot. */
export function localDeploymentFacts(
  ctx: Pick<Context, 'get'>,
  ownerLocal: boolean | undefined,
): DeploymentFacts {
  const environment = ctx.get('launchEnvironment') as {
    getFrom?: (name: string, sources: readonly string[]) => { value: string } | undefined
  } | undefined
  const read = (name: string): string | undefined => {
    try {
      const value = environment?.getFrom?.(name, ['process'])?.value
      return value !== undefined ? value : process.env[name]
    } catch {
      return process.env[name]
    }
  }
  // The managed profile context is the supported Desktop identity. Electron is
  // only an engine: the same executable runs dsh-desktop-host/lib/cli.js and
  // can boot a Web profile, which must not inherit Desktop authority.
  const profile = ctx.get('profileContext') as { name?: unknown } | undefined
  return {
    ssh: ['SSH_CONNECTION', 'SSH_TTY'].some(name => (read(name) ?? '').trim() !== ''),
    desktop: profile?.name === 'desktop',
    ownerLocal,
  }
}

export function isLoopbackAddress(value: string | undefined): boolean {
  if (!value) return false
  const address = value.toLowerCase().startsWith('::ffff:') ? value.slice(7) : value
  if (address === '::1') return true
  const octets = address.split('.')
  return octets.length === 4 && octets[0] === '127'
    && octets.every(octet => /^\d{1,3}$/.test(octet) && Number(octet) <= 255)
}

function readEndpoint(config: ResolvedConfig): { origin: string; pathPrefix: boolean } {
  try {
    const url = new URL(config.baseUrl)
    return { origin: url.origin, pathPrefix: url.pathname !== '/' }
  } catch {
    // The transport policy rejects invalid URLs; this remains a closed fallback.
    return { origin: '', pathPrefix: false }
  }
}

/**
 * The authenticated plugin-owned status bridge. It performs only read-only
 * health/readiness/capability and Scope resolution calls: never prepare,
 * capture, generation, MCP, or `/pc doctor`.
 */
export class StatusBridge {
  private readonly options: StatusBridgeOptions
  private readonly now: () => number
  private packageVersion: string | null = null
  private liveness = unknownProbe()
  private readiness = unknownProbe()
  private capabilities = unknownProbe()
  private connectionState: BridgeConnectionState = 'connecting'
  private lastCompletedAt: number | null = null
  private nextProbeAt = 0
  private generation = 0
  private inFlight: Promise<void> | null = null
  private readonly abort = new AbortController()
  private readonly scopes = new Map<string, ScopeObservation>()
  private disposed = false

  constructor(options: StatusBridgeOptions) {
    this.options = options
    this.now = options.now ?? Date.now
  }

  /** Stop pending probes; called by the plugin effect's disposer. */
  dispose(): void {
    this.disposed = true
    this.abort.abort()
    this.inFlight = null
    this.scopes.clear()
  }

  /**
   * Read the current connection, Scope, and local automatic-path observations.
   * @param sessionId - optional selector for the owner's active DSH Session.
   */
  async status(sessionId?: string): Promise<StatusPayload> {
    const waiting = this.ensureProbe()
    if (waiting !== null) await this.waitFor(waiting, STATUS_FIRST_PROBE_WAIT_MS)
    const now = this.now()
    const session = this.resolveSession(sessionId)
    const scope = session === null || session.unavailable
      ? { scopeId: null, state: 'not_attempted' as const, observed_at: now }
      : await this.resolveScope(session, now)
    const runtime = this.runtime(session, scope)
    const endpoint = readEndpoint(this.options.config)
    const connection = this.connection(now)
    const notices: string[] = []
    if (session === null) notices.push('no_active_session')
    else if (session.unavailable) notices.push('session_unavailable')
    else if (scope.state === 'unresolved') notices.push('scope_unresolved')
    if (session === null && sessionId !== undefined) notices.push('session_service_unavailable')
    return {
      schema: STATUS_SCHEMA,
      observed_at: new Date(now).toISOString(),
      plugin_version: this.options.pluginVersion ?? PLUGIN_VERSION,
      deployment: {
        kind: 'local',
        mode: this.options.deployment?.mode ?? 'desktop',
        owner_local: this.options.deployment?.ownerLocal ?? 'automatic',
        ssh: false,
        bridge: 'available',
        client: 'packaged',
      },
      configuration: {
        endpoint_origin: endpoint.origin,
        endpoint_source: this.options.config.sources.baseUrl,
        path_prefix: endpoint.pathPrefix,
        authorization_configured: Boolean(this.options.config.authorization),
        authorization_source: this.options.config.sources.authorization,
        scope_source: this.options.config.sources.scopeId,
        request_timeout_ms: this.options.config.requestTimeoutMs,
      },
      connection,
      session: this.sessionPayload(session, scope),
      runtime,
      server: {
        package_version: this.packageVersion,
        liveness: probePayload(this.liveness, now),
        readiness: probePayload(this.readiness, now),
        capabilities: probePayload(this.capabilities, now),
      },
      capabilities: this.capabilityPayload(),
      notices,
    }
  }

  private connection(now: number): StatusPayload['connection'] {
    const failure = this.connectionState === 'failed'
      ? [this.liveness, this.readiness, this.capabilities].find(probe => probe.state === 'failed')
      : undefined
    return {
      state: this.connectionState,
      observed_at: this.lastCompletedAt === null ? null : new Date(this.lastCompletedAt).toISOString(),
      age_ms: this.lastCompletedAt === null ? null : Math.max(0, now - this.lastCompletedAt),
      stale: this.lastCompletedAt !== null && now - this.lastCompletedAt >= STATUS_STALE_AFTER_MS,
      stale_after_ms: STATUS_STALE_AFTER_MS,
      generation: this.generation,
      in_flight: this.inFlight !== null,
      ...(failure?.code === undefined ? {} : { code: failure.code }),
      ...(failure?.message === undefined ? {} : { message: failure.message }),
      ...(failure?.recovery === undefined ? {} : { recovery: failure.recovery }),
      ...(failure?.http_status === undefined ? {} : { http_status: failure.http_status }),
    }
  }

  private capabilityPayload(): StatusCapabilitiesPayload {
    const value = this.capabilities as ProbeObservation & {
      contextVersions?: unknown
      memoryExtraction?: unknown
      handoffGeneration?: unknown
    }
    const contextVersions = Array.isArray(value.contextVersions)
      ? value.contextVersions.filter((item): item is string => typeof item === 'string').slice(0, 32)
      : []
    return {
      prepared_context: contextVersions.includes(PREPARED_CONTEXT_SCHEMA),
      context_versions: contextVersions,
      memory_extraction: typeof value.memoryExtraction === 'boolean' ? value.memoryExtraction : null,
      handoff_generation: typeof value.handoffGeneration === 'boolean' ? value.handoffGeneration : null,
    }
  }

  private resolveSession(sessionId: string | undefined): ResolvedSession | null {
    if (sessionId === undefined) return null
    const id = sessionId.trim()
    if (!id || id.length > MAX_SESSION_ID || !/^[a-zA-Z0-9._:-]+$/.test(id)) return { id, unavailable: true }
    if (this.options.sessions === undefined) return { id, unavailable: true }
    const session = this.options.sessions.get(id)
    if (session === undefined) return { id, unavailable: true }
    const cwd = session.header?.cwd
    return { id, unavailable: false, ...(typeof cwd === 'string' && isAbsolute(cwd) ? { cwd } : {}) }
  }

  private async resolveScope(session: ResolvedSession, now: number): Promise<ScopeObservation> {
    const key = `${session.id}\u0000${session.cwd ?? ''}`
    const cached = this.scopes.get(key)
    if (cached !== undefined && now - cached.observed_at < SCOPE_TTL_MS) return cached
    try {
      const scopeId = await this.options.resolveScope(session.cwd, this.abort.signal)
      const valid = safeScopeId(scopeId)
      const observation: ScopeObservation = valid === null
        ? {
            scopeId: null,
            state: 'unresolved',
            code: 'scope_unavailable',
            message: 'The Server did not return a usable Scope for this Session.',
            observed_at: now,
          }
        : { scopeId: valid, state: 'resolved', observed_at: now }
      this.scopes.set(key, observation)
      return observation
    } catch (error) {
      const failure = operationFailure('resolve_scope_binding', error)
      const observation: ScopeObservation = {
        scopeId: null,
        state: 'unresolved',
        ...(failure.code ? { code: failure.code } : {}),
        ...(failure.message ? { message: failure.message } : {}),
        observed_at: now,
      }
      this.scopes.set(key, observation)
      return observation
    }
  }

  private runtime(session: ResolvedSession | null, scope: ScopeObservation): StatusRuntimePayload {
    const status = this.options.status
    if (session === null || session.unavailable) return new RuntimeStatus().read(undefined, undefined) as StatusRuntimePayload
    const read = status?.read(session.id, session.cwd, scope.scopeId ?? undefined) ?? new RuntimeStatus().read(undefined, undefined)
    return read as StatusRuntimePayload
  }

  private sessionPayload(session: ResolvedSession | null, scope: ScopeObservation): StatusSessionPayload | null {
    if (session === null || session.unavailable) return null
    return {
      id: session.id,
      scope_id: scope.scopeId,
      scope_state: scope.state,
      ...(scope.code === undefined ? {} : { scope_code: scope.code }),
      ...(scope.message === undefined ? {} : { scope_message: scope.message }),
      ...(session.cwd === undefined ? {} : { cwd: session.cwd }),
    }
  }

  private ensureProbe(): Promise<void> | null {
    if (this.disposed) return null
    if (this.inFlight !== null) return this.inFlight
    const now = this.now()
    if (now < this.nextProbeAt) return null
    const started = this.probe()
    this.inFlight = started
    return started
  }

  private async waitFor(probe: Promise<void>, milliseconds: number): Promise<void> {
    let timer: ReturnType<typeof setTimeout> | undefined
    await Promise.race([
      probe,
      new Promise<void>((resolve) => {
        timer = setTimeout(resolve, milliseconds)
      }),
    ])
    if (timer !== undefined) clearTimeout(timer)
  }

  private async probe(): Promise<void> {
    this.generation += 1
    this.packageVersion = null
    try {
      const liveness = await this.probeLiveness()
      if (this.disposed) return
      this.liveness = liveness
      if (liveness.state !== 'ok') {
        this.readiness = unknownProbe()
        this.capabilities = unknownProbe()
        this.connectionState = 'failed'
        return
      }
      // Optional metadata: old Servers and failed metadata reads must not break readiness.
      await this.readPackageVersion()
      const readiness = await this.probeReadiness()
      if (this.disposed) return
      this.readiness = readiness
      if (readiness.state !== 'ok') {
        this.capabilities = unknownProbe()
        this.connectionState = 'failed'
        return
      }
      const capabilities = await this.probeCapabilities()
      if (this.disposed) return
      this.capabilities = capabilities
      this.connectionState = capabilities.state === 'ok' ? 'ready' : 'failed'
    } catch (error) {
      // `probeLiveness`/`probeReadiness`/`probeCapabilities` already normalize
      // their own failures; this is the last-resort boundary for the bridge.
      this.liveness = failedProbe('get_liveness', error, this.now())
      this.readiness = unknownProbe()
      this.capabilities = unknownProbe()
      this.connectionState = 'failed'
    } finally {
      if (!this.disposed) {
        const now = this.now()
        this.lastCompletedAt = now
        this.nextProbeAt = now + (this.connectionState === 'ready' ? READY_PROBE_TTL_MS : FAILED_PROBE_TTL_MS)
      }
      this.inFlight = null
    }
  }

  private async readPackageVersion(): Promise<void> {
    try {
      const version = await readServerPackageVersion(this.options.client, this.abort.signal)
      if (!this.disposed) this.packageVersion = version
    } catch {
      this.packageVersion = null
    }
  }

  private async probeLiveness(): Promise<ProbeObservation> {
    const observedAt = this.now()
    try {
      const response = await this.options.client.request('get_liveness', {}, this.abort.signal)
      const value = response.value
      if (response.status !== 200 || !record(value) || value.status !== 'ok') {
        throw new TypeError('Liveness requires HTTP 200 and status ok')
      }
      return okProbe(observedAt)
    } catch (error) {
      return failedProbe('get_liveness', error, this.now())
    }
  }

  private async probeReadiness(): Promise<ProbeObservation> {
    try {
      const response = await this.options.client.request('get_readiness', {}, this.abort.signal, { readinessResponse: true })
      const value = response.value
      if (!record(value) || !record(value.checks) || typeof value.status !== 'string'
        || !['ready', 'degraded', 'not_ready'].includes(value.status)
        || Object.values(value.checks).some(check => typeof check !== 'string')) {
        throw new TypeError('Readiness response does not match its schema')
      }
      const dependencies = safeDependencies(value.checks)
      const observedAt = this.now()
      if (value.status === 'ready') return { ...okProbe(observedAt), ...(dependencies ? { dependencies } : {}) }
      const failed = dependencies
        ? Object.entries(dependencies).filter(([, state]) => !['ready', 'disabled'].includes(state)).map(([name]) => name)
        : []
      return {
        state: 'failed',
        code: value.status === 'not_ready' ? 'not_ready' : 'degraded',
        message: failed.length > 0
          ? `The Server reports ${value.status}: ${failed.join(', ')}.`
          : `The Server reports ${value.status}.`,
        recovery: failed.length > 0
          ? `Check the Server readiness dependencies: ${failed.join(', ')}.`
          : 'Inspect the running Server readiness and service logs.',
        ...(dependencies ? { dependencies } : {}),
        observed_at: observedAt,
      }
    } catch (error) {
      return failedProbe('get_readiness', error, this.now())
    }
  }

  private async probeCapabilities(): Promise<ProbeObservation & {
    contextVersions?: string[]
    memoryExtraction?: boolean
    handoffGeneration?: boolean
  }> {
    try {
      const response = await this.options.client.request('get_capabilities', {}, this.abort.signal)
      const value = response.value
      if (response.status !== 200 || !record(value)
        || typeof value.memory_extraction !== 'boolean' || typeof value.handoff_generation !== 'boolean'
        || !['source_types', 'artifact_families', 'search_modes', 'context_versions'].every(key =>
          Array.isArray(value[key]) && (value[key] as unknown[]).every(item => typeof item === 'string'))) {
        throw new TypeError('Capabilities response does not match its schema')
      }
      const contextVersions = (value.context_versions as string[]).slice(0, 32)
      const observedAt = this.now()
      if (!contextVersions.includes(PREPARED_CONTEXT_SCHEMA)) {
        return {
          state: 'failed', code: 'unsupported_context_schema',
          message: 'The Server does not advertise the PreparedContext schema required by this plugin.',
          recovery: 'Install matching PowerContext Server and DSH plugin versions.',
          contextVersions, memoryExtraction: value.memory_extraction, handoffGeneration: value.handoff_generation,
          observed_at: observedAt,
        }
      }
      return {
        ...okProbe(observedAt), contextVersions,
        memoryExtraction: value.memory_extraction, handoffGeneration: value.handoff_generation,
      }
    } catch (error) {
      return failedProbe('get_capabilities', error, this.now())
    }
  }
}

/** Read the running Server package identity without treating metadata as readiness. */
export async function readServerPackageVersion(client: Pick<PowerContextClient, 'request'>, signal?: AbortSignal): Promise<string | null> {
  const response = await client.request('get_server_info', {},
    combineSignals(signal ? [signal, AbortSignal.timeout(1_000)] : [AbortSignal.timeout(1_000)]))
  return response.status === 200 && record(response.value) ? packageVersion(response.value.package_version) : null
}

export function sendJson(response: ServerResponse, status: number, payload: unknown): void {
  if (response.writableEnded) return
  response.statusCode = status
  response.setHeader('content-type', 'application/json; charset=utf-8')
  response.setHeader('cache-control', 'no-store')
  response.setHeader('x-content-type-options', 'nosniff')
  response.end(JSON.stringify(payload))
}

function sendMethodNotAllowed(response: ServerResponse): void {
  response.statusCode = 405
  response.setHeader('allow', 'GET')
  response.end()
}

export interface UiHostContext extends Context {
  webServer: { register(route: {
    kind: 'exact'
    path: string
    handler: (request: IncomingMessage, response: ServerResponse) => void | Promise<void>
  }): () => void }
  connection: {
    requestRejection(request: { headers: IncomingMessage['headers'] }): 401 | 403 | undefined
    admit?(request: { headers: IncomingMessage['headers'] }): { peer?: { principal?: { kind?: string } } }
  }
}

export function createStatusRouteHandler(
  bridge: StatusBridge,
  connection: UiHostContext['connection'],
  facts: DeploymentFacts = { ssh: false, desktop: true, ownerLocal: undefined },
): (request: IncomingMessage, response: ServerResponse) => Promise<void> {
  return async (request, response) => {
    const rejection = connection.requestRejection(request)
    if (rejection !== undefined) {
      sendJson(response, rejection, {
        code: rejection === 401 ? 'dsh_authentication_required' : 'dsh_forbidden',
        message: 'DSH authentication is required before reading PowerContext status.',
      })
      return
    }
    const deployment = deploymentRejection(request, facts)
    if (deployment !== undefined) {
      sendJson(response, deployment.status, { code: deployment.code, message: deployment.message })
      return
    }
    if (request.method !== 'GET') {
      sendMethodNotAllowed(response)
      return
    }
    try {
      const url = new URL(String(request.url), 'http://localhost')
      const sessionIds = url.searchParams.getAll('session_id')
      const sessionId = sessionIds.length > 1 ? undefined : sessionIds[0]
      sendJson(response, 200, await bridge.status(sessionId))
    } catch {
      sendJson(response, 500, {
        code: 'status_bridge_error',
        message: 'The PowerContext status bridge could not complete its read-only checks.',
      })
    }
  }
}

/**
 * Register the optional Host bridge. Headless compositions without `webServer`
 * or `connection` never enter this callback, so tools and lifecycle hooks stay
 * available unchanged.
 */
export function registerStatusBridge(ctx: Context, runtime: PluginRuntime): void {
  // Test/embedding contexts may expose only the agent-loop services; the UI
  // bridge is optional and must not make headless activation depend on Cordis
  // deferred-injection machinery.
  if (typeof ctx.inject !== 'function') return
  ctx.inject(['webServer', 'connection'], (host) => {
    const ui = host as UiHostContext
    const sessions = (ui.get('sessions') as SessionsService | undefined)
    const deploymentFacts = localDeploymentFacts(ui, runtime.config.ownerLocal)
    const deployment = resolveDeployment(deploymentFacts)
    const bridge = new StatusBridge({
      config: runtime.config,
      client: runtime.client,
      status: runtime.status,
      resolveScope: runtime.resolveScope,
      ...(sessions === undefined ? {} : { sessions }),
      ...(deployment === null ? {} : { deployment }),
    })
    host.effect(() => ui.webServer.register({
      kind: 'exact',
      path: STATUS_ROUTE_PATH,
      handler: createStatusRouteHandler(bridge, ui.connection, deploymentFacts),
    }), 'powercontext: GET /powercontext/status')
    host.effect(() => ui.webServer.register({
      kind: 'exact',
      path: DASHBOARD_ROUTE,
      handler: createDashboardRouteHandler(bridge, runtime.config, ui.connection, deploymentFacts),
    }), 'powercontext: GET /powercontext/dashboard')
    host.effect(() => () => bridge.dispose(), 'powercontext: status bridge disposal')
  })
}
