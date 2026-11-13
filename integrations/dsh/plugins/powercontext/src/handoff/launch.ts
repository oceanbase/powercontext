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
import { constants, accessSync, lstatSync } from 'node:fs'
import { isAbsolute } from 'node:path'
import { isDeepStrictEqual } from 'node:util'
import type { CallContext, DeliveryService, LaunchBundle } from './contracts.ts'
import { HandoffError } from './errors.ts'
import type { LaunchPlanner } from './native-launch.ts'
import { sameSource, type PreflightService } from './preflight.ts'
import { HandoffRecords, keyFor } from './records.ts'
import { NATIVE_TARGET_IDS } from './targets.ts'

const SCHEMA = 'powercontext.dsh-launch-attempts.v1'
const MAX_ATTEMPTS = 64
const CHECK_TIMEOUT_MS = 30_000

export type LaunchState = 'requested' | 'opened' | 'failed' | 'unknown' | 'cancelled'

export interface LaunchInput {
  readonly sessionId: string
  readonly requestId: string
  readonly targetId: string
  /** Explicit confirmation of a repeat after inspecting this exact attempt. */
  readonly retryOf?: string
}

export interface LaunchView {
  readonly launchAttemptId: string
  readonly state: LaunchState
  readonly createdAt: string
  readonly updatedAt: string
  readonly code?: 'outcome_unknown' | 'cancelled' | 'not_ready'
}

interface Attempt extends LaunchView {
  readonly requestId: string
  readonly retryOf?: string
}

interface LaunchRecord {
  schema: typeof SCHEMA
  ownerId: string
  sessionId: string
  deliveryId: string
  targetId: string
  packageSha256: string
  receiptAttemptId: string
  attempts: Attempt[]
}

interface Pending {
  input: LaunchInput
  dispatched: boolean
  controller: AbortController
  done: Promise<LaunchView>
}

interface Options {
  delivery: DeliveryService
  preflight: Pick<PreflightService, 'validateForLaunch'>
  records: HandoffRecords<LaunchRecord>
  plan: LaunchPlanner
  requireLocal(): void
  signal: AbortSignal
}

export function launchRecords(root: string) {
  return new HandoffRecords<LaunchRecord>(root, SCHEMA)
}

function result(attempt: LaunchView, state: LaunchState): LaunchView {
  return { ...attempt, state, updatedAt: new Date().toISOString(),
    ...(state === 'unknown' ? { code: 'outcome_unknown' }
      : state === 'cancelled' ? { code: 'cancelled' } : state === 'failed' ? { code: 'not_ready' } : {}) }
}

function publicAttempt({ launchAttemptId, state, createdAt, updatedAt, code }: LaunchView): LaunchView {
  return { launchAttemptId, state, createdAt, updatedAt, code }
}

function packageExists(bundle: LaunchBundle): boolean {
  try {
    if (!isAbsolute(bundle.packagePath) || !/^[0-9a-f]{64}$/.test(bundle.packageSha256)) return false
    const info = lstatSync(bundle.packagePath)
    if (!info.isFile() || info.size === 0) return false
    accessSync(bundle.packagePath, constants.R_OK)
    return true
  } catch {
    return false
  }
}

export class LaunchService {
  private readonly options: Options
  private readonly pending = new Map<string, Pending>()

  constructor(options: Options) { this.options = options }

  async drain(): Promise<void> {
    await Promise.allSettled(Array.from(this.pending.values(), item => item.done))
  }

  list(context: CallContext, deliveryId: string, sessionId: string): LaunchView[] {
    const key = keyFor(context.ownerId, deliveryId)
    const record = this.read(context, key, sessionId)
    return record?.attempts.map(attempt => this.recover(key, attempt)) ?? []
  }

  async cancelPending(context: CallContext, deliveryId: string, sessionId: string): Promise<LaunchView[]> {
    const key = keyFor(context.ownerId, deliveryId)
    const active = this.pending.get(key)
    if (active?.input.sessionId === sessionId && !active.dispatched) {
      active.controller.abort()
      await active.done.catch(() => undefined)
    }
    return this.list(context, deliveryId, sessionId)
  }

  async launch(context: CallContext, deliveryId: string, input: LaunchInput): Promise<LaunchView> {
    this.options.requireLocal()
    if (!(NATIVE_TARGET_IDS as readonly string[]).includes(input.targetId)) throw new HandoffError('payload_invalid')
    const key = keyFor(context.ownerId, deliveryId)
    const record = this.read(context, key, input.sessionId)
    if (record && record.targetId !== input.targetId) throw new HandoffError('binding_mismatch')
    const existing = record?.attempts.find(attempt => attempt.requestId === input.requestId)
    if (existing) {
      if (existing.retryOf !== input.retryOf) throw new HandoffError('idempotency_conflict')
      return this.recover(key, existing)
    }
    const active = this.pending.get(key)
    if (active) {
      if (active.input.sessionId !== input.sessionId) throw new HandoffError('not_found')
      if (active.input.requestId !== input.requestId || active.input.targetId !== input.targetId
        || active.input.retryOf !== input.retryOf) throw new HandoffError('idempotency_conflict')
      return active.done
    }
    const last = record?.attempts.at(-1)
    if (last?.launchAttemptId !== input.retryOf) {
      throw new HandoffError(last?.state === 'unknown' || last?.state === 'requested' ? 'outcome_unknown' : 'idempotency_conflict')
    }
    if (record && record.attempts.length >= MAX_ATTEMPTS) throw new HandoffError('capacity_exceeded')
    context.signal.throwIfAborted()
    const controller = new AbortController()
    const pending: Pending = {
      controller, input, dispatched: false,
      done: Promise.resolve().then(() => this.run(context, key, deliveryId, record, pending))
        .finally(() => { this.pending.delete(key) }),
    }
    this.pending.set(key, pending)
    return pending.done
  }

  private async run(context: CallContext, key: string, deliveryId: string, previous: LaunchRecord | undefined, pending: Pending) {
    const { input } = pending
    const deadline = AbortSignal.timeout(CHECK_TIMEOUT_MS)
    const signal = AbortSignal.any([context.signal, pending.controller.signal, this.options.signal, deadline])
    const call = { ...context, signal }
    signal.throwIfAborted()
    const bundle = await this.options.delivery.getForLaunch(call, deliveryId)
    if (bundle.source.sessionId !== input.sessionId || bundle.deliveryId !== deliveryId) throw new HandoffError('not_found')
    if (bundle.binding.targetId !== input.targetId || !sameSource(bundle.source, bundle.binding.source)
      || (previous && (bundle.packageSha256 !== previous.packageSha256
        || bundle.receiptAttempt.receiptAttemptId !== previous.receiptAttemptId))) {
      throw new HandoffError('binding_mismatch')
    }
    const record: LaunchRecord = {
      schema: SCHEMA, ownerId: context.ownerId, sessionId: bundle.source.sessionId,
      deliveryId, targetId: bundle.binding.targetId, packageSha256: bundle.packageSha256,
      receiptAttemptId: bundle.receiptAttempt.receiptAttemptId,
      attempts: (previous?.attempts ?? []).map(attempt =>
        attempt.state === 'requested' ? { ...attempt, ...result(attempt, 'unknown') } : attempt),
    }
    const createdAt = new Date().toISOString()
    const attempt: Attempt = { launchAttemptId: randomUUID(), requestId: input.requestId, retryOf: input.retryOf,
      state: 'requested', createdAt, updatedAt: createdAt }
    try {
      signal.throwIfAborted()
      const { adapter, target, assertDirectory } = await this.options.preflight.validateForLaunch(call, bundle.binding)
      if (!packageExists(bundle) || !await adapter.canReadPackage(target, bundle, signal)) {
        throw new HandoffError('not_ready')
      }
      const current = await this.options.delivery.getForLaunch(call, deliveryId)
      if (!isDeepStrictEqual(current, bundle)) throw new HandoffError('binding_mismatch')
      if (!packageExists(bundle)) throw new HandoffError('not_ready')
      assertDirectory()
      const launch = this.options.plan(adapter.id, bundle.binding.effectiveCwd, adapter.launchIntent(target, bundle))
      signal.throwIfAborted()
      this.options.requireLocal()
      record.attempts.push(attempt)
      this.options.records.save(key, record, previous === undefined)
      // No await between durable requested and the issuance decision. Cancelling a dispatched action
      // cannot change its outcome; persist the actual observation even if the HTTP client disconnects.
      pending.dispatched = true
      let state: 'opened' | 'failed' | 'unknown'
      try { state = await launch.dispatch() } catch { state = 'unknown' }
      const completed = result(attempt, state)
      record.attempts[record.attempts.length - 1] = { ...attempt, ...completed }
      try { this.options.records.save(key, record) } catch { throw new HandoffError('outcome_unknown') }
      return publicAttempt(completed)
    } catch (error) {
      if (!signal.aborted || deadline.aborted || pending.dispatched) throw error
      const cancelled = result(attempt, 'cancelled')
      record.attempts.push({ ...attempt, ...cancelled })
      this.options.records.save(key, record, previous === undefined)
      return publicAttempt(cancelled)
    }
  }

  private read(context: CallContext, key: string, sessionId: string): LaunchRecord | undefined {
    const record = this.options.records.read(key)
    if (record && (record.ownerId !== context.ownerId || record.sessionId !== sessionId)) throw new HandoffError('not_found')
    return record
  }

  private recover(key: string, attempt: Attempt): LaunchView {
    if (attempt.state === 'requested' && !this.pending.get(key)?.dispatched) {
      return publicAttempt({ ...attempt, state: 'unknown', code: 'outcome_unknown' })
    }
    return publicAttempt(attempt)
  }
}
