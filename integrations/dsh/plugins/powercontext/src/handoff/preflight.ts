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


import { accessSync, constants, realpathSync, statSync } from 'node:fs'
import { isAbsolute } from 'node:path'
import type { CallContext, PreflightGate, SourceBoundary, VerifiedPreflight } from './contracts.ts'
import { HandoffError, handoffError, type HandoffErrorCode } from './errors.ts'
import { HandoffRecords, keyFor } from './records.ts'
import type { ConnectionBinding, InstallationResult, PresentTarget, TargetAdapter, TargetCatalog } from './targets.ts'

const SCHEMA = 'powercontext.dsh-preflight.v1'
const ACTIVE = new Set(['checking_agent', 'checking_integration', 'installing', 'verifying'])
const CHECK_TIMEOUT_MS = 20_000
const INSTALL_TIMEOUT_MS = 120_000

export type PreflightState = 'checking_agent' | 'checking_integration' | 'needs_install_consent' |
  'installing' | 'verifying' | 'needs_user_action' | 'ready' | 'failed' | 'cancelled' | 'unknown'

type NextAction = 'install_agent' | 'install_integration' | 'repair_integration' | 'restart_target' |
  'sign_in' | 'configure_target' | 'reselect_source' | 'run_preflight_again' | 'check_target'
type Detection = { configurationKey: string; targetVersion: string; adapterVersion: string }

export interface PreflightView {
  preflightId: string
  revision: number
  source: SourceBoundary
  targetId: string
  state: PreflightState
  agent?: 'present' | 'missing' | 'unsupported' | 'unknown'
  installation?: InstallationResult['state']
  integration?: 'ready' | 'missing' | 'disabled' | 'broken' | 'needs_user_action' | 'unknown'
  nextAction?: NextAction
  code?: HandoffErrorCode
  updatedAt: string
  binding?: VerifiedPreflight
}

export interface PreflightRecord extends PreflightView {
  schema: typeof SCHEMA
  ownerId: string
  directoryIdentity?: string
  detection?: Detection
  targetConfigurationGeneration?: string
  decision?: InstallDecision
  confirmedOriginal?: boolean
}

export interface InstallDecision {
  readonly expectedRevision: number
  readonly action: 'install' | 'decline' | 'verify'
  readonly confirmOriginal?: boolean
}

export interface PreflightAuthority {
  requireLocal(): void
  /** Authorize the source session and verify the exact completed response and observed cwd. */
  verifySource(context: CallContext, source: SourceBoundary, requireLatest: boolean): Promise<void>
  /** Read live identity, configuration generation and the effective DSH Scope without binding. */
  sourceConnection(context: CallContext, source: SourceBoundary): Promise<ConnectionBinding>
}

interface Options {
  catalog: TargetCatalog
  authority: PreflightAuthority
  records: HandoffRecords<PreflightRecord>
  signal: AbortSignal
}

interface ActiveCheck {
  controller: AbortController
  done: Promise<PreflightView>
}

export function preflightRecords(root: string) {
  return new HandoffRecords<PreflightRecord>(root, SCHEMA)
}

export function sameSource(a: SourceBoundary, b: SourceBoundary): boolean {
  return a.sessionId === b.sessionId && a.messageId === b.messageId && a.throughSeq === b.throughSeq
    && a.cwd === b.cwd && a.workspaceObservedAt === b.workspaceObservedAt
}

function sameBinding(a: VerifiedPreflight, b: VerifiedPreflight): boolean {
  return a.preflightId === b.preflightId && a.revision === b.revision && sameSource(a.source, b.source)
    && a.targetId === b.targetId && a.targetVersion === b.targetVersion && a.adapterVersion === b.adapterVersion
    && a.effectiveCwd === b.effectiveCwd && a.serverIdentity === b.serverIdentity && a.connectionRef === b.connectionRef
    && a.connectionGeneration === b.connectionGeneration && a.scopeId === b.scopeId
}

function directory(cwd: string): { path: string; identity: string } {
  try {
    if (!isAbsolute(cwd)) throw new Error()
    const path = realpathSync(cwd)
    const info = statSync(path, { bigint: true })
    if (!info.isDirectory()) throw new Error()
    accessSync(path, constants.R_OK | constants.X_OK)
    return { path, identity: `${info.dev}:${info.ino}:${info.birthtimeNs}` }
  } catch {
    throw new HandoffError('binding_mismatch')
  }
}

function view(record: PreflightRecord): PreflightView {
  const { preflightId, revision, source, targetId, state, agent, installation, integration, nextAction, code, updatedAt, binding } = record
  return { preflightId, revision, source, targetId, state, agent, installation, integration, nextAction, code, updatedAt, binding }
}

export class PreflightService implements PreflightGate {
  private readonly options: Options
  private readonly checks = new Map<string, ActiveCheck>()
  private readonly installs = new Map<string, Promise<InstallationResult>>()

  constructor(options: Options) { this.options = options }

  /** Wait for Host-owned work before releasing the storage lease on unload. */
  async drain(): Promise<void> {
    await Promise.allSettled([...Array.from(this.checks.values(), check => check.done), ...this.installs.values()])
  }

  async create(context: CallContext, input: { requestId: string; source: SourceBoundary; targetId: string }): Promise<PreflightView> {
    this.options.authority.requireLocal()
    const id = keyFor(context.ownerId, input.requestId)
    const previous = this.options.records.read(id)
    if (previous) {
      if (previous.targetId !== input.targetId || !sameSource(previous.source, input.source)) {
        throw new HandoffError('idempotency_conflict')
      }
      return this.checks.get(id)?.done ?? this.get(context, id, input.source.sessionId)
    }
    const adapter = this.options.catalog.require(input.targetId)
    await this.options.authority.verifySource(context, input.source, true)
    context.signal.throwIfAborted()
    // An identical request may have passed authorization while this one was waiting.
    if (this.options.records.read(id)) return this.create(context, input)
    const record: PreflightRecord = {
      schema: SCHEMA, ownerId: context.ownerId, preflightId: id, source: input.source, targetId: adapter.id,
      revision: 1, state: 'checking_agent', updatedAt: new Date().toISOString(),
    }
    this.options.records.save(id, record, true)
    return this.run(context, record, (call, set) => this.inspect(call, record, adapter, set))
  }

  get(context: CallContext, id: string, sessionId: string): PreflightView {
    const record = this.read(context, id, sessionId)
    return view(this.recovered(record))
  }

  cancel(context: CallContext, id: string, sessionId: string): PreflightView {
    const record = this.read(context, id, sessionId)
    if (record.state === 'cancelled') return view(record)
    // This only cancels continuation. The independently owned install may already have made changes.
    this.checks.get(id)?.controller.abort()
    return view(this.update(record, { state: 'cancelled', code: 'cancelled', nextAction: undefined,
      ...(record.state === 'installing' ? { installation: 'unknown', integration: 'unknown' } : {}) }))
  }

  async decide(context: CallContext, id: string, sessionId: string, input: InstallDecision): Promise<PreflightView> {
    this.options.authority.requireLocal()
    const record = this.read(context, id, sessionId)
    if (record.decision?.expectedRevision === input.expectedRevision) {
      if (record.decision.action !== input.action || Boolean(record.decision.confirmOriginal) !== Boolean(input.confirmOriginal)) {
        throw new HandoffError('idempotency_conflict')
      }
      return this.checks.get(id)?.done ?? view(this.recovered(record))
    }
    if (record.revision !== input.expectedRevision) throw new HandoffError('stale_preflight')
    if (this.checks.has(id)) throw new HandoffError('not_ready')
    if (input.action === 'decline' && record.state === 'needs_install_consent') {
      return view(this.update(record, { state: 'cancelled', code: 'cancelled', nextAction: undefined, decision: input }))
    }
    if ((input.action === 'install' && record.state !== 'needs_install_consent')
      || (input.action === 'verify' && record.state !== 'needs_user_action')
      || input.action === 'decline'
      || (input.confirmOriginal && (input.action !== 'verify' || record.nextAction !== 'reselect_source'))) {
      throw new HandoffError('not_ready')
    }
    context.signal.throwIfAborted()
    const adapter = this.options.catalog.require(record.targetId)
    const installing = input.action === 'install'
    const updated = this.update(record, { state: installing ? 'installing' : 'verifying', decision: input,
      confirmedOriginal: record.confirmedOriginal || input.confirmOriginal, nextAction: undefined, code: undefined })
    return this.run(context, updated, async (call, set) => {
      if (installing) {
        await this.options.authority.verifySource(call, record.source, true)
        const result = await this.waitForInstall(call, adapter, record)
        if (call.signal.aborted) {
          const current = this.read(context, id, sessionId)
          const cancelled = current.state === 'cancelled' || (!this.options.signal.aborted && context.signal.aborted)
          return view(this.update(current, { state: cancelled ? 'cancelled' : 'unknown',
            installation: result.state, integration: 'unknown', code: cancelled ? 'cancelled' : 'outcome_unknown',
            nextAction: cancelled ? undefined : 'check_target' }))
        }
        if (result.state === 'needs_user_action') {
          return set({ state: 'needs_user_action', installation: result.state, nextAction: result.action })
        }
        if (result.state === 'failed') return set({ state: 'failed', installation: result.state, code: 'not_ready' })
        if (result.state === 'unknown') return set({ state: 'unknown', installation: result.state,
          code: 'outcome_unknown', nextAction: 'check_target' })
        set({ state: 'verifying', installation: 'completed' })
      }
      return this.inspect(call, updated, adapter, set)
    }, installing ? INSTALL_TIMEOUT_MS + CHECK_TIMEOUT_MS : CHECK_TIMEOUT_MS)
  }

  async validateForPrepare(context: CallContext, input: Parameters<PreflightGate['validateForPrepare']>[1]): Promise<VerifiedPreflight> {
    this.options.authority.requireLocal()
    const record = this.read(context, input.preflightId, input.source.sessionId)
    if (record.revision !== input.expectedRevision || !sameSource(record.source, input.source)) {
      throw new HandoffError('stale_preflight')
    }
    if (record.state !== 'ready' || !record.binding) throw new HandoffError('not_ready')
    await this.revalidate(context, record, record.binding, !record.confirmedOriginal)
    // A cancellation or new revision while live probes ran invalidates the gate.
    if (this.read(context, input.preflightId, input.source.sessionId).revision !== record.revision) {
      throw new HandoffError('stale_preflight')
    }
    return record.binding
  }

  async validateForLaunch(context: CallContext, binding: VerifiedPreflight) {
    this.options.authority.requireLocal()
    const record = this.read(context, binding.preflightId, binding.source.sessionId)
    if (!record.binding || !sameBinding(record.binding, binding)) throw new HandoffError('binding_mismatch')
    // A later response does not change a delivery's saved source boundary.
    return this.revalidate(context, record, binding)
  }

  private async revalidate(context: CallContext, record: PreflightRecord, binding: VerifiedPreflight, latest?: boolean) {
    const call = { ...context, signal: AbortSignal.any([context.signal, this.options.signal, AbortSignal.timeout(CHECK_TIMEOUT_MS)]) }
    if (latest !== undefined) await this.options.authority.verifySource(call, binding.source, latest)
    const adapter = this.options.catalog.require(binding.targetId)
    const target = await adapter.detect(call.signal)
    if (target.state !== 'present' || !record.detection || !this.matches(adapter, target, record.detection)
      || (await adapter.checkIntegration(target, call.signal)).state !== 'ready') {
      throw new HandoffError('binding_mismatch')
    }
    const live = await this.resolve(call, adapter, target, binding.source)
    if (latest !== undefined) await this.options.authority.verifySource(call, binding.source, latest)
    call.signal.throwIfAborted()
    if (live.directoryIdentity !== record.directoryIdentity
      || live.targetConfigurationGeneration !== record.targetConfigurationGeneration || !sameBinding(binding, {
      ...binding, ...live.binding, targetVersion: target.version, adapterVersion: adapter.version,
    })) throw new HandoffError('binding_mismatch')
    return { adapter, target, assertDirectory: () => {
      const current = directory(binding.source.cwd)
      if (current.path !== binding.effectiveCwd || current.identity !== record.directoryIdentity) {
        throw new HandoffError('binding_mismatch')
      }
    } }
  }

  private async inspect(context: CallContext, record: PreflightRecord, adapter: TargetAdapter, set: Update) {
    const target = await adapter.detect(context.signal)
    if (target.state === 'missing') return set({ state: 'needs_user_action', agent: 'missing', nextAction: 'install_agent' })
    if (target.state === 'unsupported') return set({ state: 'failed', agent: 'unsupported', code: 'integration_unavailable' })
    if (target.state !== 'present') return set({ state: 'unknown', agent: 'unknown', code: 'outcome_unknown', nextAction: 'check_target' })
    const detection = { configurationKey: target.configurationKey, targetVersion: target.version, adapterVersion: adapter.version }
    set({ state: record.state === 'checking_agent' ? 'checking_integration' : 'verifying', agent: 'present', detection })
    const integration = await adapter.checkIntegration(target, context.signal)
    if (integration.state === 'needs_user_action') {
      return set({ state: 'needs_user_action', integration: integration.state, nextAction: integration.action })
    }
    if (integration.state === 'unknown') return set({ state: 'unknown', integration: 'unknown',
      code: 'outcome_unknown', nextAction: 'check_target' })
    if (integration.state !== 'ready') {
      if (integration.repairable && record.decision?.action !== 'install') return set({
        state: 'needs_install_consent', integration: integration.state,
        nextAction: integration.state === 'missing' ? 'install_integration' : 'repair_integration',
      })
      return set({ state: 'needs_user_action', integration: integration.state, nextAction: 'configure_target' })
    }
    const live = await this.resolve(context, adapter, target, record.source)
    await this.options.authority.verifySource(context, record.source, !record.confirmedOriginal)
    return set({ state: 'ready', integration: 'ready', directoryIdentity: live.directoryIdentity,
      targetConfigurationGeneration: live.targetConfigurationGeneration,
      binding: { preflightId: record.preflightId, revision: 0, source: record.source, targetId: adapter.id,
        targetVersion: target.version, adapterVersion: adapter.version, ...live.binding },
    })
  }

  private async resolve(context: CallContext, adapter: TargetAdapter, target: PresentTarget, source: SourceBoundary) {
    const before = directory(source.cwd)
    const actual = await adapter.resolveEnvironment(target, before.path, context.signal)
    const connection = await this.options.authority.sourceConnection(context, source)
    const effective = directory(actual.effectiveCwd)
    const current = directory(source.cwd)
    if (before.path !== current.path || before.identity !== current.identity
      || effective.path !== current.path || effective.identity !== current.identity
      || !connection.serverIdentity || !connection.scopeId || !connection.connectionRef || !Number.isSafeInteger(connection.connectionGeneration)
      || actual.serverIdentity !== connection.serverIdentity || actual.scopeId !== connection.scopeId) {
      throw new HandoffError('binding_mismatch')
    }
    if (!actual.newSession || !actual.unsentPrefill || !actual.packageAccess || !actual.configurationGeneration) {
      throw new HandoffError('integration_unavailable')
    }
    const { serverIdentity, connectionRef, connectionGeneration, scopeId } = connection
    return { binding: { serverIdentity, connectionRef, connectionGeneration, scopeId, effectiveCwd: effective.path },
      directoryIdentity: effective.identity, targetConfigurationGeneration: actual.configurationGeneration }
  }

  private matches(adapter: TargetAdapter, target: PresentTarget, prior: Detection): boolean {
    return adapter.version === prior.adapterVersion && target.version === prior.targetVersion
      && target.configurationKey === prior.configurationKey
  }

  private async waitForInstall(context: CallContext, adapter: TargetAdapter, record: PreflightRecord) {
    // A shared installer can finish while this caller is rechecking the target identity.
    const existing = record.detection && this.installs.get(record.detection.configurationKey)
    const target = await adapter.detect(context.signal)
    if (target.state !== 'present' || !record.detection || !this.matches(adapter, target, record.detection)) {
      throw new HandoffError('stale_preflight')
    }
    context.signal.throwIfAborted()
    const key = target.configurationKey
    const shared = existing ?? this.installs.get(key)
    if (shared) return shared
    // Probe integration inside the shared operation: ready can be visible before an installer settles.
    const pending = this.install(adapter, target, record)
    this.installs.set(key, pending)
    const clear = () => { if (this.installs.get(key) === pending) this.installs.delete(key) }
    void pending.then(clear, clear)
    return pending
  }

  private async install(adapter: TargetAdapter, target: PresentTarget, record: PreflightRecord): Promise<InstallationResult> {
    // Shared side effect: a cancelled waiter stops continuation without claiming rollback.
    const signal = AbortSignal.any([this.options.signal, AbortSignal.timeout(INSTALL_TIMEOUT_MS)])
    let started = false
    try {
      const check = await adapter.checkIntegration(target, signal)
      signal.throwIfAborted()
      if (check.state === 'ready') return { state: 'completed' }
      if (check.state === 'needs_user_action') return check
      if (check.state === 'unknown') return { state: 'unknown' }
      if (check.state !== record.integration || !check.repairable) throw new HandoffError('stale_preflight')
      started = true
      const result = await adapter.installIntegration(target, signal)
      return signal.aborted ? { state: 'unknown' } : result
    } catch (error) {
      if (!started && !signal.aborted) throw error
      return { state: 'unknown' }
    }
  }

  private read(context: CallContext, id: string, sessionId: string): PreflightRecord {
    const record = this.options.records.read(id)
    if (!record || record.ownerId !== context.ownerId || record.source.sessionId !== sessionId) {
      throw new HandoffError('not_found')
    }
    return record
  }

  private recovered(record: PreflightRecord): PreflightRecord {
    return ACTIVE.has(record.state) && !this.checks.has(record.preflightId)
      ? { ...record, state: 'unknown', code: 'outcome_unknown', nextAction: 'check_target' }
      : record
  }

  private update(record: PreflightRecord, patch: Partial<PreflightRecord>): PreflightRecord {
    const revision = record.revision + 1
    const next = { ...record, ...patch, revision, updatedAt: new Date().toISOString(),
      ...(patch.binding ? { binding: { ...patch.binding, revision } } : {}) }
    this.options.records.save(record.preflightId, next)
    return next
  }

  private run(context: CallContext, record: PreflightRecord,
    work: (call: CallContext, set: Update) => Promise<PreflightView>, timeoutMs = CHECK_TIMEOUT_MS): Promise<PreflightView> {
    const id = record.preflightId
    const controller = new AbortController()
    const deadline = AbortSignal.timeout(timeoutMs)
    const signal = AbortSignal.any([context.signal, controller.signal, this.options.signal, deadline])
    let current = record
    const done = Promise.resolve().then(() => work({ ...context, signal }, patch => {
      signal.throwIfAborted()
      current = this.update(current, patch)
      return view(current)
    })).catch(error => {
      if (controller.signal.aborted) return this.get(context, id, record.source.sessionId)
      const code = handoffError(error).code
      if (deadline.aborted || (signal.aborted && current.state === 'installing')) {
        return view(this.update(current, { state: 'unknown', code: 'outcome_unknown', nextAction: 'check_target' }))
      }
      if (signal.aborted) return view(this.update(current, { state: 'cancelled', code: 'cancelled' }))
      if (code === 'stale_preflight') return view(this.update(current, {
        state: 'needs_user_action', code, nextAction: 'reselect_source',
      }))
      return view(this.update(current, { state: 'failed', code, nextAction: 'run_preflight_again' }))
    }).finally(() => { this.checks.delete(id) })
    this.checks.set(id, { controller, done })
    return done
  }
}

type Update = (patch: Partial<PreflightRecord>) => PreflightView
