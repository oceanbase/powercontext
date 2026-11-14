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


import { spawnSync } from 'node:child_process'
import { createHmac, randomBytes } from 'node:crypto'
import { closeSync, constants, fstatSync, fsyncSync, openSync, readFileSync, writeFileSync } from 'node:fs'
import type { IncomingMessage, ServerResponse } from 'node:http'
import { homedir } from 'node:os'
import { isAbsolute, join } from 'node:path'
import type { Context } from '@deepseek-ai/cordis'
import type { PluginRuntime } from '../invoke.ts'
import { isLoopbackAddress, localDeploymentFacts, resolveDeployment } from '../status-bridge.ts'
import { createHandoffServices } from './assembly.ts'
import { createDefaultTargetAdapters } from './adapters/defaults.ts'
import { createBoundaryReader } from './boundary-reader.ts'
import type { CallContext, SourceBoundary } from './contracts.ts'
import { HandoffError } from './errors.ts'
import { keyFor, privateDirectory } from './records.ts'
import { createHandoffHandler } from './routes.ts'
import { TargetCatalog, type TargetAdapter } from './targets.ts'

const COLLECTIONS = ['/powercontext/targets', '/powercontext/preflights', '/powercontext/deliveries', '/powercontext/boundaries'] as const
const MAX_SOURCE_EVENTS = 512

interface SessionEvent {
  readonly type: string
  readonly seq: number
  readonly time: number
  readonly data?: {
    readonly turn?: number
    readonly reason?: { readonly kind?: string }
    readonly interrupted?: boolean
    readonly message?: { readonly id?: string; readonly content?: readonly { readonly type?: string; readonly text?: string }[] }
  }
}

interface Session {
  readonly header: { readonly id: string; readonly origin?: string; readonly cwd?: string }
  readonly seq: number
  snapshotEvents(from: number, toExclusive: number): readonly SessionEvent[]
}

interface Profile {
  readonly name: string
  readonly home: string
}

interface Host extends Context {
  readonly webServer: {
    readonly host?: string
    register(route: { kind: 'exact' | 'prefix'; path: string
      handler: (request: IncomingMessage, response: ServerResponse) => Promise<void> }): () => void
  }
  readonly connection: {
    requestRejection(request: IncomingMessage): 401 | 403 | undefined
    /** Newer DSH versions expose the authenticated principal; v4 browser auth only admits the owner. */
    admit?(request: IncomingMessage): { peer?: { principal?: { kind?: string } } }
    readonly allowsRemoteAuthorities?: boolean
  }
}

function loopbackHost(value: string | undefined): boolean {
  if (!value) return false
  return value === 'localhost' || isLoopbackAddress(value.replace(/^\[|\]$/gu, ''))
}

function selectedReply(events: readonly SessionEvent[], id: string, throughSeq: number) {
  const settled = new Set(events.filter(event => event.type === 'turn/end' && event.data?.reason?.kind === 'completed')
    .map(event => event.data?.turn))
  const candidates = events.filter(event => event.type === 'assistant/message' && event.data?.turn !== undefined
    && settled.has(event.data.turn) && !event.data.interrupted
    && event.data.message?.content?.some(part => part.type === 'text' && Boolean(part.text?.trim())))
  const selected = candidates.find(event => event.data?.message?.id === id && event.seq <= throughSeq)
  if (selected === undefined || events.some(event => event.seq > selected.seq && event.seq <= throughSeq
    && (!['step/end', 'turn/end'].includes(event.type) || event.data?.turn !== selected.data?.turn))
    || candidates.some(event => event.seq > selected.seq && event.data?.turn === selected.data?.turn)) {
    throw new HandoffError('stale_preflight')
  }
  return { selected, latest: candidates.at(-1) }
}

/** Resolve a UI message identity from Host-owned events; clients never supply its sequence or time. */
export function resolveHostBoundary(host: Pick<Context, 'get'>, context: CallContext,
  sessionId: string, messageId: string): SourceBoundary {
  context.signal.throwIfAborted()
  const sessions = host.get('sessions') as { get(id: string): Session | undefined } | undefined
  const session = sessions?.get(sessionId)
  if (!session || session.header.id !== sessionId || session.header.origin === 'subagent') {
    throw new HandoffError('not_found')
  }
  const workingDirectory = host.get('workingDirectory') as { get?: (session: Session) => string } | undefined
  if (!workingDirectory?.get || !Number.isSafeInteger(session.seq)) throw new HandoffError('integration_unavailable')
  const events = session.snapshotEvents(Math.max(0, session.seq - MAX_SOURCE_EVENTS), session.seq)
  const reply = events.find(event => event.type === 'assistant/message' && event.data?.message?.id === messageId)
  const end = reply && events.find(event => event.seq > reply.seq && event.type === 'turn/end'
    && event.data?.turn === reply.data?.turn && event.data?.reason?.kind === 'completed')
  if (!reply || !end || !Number.isSafeInteger(end.time)) throw new HandoffError('stale_preflight')
  const source = { sessionId, messageId, throughSeq: end.seq, cwd: workingDirectory.get(session),
    workspaceObservedAt: new Date(end.time).toISOString() }
  verifyHostSource(host, context, source, false)
  return source
}

/** An owner-local Host session is the authority for the selected response and effective directory. */
export function verifyHostSource(host: Pick<Context, 'get'>, context: CallContext, source: SourceBoundary, requireLatest: boolean) {
  context.signal.throwIfAborted()
  const sessions = host.get('sessions') as { get(id: string): Session | undefined } | undefined
  const session = sessions?.get(source.sessionId)
  if (!session || session.header.id !== source.sessionId || session.header.origin === 'subagent') {
    throw new HandoffError('not_found')
  }
  const workingDirectory = host.get('workingDirectory') as { get?: (session: Session) => string } | undefined
  if (!workingDirectory?.get) throw new HandoffError('integration_unavailable')
  if (workingDirectory.get(session) !== source.cwd || !Number.isSafeInteger(session.seq)
    || source.throughSeq >= session.seq || (requireLatest && session.seq - source.throughSeq > MAX_SOURCE_EVENTS)) {
    throw new HandoffError('stale_preflight')
  }
  const start = Math.max(0, source.throughSeq - 32)
  const end = Math.min(session.seq, start + MAX_SOURCE_EVENTS)
  const events = session.snapshotEvents(start, end)
  const cutoff = events.find(event => event.seq === source.throughSeq)
  if (!cutoff || !Number.isSafeInteger(cutoff.time) || Date.parse(source.workspaceObservedAt) !== cutoff.time) {
    throw new HandoffError('stale_preflight')
  }
  const { selected, latest } = selectedReply(events, source.messageId, source.throughSeq)
  if (requireLatest && (selected !== latest || end !== session.seq)) throw new HandoffError('stale_preflight')
}

function lockWriter(root: string): () => void {
  // lockf's descriptor form takes a kernel flock. The parent keeps that open file
  // description; process exit releases it without deleting/reclaiming a PID file.
  if (process.platform !== 'darwin') throw new HandoffError('deployment_unsupported')
  privateDirectory(root)
  const fd = openSync(join(root, 'host.lock'), constants.O_RDWR | constants.O_CREAT
    | constants.O_NOFOLLOW | constants.O_NONBLOCK, 0o600)
  try {
    const info = fstatSync(fd)
    if (!info.isFile() || info.nlink !== 1 || info.uid !== process.getuid?.() || (info.mode & 0o077) !== 0) {
      throw new HandoffError('integration_unavailable')
    }
    const acquired = spawnSync('/usr/bin/lockf', ['-t', '0', '3'], {
      stdio: ['ignore', 'ignore', 'ignore', fd], env: { LANG: 'C' }, timeout: 1000, cwd: '/',
    })
    if (acquired.status !== 0) throw new HandoffError('integration_unavailable')
    return () => closeSync(fd)
  } catch (error) { closeSync(fd); throw error }
}

function connectionKey(root: string): Buffer {
  const path = join(root, 'connection-key')
  const fd = openSync(path, constants.O_RDWR | constants.O_CREAT | constants.O_NOFOLLOW | constants.O_NONBLOCK, 0o600)
  try {
    const info = fstatSync(fd)
    if (!info.isFile() || info.nlink !== 1 || info.uid !== process.getuid?.() || (info.mode & 0o077) !== 0
      || (info.size !== 0 && info.size !== 32)) throw new HandoffError('integration_unavailable')
    if (info.size === 32) return readFileSync(fd)
    const key = randomBytes(32)
    writeFileSync(fd, key)
    fsyncSync(fd)
    return key
  } finally { closeSync(fd) }
}

function object(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new HandoffError('integration_unavailable')
  return value as Record<string, unknown>
}

function identifier(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9._:-]{1,256}$/.test(value)) throw new HandoffError('integration_unavailable')
  return value
}

function sourceConnection(host: Host, runtime: PluginRuntime, key: Buffer) {
  const config = runtime.config
  const connectionRef = keyFor(config.baseUrl)
  return async (context: CallContext, source: SourceBoundary) => {
    const sessions = host.get('sessions') as { get(id: string): Session | undefined } | undefined
    const session = sessions?.get(source.sessionId)
    // The existing DSH PowerContext hooks resolve Scope from the session header.
    // If DSH moved its effective cwd, both current routes must agree before handoff.
    const headerScope = session && session.header.cwd !== source.cwd
      ? runtime.resolveScope(session.header.cwd, context.signal) : undefined
    const [identity, readiness, capabilities, scope, originalScope] = await Promise.all([
      runtime.client.request('get_connection_identity', {}, context.signal),
      runtime.client.request('get_readiness', {}, context.signal, { readinessResponse: true }),
      runtime.client.request('get_capabilities', {}, context.signal),
      runtime.resolveScope(source.cwd, context.signal),
      headerScope,
    ])
    context.signal.throwIfAborted()
    if (headerScope !== undefined && originalScope !== scope) throw new HandoffError('binding_mismatch')
    const connection = object(identity.value)
    const serverIdentity = identifier(connection.server_id)
    const auth = connection.authentication
    const access = connection.access_mode
    const principal = connection.principal === null ? null : object(connection.principal)
    if ((auth !== 'disabled' && auth !== 'authenticated') || (access !== 'disabled' && access !== 'enforced')
      || (auth === 'disabled' && principal !== null)
      || (auth === 'authenticated' && (!principal || !['user', 'service'].includes(String(principal.type))
        || typeof principal.id !== 'string' || !principal.id))
      || (access === 'enforced' && auth !== 'authenticated')
      || object(readiness.value).status !== 'ready') throw new HandoffError('integration_unavailable')
    const sourceTypes = object(capabilities.value).source_types
    if (!Array.isArray(sourceTypes) || !sourceTypes.includes('content')) throw new HandoffError('integration_unavailable')
    // Keep only a keyed, non-secret generation; even a same-principal token rotation changes it.
    const generation = createHmac('sha256', key).update(JSON.stringify([
      connectionRef, config.authorization, config.allowInsecureHttp, config.scopeId, serverIdentity,
      auth, access, principal?.type, principal?.id,
    ])).digest('hex')
    return { serverIdentity, scopeId: identifier(scope), connectionRef, accessMode: access as 'disabled' | 'enforced',
      connectionGeneration: Number.parseInt(generation.slice(0, 13), 16) }
  }
}

/** Optional DSH Host composition; headless and status-only plugin paths stay independent. */
export function registerHandoffBridge(ctx: Context, runtime: PluginRuntime, adapters?: readonly TargetAdapter[]): void {
  if (typeof ctx.inject !== 'function') return
  ctx.inject(['webServer', 'connection'], host => {
    const ui = host as Host
    const facts = localDeploymentFacts(ui, runtime.config.ownerLocal)
    const profile = ui.get('profileContext') as Profile | undefined
    const lifetime = new AbortController()
    let catalog: TargetCatalog | undefined
    const requireLocal = () => {
      if (!resolveDeployment(facts) || !loopbackHost(ui.webServer.host) || ui.connection.allowsRemoteAuthorities === true) {
        throw new HandoffError('deployment_unsupported')
      }
    }
    let services: ReturnType<typeof createHandoffServices> | undefined
    let release: (() => void) | undefined
    let maintenance: Promise<void> | undefined
    let maintenanceTimer: ReturnType<typeof setInterval> | undefined
    const ensureServices = () => {
      lifetime.signal.throwIfAborted()
      requireLocal()
      if (services !== undefined) return services
      if (!profile || !isAbsolute(profile.home)) throw new HandoffError('integration_unavailable')
      const ownerRoot = join(homedir(), '.powercontext-dsh-handoff')
      const unlock = lockWriter(ownerRoot)
      try {
        const root = join(ownerRoot, keyFor(profile.home, profile.name))
        privateDirectory(root)
        const generationKey = connectionKey(root)
        catalog = new TargetCatalog(adapters ?? createDefaultTargetAdapters({
          root, generationKey, sourceServerUrl: runtime.config.baseUrl,
        }))
        const connection = sourceConnection(ui, runtime, generationKey)
        services = createHandoffServices({
          root, catalog, signal: lifetime.signal, client: runtime.client,
          authorization: runtime.config.authorization,
          readBoundary: createBoundaryReader(ui),
          resolveReceiptConnection: async (context, binding) => {
            requireLocal()
            const live = await connection(context, binding.source)
            if (live.serverIdentity !== binding.serverIdentity || live.scopeId !== binding.scopeId
              || live.connectionRef !== binding.connectionRef || live.connectionGeneration !== binding.connectionGeneration) {
              throw new HandoffError('binding_mismatch')
            }
            return { client: runtime.client, accessMode: live.accessMode }
          },
          authority: { requireLocal, sourceConnection: connection,
            verifySource: async (context, source, latest) => verifyHostSource(ui, context, source, latest) },
        })
        release = unlock
        maintenanceTimer = setInterval(() => {
          if (maintenance || lifetime.signal.aborted) return
          // Retention reads cached receipts only, under this Host's existing writer lease.
          maintenance = services!.maintain({ ownerId: `local-owner:${process.getuid?.() ?? 'host'}`,
            signal: lifetime.signal }).catch(() => undefined).finally(() => { maintenance = undefined })
        }, 60 * 60 * 1000)
        maintenanceTimer.unref()
        return services
      } catch (error) { unlock(); throw error }
    }
    const handler = createHandoffHandler({
      facts, requireLocal, signal: lifetime.signal,
      get catalog() { ensureServices(); return catalog! },
      requestRejection: request => ui.connection.requestRejection(request),
      authenticatedOwner: request => {
        const admission = ui.connection.admit?.(request)
        if (ui.connection.admit && admission?.peer?.principal?.kind !== 'owner') return undefined
        return `local-owner:${process.getuid?.() ?? 'host'}`
      },
      resolveBoundary: (context, sessionId, messageId) => resolveHostBoundary(ui, context, sessionId, messageId),
      services: ensureServices,
    })
    for (const path of COLLECTIONS) {
      host.effect(() => ui.webServer.register({
        kind: 'prefix', path, handler,
      }), `powercontext: ${path}`)
    }
    host.effect(() => async () => {
      lifetime.abort()
      clearInterval(maintenanceTimer)
      try { await maintenance; await services?.close() } finally { release?.() }
    }, 'powercontext: handoff bridge disposal')
  })
}
