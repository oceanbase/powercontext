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

import { randomUUID } from 'node:crypto'
import type { IncomingMessage, ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type { PluginRuntime } from './invoke.ts'
import { PLUGIN_NAME, PLUGIN_VERSION } from './errors.ts'
import { officialRelease, serverDistributionAvailable } from './release-updates.ts'
import { packageVersion } from './status-contract.ts'
import { compareVersions, releaseVersion, UPDATE_ACTION_HEADER, UPDATE_PLAN_HEADER, UPDATE_ROUTE, type UpdateSnapshot } from './update-contract.ts'
import { deploymentRejection, localDeploymentFacts, readServerPackageVersion, resolveDeployment, sendJson, type DeploymentFacts, type UiHostContext } from './status-bridge.ts'

interface Bundle {
  name: string
  version?: string
  enabled: boolean
  installed: boolean
  source?: string
  updatable?: boolean
  update?: 'registry' | 'git'
}

function supportsRegistryUpdate(bundle: Bundle): boolean {
  // DSH rc.2 names the update source; the boolean is the rc.1 contract.
  return bundle.update === undefined ? bundle.updatable === true : bundle.update === 'registry'
}

interface Candidate {
  version: string
  bundle: boolean
  deprecated?: string
  incompatible?: object
}

interface UpdateOutcome {
  result: { application: string; version?: string }
  restore?: { application: string; version?: string }
}

type Registry = string | null
export interface NativePluginManager {
  listBundles(): Bundle[] | Promise<Bundle[]>
  registries(): Promise<{ registry: Registry; resolved: Registry; fallbackRegistries: string[] }>
  listVersions(name: string, options?: object, signal?: AbortSignal): Promise<
    { status: 'listed'; installed: string; registry: Registry; versions: Candidate[] }
    | { status: 'refused'; problem: string }
  >
  updateBundle(name: string, version: string, options: { registry: Registry }): Promise<UpdateOutcome>
}

const PUBLIC_REGISTRIES = new Set(['https://registry.npmjs.org/', 'https://registry.npmmirror.com/'])
export const UPDATE_CHECK_INTERVAL_MS = 60 * 60_000
export const UPDATE_STARTUP_DELAY_MS = 15_000
const UPDATE_BUSY_RETRY_MS = 60_000
// Keep an hourly offer valid until the next check can replace it.
const PLAN_TTL_MS = UPDATE_CHECK_INTERVAL_MS + 5 * 60_000
const CHECK_COOLDOWN_MS = 5_000

function publicRegistry(registry: Registry): registry is string {
  if (registry === null) return false
  try {
    return PUBLIC_REGISTRIES.has(new URL(registry).href)
  } catch {
    return false
  }
}

interface Plan {
  id: string
  expires: number
  from: string
  source: string
  to: string
  server: string
  registry: string
  channel: Options['channel']
}

export function updateLedger() {
  return {
    snapshot: {
      phase: 'idle', plugin_version: null, plugin_target: null, server_target: null,
      server_state: 'unknown', plan: null, release_tag: null,
    } as UpdateSnapshot,
    plan: null as Plan | null,
    checkedAt: Number.NEGATIVE_INFINITY,
  }
}

type Ledger = ReturnType<typeof updateLedger>
// DSH may replace the Host plugin during a native update. Keep only one operation
// per Host root across module instances, with no state or authority in the browser.
const key = Symbol.for('powercontext.dsh-update-ledgers.v1')
const global = globalThis as typeof globalThis & { [key]?: WeakMap<Context, Ledger> }
const ledgers = global[key] ??= new WeakMap()

interface Options {
  ledger: Ledger
  pluginVersion: string
  channel: 'stable' | 'preview'
  held: boolean
  manager: () => NativePluginManager | undefined
  serverVersion: () => Promise<string | null>
  canInstall: () => boolean
  fetch?: typeof fetch
  now?: () => number
}

export class DshUpdates {
  private readonly options: Options

  constructor(options: Options) {
    this.options = options
  }

  private get ledger() { return this.options.ledger }
  private now() { return this.options.now?.() ?? Date.now() }

  read(): UpdateSnapshot {
    const { snapshot, plan } = this.ledger
    const plugin_version = packageVersion(this.options.pluginVersion)
    if (plan && (plan.expires <= this.now() || plan.from !== plugin_version
      || plan.channel !== this.options.channel || this.options.held)) {
      this.ledger.plan = null
      this.ledger.snapshot = { ...snapshot, phase: 'idle', plan: null }
    }
    if (snapshot.phase === 'restart_required' && snapshot.plugin_target === plugin_version) {
      this.ledger.snapshot = { ...snapshot, phase: 'updated' }
    }
    return { ...this.ledger.snapshot, plugin_version }
  }

  check(): UpdateSnapshot {
    const current = this.read()
    if (['checking', 'installing', 'restart_required'].includes(current.phase)
      || this.now() - this.ledger.checkedAt < CHECK_COOLDOWN_MS) return current
    this.ledger.checkedAt = this.now()
    this.ledger.plan = null
    this.ledger.snapshot = { ...current, phase: 'checking', plan: null }
    void this.resolveCheck().catch(() => this.phase('check_failed'))
    return this.read()
  }

  /** Schedule the same read-only check once per hour, independent of the panel. */
  scheduleChecks(): () => void {
    let timer: ReturnType<typeof setTimeout>
    const remaining = () => this.ledger.checkedAt + UPDATE_CHECK_INTERVAL_MS - this.now()
    const schedule = (delay: number) => {
      timer = setTimeout(tick, delay)
      timer.unref()
    }
    const tick = () => {
      if (remaining() <= 0) this.check()
      const delay = remaining()
      schedule(delay > 0 ? delay : UPDATE_BUSY_RETRY_MS)
    }
    schedule(Math.max(UPDATE_STARTUP_DELAY_MS, remaining()))
    return () => clearTimeout(timer)
  }

  install(id: string): UpdateSnapshot | null {
    if (this.read().phase !== 'available' || this.ledger.plan?.id !== id) return null
    const plan = this.ledger.plan
    this.ledger.plan = null
    this.phase('installing')
    void this.apply(plan).catch(() => this.phase('unconfirmed'))
    return this.read()
  }

  private phase(phase: UpdateSnapshot['phase']) {
    this.ledger.snapshot = { ...this.ledger.snapshot, phase, plan: null }
  }

  private async resolveCheck(): Promise<void> {
    const { fetch: fetcher, channel } = this.options
    const [release, server] = await Promise.all([officialRelease(channel, fetcher), this.options.serverVersion()])
    const installedServer = releaseVersion(server)
    const serverHasUpdate = server === null || (installedServer !== null && compareVersions(release.version, installedServer) > 0)
    const serverAvailable = serverHasUpdate && await serverDistributionAvailable(release, fetcher)
    const server_state = serverHasUpdate
      ? serverAvailable ? 'available' : 'artifact_unavailable'
      : installedServer ? 'current' : 'unknown'
    this.ledger.snapshot = {
      ...this.ledger.snapshot, server_state, server_target: serverHasUpdate ? release.version.python : null,
      release_tag: release.tag, plugin_target: release.version.npm,
    }

    const running = releaseVersion(this.options.pluginVersion)
    const manager = this.options.manager()
    const bundle = (await manager?.listBundles())?.find(bundle => bundle.name === PLUGIN_NAME)
    if (!running || !manager || !bundle?.installed || !bundle.enabled || !supportsRegistryUpdate(bundle)
      || !bundle.source || bundle.version !== running.npm) return this.phase('manual')
    if (compareVersions(release.version, running) <= 0) return this.phase('current')
    if (this.options.held) return this.phase('held')
    if (!release.pluginPublished) return this.phase('artifact_unavailable')

    const registries = await manager.registries()
    if (!publicRegistry(registries.registry ?? registries.resolved)
      || registries.fallbackRegistries.some(registry => !publicRegistry(registry))) return this.phase('manual')
    const versions = await manager.listVersions(PLUGIN_NAME, undefined, AbortSignal.timeout(12_000))
    if (versions.status !== 'listed') return this.phase(versions.problem === 'not-found' ? 'artifact_unavailable' : 'check_failed')
    const registry = versions.registry ?? registries.resolved
    if (!publicRegistry(registry) || versions.installed !== running.npm) return this.phase('manual')
    const target = versions.versions.find(version => version.version === release.version.npm)
    if (!target) return this.phase('artifact_unavailable')
    if (!target.bundle || target.deprecated !== undefined || target.incompatible) return this.phase('incompatible')
    if (installedServer?.python !== release.version.python) return this.phase('server_first')

    const plan = {
      id: randomUUID(), expires: this.now() + PLAN_TTL_MS,
      from: running.npm, source: bundle.source, to: target.version,
      server: release.version.python, registry, channel,
    }
    this.ledger.plan = plan
    this.ledger.snapshot = { ...this.ledger.snapshot, phase: 'available', plan: plan.id }
  }

  private async apply(plan: Plan): Promise<void> {
    const manager = this.options.manager()
    if (!manager || this.options.held) return this.phase('manual')
    const [bundles, server, registries] = await Promise.all([
      manager.listBundles(), this.options.serverVersion(), manager.registries(),
    ])
    const current = bundles.find(bundle => bundle.name === PLUGIN_NAME)
    if (current?.version !== plan.from || current.source !== plan.source
      || !current.enabled || !current.installed || !supportsRegistryUpdate(current)
      || !publicRegistry(registries.registry ?? registries.resolved)
      || !publicRegistry(plan.registry)
      || registries.fallbackRegistries.some(registry => !publicRegistry(registry))) return this.phase('manual')
    if (releaseVersion(server)?.python !== plan.server) return this.phase('server_first')
    if (!this.options.canInstall()) return this.phase('busy')

    const outcome = await manager.updateBundle(PLUGIN_NAME, plan.to, { registry: plan.registry })
    const { result, restore } = outcome
    if (result.application === 'applied' || result.application === 'restart-required') {
      const installed = (await manager.listBundles()).find(bundle => bundle.name === PLUGIN_NAME)
      if (installed?.version !== plan.to || !installed.enabled || !installed.installed) return this.phase('unconfirmed')
      // A new Host entry must report the selected version before we claim success.
      return this.phase('restart_required')
    }
    if (restore?.application === 'applied' || restore?.application === 'restart-required') {
      const installed = (await manager.listBundles()).find(bundle => bundle.name === PLUGIN_NAME)
      if (installed?.version === plan.from && installed.enabled && installed.installed) return this.phase('restored')
    }
    return this.phase('install_failed')
  }
}

export function createUpdateRouteHandler(
  updates: DshUpdates, connection: UiHostContext['connection'], facts: DeploymentFacts,
): (request: IncomingMessage, response: ServerResponse) => void {
  return (request, response) => {
    const rejection = connection.requestRejection(request)
    const deployment = deploymentRejection(request, facts)
    if (rejection !== undefined || deployment) return sendJson(response, rejection ?? 403, { code: 'owner_required' })
    if (request.method === 'GET') return sendJson(response, 200, updates.read())
    if (request.method !== 'POST') {
      response.setHeader('allow', 'GET, POST')
      return sendJson(response, 405, { code: 'method_not_allowed' })
    }
    // A cross-origin form cannot set the action header. DSH also applies its
    // own Host/Origin checks above; installation additionally requires its owner principal.
    const action = request.headers[UPDATE_ACTION_HEADER]
    if (action === 'check') return sendJson(response, 202, updates.check())
    if (action !== 'install') return sendJson(response, 400, { code: 'unknown_action' })
    if (connection.admit?.(request).peer?.principal?.kind !== 'owner') {
      return sendJson(response, 403, { code: 'owner_required' })
    }
    const id = request.headers[UPDATE_PLAN_HEADER]
    const snapshot = typeof id === 'string' && updates.install(id)
    if (!snapshot) return sendJson(response, 409, { code: 'update_plan_changed' })
    return sendJson(response, 202, snapshot)
  }
}

function hostUpdates(ctx: UiHostContext, runtime: PluginRuntime, facts: DeploymentFacts): DshUpdates {
  let ledger = ledgers.get(ctx.root)
  if (!ledger) ledgers.set(ctx.root, ledger = updateLedger())
  return new DshUpdates({
    ledger, pluginVersion: PLUGIN_VERSION,
    channel: runtime.config.updateChannel ?? 'stable', held: runtime.config.updateHold === true,
    manager: () => {
      if (typeof ctx.connection.admit !== 'function') return undefined
      const profile = ctx.get('profileContext') as { name?: string } | undefined
      if (profile?.name !== (facts.desktop ? 'desktop' : 'web')) return undefined
      const manager = (ctx.root.get('pluginManager') ?? ctx.get('pluginManager')) as NativePluginManager | undefined
      return manager && typeof manager.listVersions === 'function' && typeof manager.updateBundle === 'function'
        ? manager : undefined
    },
    serverVersion: async () => {
      try {
        return await readServerPackageVersion(runtime.client)
      } catch {
        return null
      }
    },
    canInstall: () => {
      const sessions = ctx.get('sessions') as { list?: () => unknown[] } | undefined
      const agents = ctx.get('agents') as { turnBoundaryOf?: (session: unknown) => { openTurnStartSeq: unknown } } | undefined
      return typeof sessions?.list === 'function' && typeof agents?.turnBoundaryOf === 'function'
        && sessions.list().every(session => agents.turnBoundaryOf!(session).openTurnStartSeq === null)
    },
  })
}

export function registerUpdateBridge(ctx: Context, runtime: PluginRuntime): void {
  if (typeof ctx.inject !== 'function') return
  ctx.inject(['webServer', 'connection'], host => {
    const ui = host as UiHostContext
    const facts = localDeploymentFacts(ui, runtime.config.ownerLocal)
    const updates = hostUpdates(ui, runtime, facts)
    host.effect(() => ui.webServer.register({
      kind: 'exact',
      path: UPDATE_ROUTE,
      handler: createUpdateRouteHandler(updates, ui.connection, facts),
    }), 'powercontext: update route')
    const profile = ui.get('profileContext') as { name?: string } | undefined
    if (resolveDeployment(facts) && profile?.name === (facts.desktop ? 'desktop' : 'web')) {
      host.effect(() => updates.scheduleChecks(), 'powercontext: hourly update check')
    }
  })
}
