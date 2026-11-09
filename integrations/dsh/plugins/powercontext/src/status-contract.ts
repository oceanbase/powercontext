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

/**
 * Wire contract between the plugin Host status bridge and its browser entry.
 *
 * The Host owns every field below. The browser parser reconstructs exactly the
 * documented fields instead of spreading the response, so an accidentally added
 * credential, path, or provider payload cannot reach the diagnostics panel.
 */

export const STATUS_SCHEMA = 'powercontext.dsh-status.v1'
export const STATUS_ROUTE = '/powercontext/status'
export const STATUS_REFRESH_INTERVAL_MS = 10_000
export const STATUS_STALE_AFTER_MS = 30_000
export const STATUS_REQUEST_TIMEOUT_MS = 8_000
export const STATUS_CONNECTING_RETRY_MS = 2_000
export const STATUS_RETRY_DELAYS_MS = [2_000, 4_000, 8_000, 16_000, 30_000] as const

export type BridgeConnectionState = 'unconfigured' | 'connecting' | 'ready' | 'failed'
export type DeploymentMode = 'desktop' | 'owner_local_web'
export type OwnerLocalSource = 'automatic' | 'explicit'
export type DisplayState = 'unconfigured' | 'connecting' | 'stale' | 'ready' | 'failed'
export type ProbeState = 'ok' | 'failed' | 'unknown'
export type StageState =
  | 'not_yet_observed' | 'running' | 'resolved' | 'ready' | 'empty' | 'accepted'
  | 'completed' | 'incomplete' | 'appended' | 'skipped' | 'unavailable'

export interface StatusProbePayload {
  state: ProbeState
  code?: string
  message?: string
  recovery?: string
  http_status?: number
  request_id?: string
  observed_at: string | null
  age_ms: number | null
  dependencies?: Record<string, string>
}

export interface StatusStagePayload {
  operation: string
  state: StageState
  code?: string
  message?: string
  recovery?: string
  http_status?: number
  request_id?: string
  protocol_issue?: string
  content_bytes?: number
  confirmation?: 'rejected' | 'unconfirmed'
  observed_at: string | null
  age_ms: number | null
}

export interface StatusRuntimePayload {
  observation: string
  freshness: 'not_yet_observed' | 'current' | 'stale'
  stale_reason?: string
  reason?: string
  attempt: number | null
  turn: string | null
  started_at: string | null
  age_ms: number | null
  stale_after_ms: number
  observed_scope: string | null
  stages: Record<string, StatusStagePayload>
  coverage: string
}

export interface StatusSessionPayload {
  id: string
  scope_id: string | null
  scope_state: 'resolved' | 'unresolved' | 'not_attempted'
  scope_code?: string
  scope_message?: string
  cwd?: string
}

export interface StatusConfigurationPayload {
  endpoint_origin: string
  endpoint_source: string
  path_prefix: boolean
  authorization_configured: boolean
  authorization_source: string
  scope_source: string
  request_timeout_ms: number
}

export interface StatusCapabilitiesPayload {
  prepared_context: boolean
  context_versions: string[]
  memory_extraction: boolean | null
  handoff_generation: boolean | null
}

export interface StatusPayload {
  schema: typeof STATUS_SCHEMA
  observed_at: string
  plugin_version: string
  deployment: {
    kind: 'local'
    mode: DeploymentMode
    owner_local: OwnerLocalSource
    ssh: false
    bridge: 'available'
    client: 'packaged'
  }
  configuration: StatusConfigurationPayload
  connection: {
    state: BridgeConnectionState
    observed_at: string | null
    age_ms: number | null
    stale: boolean
    stale_after_ms: number
    generation: number
    in_flight: boolean
    code?: string
    message?: string
    recovery?: string
    http_status?: number
  }
  session: StatusSessionPayload | null
  runtime: StatusRuntimePayload
  server: {
    liveness: StatusProbePayload
    readiness: StatusProbePayload
    capabilities: StatusProbePayload
  }
  capabilities: StatusCapabilitiesPayload
  notices: string[]
}

const STAGE_KEYS = ['scope', 'prepare', 'capture', 'flush', 'injection'] as const
const CONNECTION_STATES = new Set<BridgeConnectionState>(['unconfigured', 'connecting', 'ready', 'failed'])
const PROBE_STATES = new Set<ProbeState>(['ok', 'failed', 'unknown'])
const STAGE_STATES = new Set<StageState>([
  'not_yet_observed', 'running', 'resolved', 'ready', 'empty', 'accepted',
  'completed', 'incomplete', 'appended', 'skipped', 'unavailable',
])
const MAX_TEXT = 2_048

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function text(value: unknown, max = MAX_TEXT): string | undefined {
  return typeof value === 'string' && value.length > 0 ? value.slice(0, max) : undefined
}

function finiteNumber(value: unknown): number | undefined {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined
}

function nullableText(value: unknown): string | null {
  return typeof value === 'string' ? value : null
}

function nullableNumber(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

function stringRecord(value: unknown): Record<string, string> | undefined {
  if (!record(value)) return undefined
  const result: Record<string, string> = {}
  for (const [key, item] of Object.entries(value)) {
    if (typeof item === 'string' && /^[a-zA-Z0-9._:-]{1,64}$/.test(key)) result[key] = item.slice(0, 256)
  }
  return result
}

function parseProbe(value: unknown): StatusProbePayload | null {
  if (!record(value) || !PROBE_STATES.has(value.state as ProbeState)) return null
  return {
    state: value.state as ProbeState,
    ...text(value.code) === undefined ? {} : { code: text(value.code)! },
    ...text(value.message) === undefined ? {} : { message: text(value.message)! },
    ...text(value.recovery) === undefined ? {} : { recovery: text(value.recovery)! },
    ...finiteNumber(value.http_status) === undefined ? {} : { http_status: finiteNumber(value.http_status)! },
    ...text(value.request_id, 128) === undefined ? {} : { request_id: text(value.request_id, 128)! },
    observed_at: nullableText(value.observed_at),
    age_ms: nullableNumber(value.age_ms),
    ...stringRecord(value.dependencies) === undefined ? {} : { dependencies: stringRecord(value.dependencies)! },
  }
}

function parseStage(value: unknown): StatusStagePayload | null {
  if (!record(value) || !STAGE_STATES.has(value.state as StageState)) return null
  return {
    operation: text(value.operation, 128) ?? 'unknown',
    state: value.state as StageState,
    ...text(value.code) === undefined ? {} : { code: text(value.code)! },
    ...text(value.message) === undefined ? {} : { message: text(value.message)! },
    ...text(value.recovery) === undefined ? {} : { recovery: text(value.recovery)! },
    ...finiteNumber(value.http_status) === undefined ? {} : { http_status: finiteNumber(value.http_status)! },
    ...text(value.request_id, 128) === undefined ? {} : { request_id: text(value.request_id, 128)! },
    ...text(value.protocol_issue, 128) === undefined ? {} : { protocol_issue: text(value.protocol_issue, 128)! },
    ...finiteNumber(value.content_bytes) === undefined ? {} : { content_bytes: finiteNumber(value.content_bytes)! },
    ...(value.confirmation === 'rejected' || value.confirmation === 'unconfirmed'
      ? { confirmation: value.confirmation } : {}),
    observed_at: nullableText(value.observed_at),
    age_ms: nullableNumber(value.age_ms),
  }
}

function parseRuntime(value: unknown): StatusRuntimePayload | null {
  if (!record(value) || !record(value.stages)) return null
  const stages: Record<string, StatusStagePayload> = {}
  for (const key of STAGE_KEYS) {
    const stage = parseStage(value.stages[key])
    if (stage !== null) stages[key] = stage
  }
  for (const key of STAGE_KEYS) {
    if (stages[key] === undefined) return null
  }
  const freshness = value.freshness
  if (freshness !== 'not_yet_observed' && freshness !== 'current' && freshness !== 'stale') return null
  return {
    observation: text(value.observation, 128) ?? 'local_automatic_path',
    freshness,
    ...text(value.stale_reason) === undefined ? {} : { stale_reason: text(value.stale_reason)! },
    ...text(value.reason) === undefined ? {} : { reason: text(value.reason)! },
    attempt: finiteNumber(value.attempt) ?? null,
    turn: typeof value.turn === 'string' ? value.turn.slice(0, 32) : null,
    started_at: nullableText(value.started_at),
    age_ms: nullableNumber(value.age_ms),
    stale_after_ms: finiteNumber(value.stale_after_ms) ?? 300_000,
    observed_scope: nullableText(value.observed_scope),
    stages,
    coverage: text(value.coverage, 4_096) ?? '',
  }
}

function parseSession(value: unknown): StatusSessionPayload | null {
  if (value === null) return null
  if (!record(value) || typeof value.id !== 'string') return null
  const scopeState = value.scope_state
  if (scopeState !== 'resolved' && scopeState !== 'unresolved' && scopeState !== 'not_attempted') return null
  return {
    id: value.id.slice(0, 256),
    scope_id: nullableText(value.scope_id),
    scope_state: scopeState,
    ...text(value.scope_code) === undefined ? {} : { scope_code: text(value.scope_code)! },
    ...text(value.scope_message) === undefined ? {} : { scope_message: text(value.scope_message)! },
    ...text(value.cwd, 4_096) === undefined ? {} : { cwd: text(value.cwd, 4_096)! },
  }
}

/**
 * Rebuild a status payload from the exact documented fields.
 * Unknown or malformed members are dropped; a malformed envelope returns null.
 */
export function parseStatusPayload(value: unknown): StatusPayload | null {
  if (!record(value) || value.schema !== STATUS_SCHEMA) return null
  const configuration = value.configuration
  const connection = value.connection
  const deployment = value.deployment
  const server = value.server
  const capabilities = value.capabilities
  if (!record(configuration) || !record(connection) || !record(deployment)
    || !record(server) || !record(capabilities)) return null
  if (!CONNECTION_STATES.has(connection.state as BridgeConnectionState)) return null
  const liveness = parseProbe(server.liveness)
  const readiness = parseProbe(server.readiness)
  const capabilityProbe = parseProbe(server.capabilities)
  const runtime = parseRuntime(value.runtime)
  if (liveness === null || readiness === null || capabilityProbe === null || runtime === null) return null
  const contextVersions = Array.isArray(capabilities.context_versions)
    ? capabilities.context_versions.filter((item): item is string => typeof item === 'string').slice(0, 32)
    : []
  const notices = Array.isArray(value.notices)
    ? value.notices.filter((item): item is string => typeof item === 'string').slice(0, 16)
    : []
  return {
    schema: STATUS_SCHEMA,
    observed_at: typeof value.observed_at === 'string' ? value.observed_at : new Date().toISOString(),
    plugin_version: text(value.plugin_version, 128) ?? 'unknown',
    deployment: {
      kind: 'local',
      mode: deployment.mode === 'owner_local_web' ? 'owner_local_web' : 'desktop',
      owner_local: deployment.owner_local === 'explicit' ? 'explicit' : 'automatic',
      ssh: false,
      bridge: 'available',
      client: 'packaged',
    },
    configuration: {
      endpoint_origin: text(configuration.endpoint_origin, 512) ?? '',
      endpoint_source: text(configuration.endpoint_source, 32) ?? 'unknown',
      path_prefix: configuration.path_prefix === true,
      authorization_configured: configuration.authorization_configured === true,
      authorization_source: text(configuration.authorization_source, 32) ?? 'unknown',
      scope_source: text(configuration.scope_source, 32) ?? 'unknown',
      request_timeout_ms: finiteNumber(configuration.request_timeout_ms) ?? 0,
    },
    connection: {
      state: connection.state as BridgeConnectionState,
      observed_at: nullableText(connection.observed_at),
      age_ms: nullableNumber(connection.age_ms),
      stale: connection.stale === true,
      stale_after_ms: finiteNumber(connection.stale_after_ms) ?? STATUS_STALE_AFTER_MS,
      generation: finiteNumber(connection.generation) ?? 0,
      in_flight: connection.in_flight === true,
      ...text(connection.code) === undefined ? {} : { code: text(connection.code)! },
      ...text(connection.message) === undefined ? {} : { message: text(connection.message)! },
      ...text(connection.recovery) === undefined ? {} : { recovery: text(connection.recovery)! },
      ...finiteNumber(connection.http_status) === undefined ? {} : { http_status: finiteNumber(connection.http_status)! },
    },
    session: parseSession(value.session),
    runtime,
    server: { liveness, readiness, capabilities: capabilityProbe },
    capabilities: {
      prepared_context: capabilities.prepared_context === true,
      context_versions: contextVersions,
      memory_extraction: typeof capabilities.memory_extraction === 'boolean' ? capabilities.memory_extraction : null,
      handoff_generation: typeof capabilities.handoff_generation === 'boolean' ? capabilities.handoff_generation : null,
    },
    notices,
  }
}
