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
import { combineSignals } from '../client.ts'
import { DeliveryError, isDeliveryErrorCode } from './contracts.ts'
import type { CallContext, DeliveryErrorCode, DeliveryService, DeliveryView, LaunchBundle, PackageExport,
  PreflightGate, PrepareDeliveryInput, VerifiedPreflight } from './contracts.ts'
import { deliveryIdPattern, receiptIdentity, verifiedBinding } from './identity.ts'
import { boundary, inspectedWork, sameBoundary } from './materials.ts'
import type { BoundaryReader } from './materials.ts'
import { knownPreparationFailure, prepareCarrier, validateCarrier } from './prepare.ts'
import type { Carrier, PreparationConnectionResolver } from './prepare.ts'
import { DELIVERY_SCHEMA, DeliveryStore, MAX_PACKAGE_BYTES, MAX_REQUEST_KEYS } from './store.ts'
import type { DeliveryRecord } from './store.ts'
import { invalid, object, sha256, trimmed, wire } from './wire.ts'

const DAY = 86_400_000
const SEVEN_DAYS = 7 * DAY
const THIRTY_DAYS = 30 * DAY
const MAX_METADATA_RECORDS = 1000
const defaultLimits = { maxDeliveries: 100, maxBytes: 50 * 1024 * 1024 }

type NormalizedInput = ReturnType<typeof normalize>
type Operation = { controller: AbortController; done?: Promise<DeliveryView> }

export interface DeliveryOptions {
  /** Absolute path under trusted Host user data. Parent must already exist. */
  directory: string
  preflightGate?: PreflightGate
  resolveConnection: PreparationConnectionResolver
  readBoundary?: BoundaryReader
  limits?: Partial<typeof defaultLimits>
  now?: () => number
}

function normalize(input: PrepareDeliveryInput) {
  const fields = ['requestId', 'preflightId', 'expectedRevision', 'source', 'workInput', 'revision', 'supersedesDeliveryId']
  if (!object(input) || Object.keys(input).some(field => !fields.includes(field)) || !trimmed(input.requestId, 128) || !trimmed(input.preflightId, 256)
    || !Number.isSafeInteger(input.expectedRevision) || input.expectedRevision < 1
    || (input.supersedesDeliveryId !== undefined && !deliveryIdPattern.test(input.supersedesDeliveryId))) invalid()
  const revision = input.revision === undefined ? undefined : wire('ArtifactReference', input.revision)
  if (revision && (revision.family !== 'handoff' || input.workInput !== undefined)) invalid()
  return {
    requestId: input.requestId,
    preflightId: input.preflightId,
    expectedRevision: input.expectedRevision,
    source: boundary(input.source),
    workInput: input.workInput === undefined ? undefined : inspectedWork(input.workInput),
    revision,
    supersedesDeliveryId: input.supersedesDeliveryId,
  }
}

function key(ownerId: string, requestId: string): string {
  return JSON.stringify([ownerId, requestId])
}

function sameBinding(a: VerifiedPreflight, b: VerifiedPreflight): boolean {
  return JSON.stringify(a) === JSON.stringify(b) // Both are projected in contract order.
}

function packageData(record: DeliveryRecord, carrier: Carrier) {
  return {
    schema: DELIVERY_SCHEMA,
    deliveryId: record.deliveryId,
    source: record.binding.source,
    binding: record.binding,
    receiptAttempt: record.receiptAttempt,
    carrier,
  }
}

function safeError(error: unknown): DeliveryError {
  // The injected gate and connection resolver can use different error classes.
  // Keep only the shared code and never pass through remote messages or causes.
  const code = object(error) ? error.code : undefined
  if (isDeliveryErrorCode(code)) return new DeliveryError(code)
  if (code === 'ENOSPC' || code === 'EDQUOT') return new DeliveryError('capacity_exceeded')
  return new DeliveryError('integration_unavailable')
}

/** Task 4 supplies the real gate and exact connection resolver when it wires the Host. */
export class LocalDeliveryService implements DeliveryService {
  private readonly store: DeliveryStore
  private readonly active = new Map<string, Operation>()
  private readonly preflightReaders = new Map<string, number>()
  private readonly launchReaders = new Map<string, number>()
  private readonly requestIndex = new Map<string, { deliveryId: string; fingerprint: string }>()
  private readonly limits: typeof defaultLimits
  private readonly options: DeliveryOptions
  private closing?: Promise<void>

  constructor(options: DeliveryOptions) {
    this.options = options
    this.limits = { ...defaultLimits, ...options.limits }
    if (Object.values(this.limits).some(limit => !Number.isSafeInteger(limit) || limit < 1)
      || this.limits.maxDeliveries > MAX_METADATA_RECORDS) invalid()
    try {
      this.store = new DeliveryStore(options.directory)
      try {
        for (const record of this.store.records.values()) {
          for (const request of record.requests) {
            const indexKey = key(record.ownerId, request.id)
            if (this.requestIndex.has(indexKey)) invalid()
            this.requestIndex.set(indexKey, { deliveryId: record.deliveryId, fingerprint: request.fingerprint })
          }
          if (record.deletedAt || record.expiredAt) this.store.removePackage(record.deliveryId)
          else if (record.preparationState === 'preparing') this.recover(record)
        }
      } catch (error) {
        this.store.close()
        throw error
      }
    } catch (error) {
      throw safeError(error)
    }
  }

  private get now(): number {
    return this.options.now?.() ?? Date.now()
  }

  private updated(record: DeliveryRecord, changes: Partial<DeliveryRecord>): DeliveryRecord {
    try {
      return this.store.put({ ...record, ...changes, updatedAt: new Date(this.now).toISOString() })
    } catch (error) {
      throw safeError(error)
    }
  }

  private caller(context: CallContext): void {
    if (this.closing) throw new DeliveryError('integration_unavailable')
    if (!trimmed(context.ownerId, 256)) throw new DeliveryError('not_found')
    if (context.signal.aborted) throw new DeliveryError('not_ready')
  }

  private owned(context: CallContext, deliveryId: string): DeliveryRecord {
    this.caller(context)
    const record = this.store.records.get(deliveryId)
    if (!record || record.ownerId !== context.ownerId || record.deletedAt) throw new DeliveryError('not_found')
    return record
  }

  private replay(owner: string, request: string, fingerprint: string): DeliveryRecord | undefined {
    const replay = this.requestIndex.get(key(owner, request))
    if (!replay) return undefined
    if (replay.fingerprint !== fingerprint) throw new DeliveryError('idempotency_conflict')
    const record = this.store.records.get(replay.deliveryId)
    if (!record || record.deletedAt) throw new DeliveryError('not_found')
    return record
  }

  private validatedPackage(record: DeliveryRecord) {
    if (!record.package || record.expiredAt || record.deletedAt) invalid()
    const bytes = this.store.readPackage(record.deliveryId)
    if (bytes.length !== record.package.bytes || sha256(bytes) !== record.package.sha256) invalid()
    const value: unknown = JSON.parse(bytes.toString('utf8'))
    if (!object(value) || value.schema !== DELIVERY_SCHEMA || value.deliveryId !== record.deliveryId
      || !object(value.source) || !sameBoundary(boundary(value.source as unknown as VerifiedPreflight['source']), record.binding.source)
      || !sameBinding(verifiedBinding(value.binding), record.binding)
      || JSON.stringify(receiptIdentity(value.receiptAttempt)) !== JSON.stringify(record.receiptAttempt)) invalid()
    const carrier = validateCarrier(value.carrier, record.binding, record.deliveryId)
    return { bytes, carrier, sha256: record.package.sha256 }
  }

  private recover(record: DeliveryRecord): DeliveryRecord {
    let ready = false
    try {
      this.validatedPackage(record)
      ready = true
    } catch {
      // A boundary Source, or a valid-looking unbound file, cannot recover the original.
    }
    return this.updated(record, ready ? { preparationState: 'prepared', error: undefined }
      : { preparationState: 'unknown', error: 'outcome_unknown' })
  }

  private reconcile(record: DeliveryRecord): DeliveryRecord {
    return record.preparationState === 'preparing' && !this.active.has(record.deliveryId) ? this.recover(record) : record
  }

  private view(record: DeliveryRecord): DeliveryView {
    record = this.reconcile(record)
    let preview: DeliveryView['preview']
    if (record.preparationState === 'prepared' && !record.expiredAt) {
      try {
        const { carrier } = this.validatedPackage(record)
        if (carrier.kind === 'prepared') {
          const { objective, disposition, omissions } = carrier.prepared.content
          preview = { objective, disposition, omissionCount: omissions.length }
        }
      } catch {
        record = this.updated(record, { preparationState: 'unknown', error: 'outcome_unknown' })
      }
    }
    const recovery: Array<DeliveryView['recoveryActions'][number]> = []
    if (record.preparationState === 'prepared' && !record.expiredAt) recovery.push('export_package')
    if (record.preparationState === 'needs_input') recovery.push('supply_work_input')
    if (record.expiredAt || ['unknown', 'failed', 'cancelled', 'needs_input'].includes(record.preparationState)) {
      recovery.push('create_new_version')
    }
    return structuredClone({
      deliveryId: record.deliveryId,
      preparationState: record.preparationState,
      source: record.binding.source,
      binding: record.binding,
      receiptAttempt: record.receiptAttempt,
      createdAt: record.createdAt,
      updatedAt: record.updatedAt,
      pinned: record.pinned,
      expired: !!record.expiredAt,
      ...(preview ? { preview } : {}),
      ...(record.error ? { error: { code: record.error, message: new DeliveryError(record.error).message } } : {}),
      recoveryActions: recovery,
    })
  }

  private matchingOperation(owner: string, binding: VerifiedPreflight, input: NormalizedInput, fingerprint: string) {
    let same: DeliveryRecord | undefined
    let active: DeliveryRecord | undefined
    let saved: DeliveryRecord | undefined
    const records = [...this.store.records.values()].filter(record => record.ownerId === owner
      && record.binding.targetId === binding.targetId && sameBoundary(record.binding.source, binding.source))
    const superseded = new Set(records.map(record => record.supersedesDeliveryId))
    for (const record of records) {
      if (record.deletedAt || record.expiredAt || superseded.has(record.deliveryId)) continue
      if (record.preparationState === 'unknown' || record.preparationState === 'preparing') active ??= record
      if (!sameBinding(record.binding, binding)) continue
      if (record.preparationState === 'prepared') saved = record
      if (record.requests.some(request => request.fingerprint === fingerprint)
        && ['prepared', 'preparing', 'needs_input', 'unknown'].includes(record.preparationState)) same = record
    }
    if (input.supersedesDeliveryId) {
      const original = this.store.records.get(input.supersedesDeliveryId)
      if (!original || original.deletedAt || original.ownerId !== owner) throw new DeliveryError('not_found')
      if (original.binding.targetId !== binding.targetId || !sameBoundary(original.binding.source, binding.source)) {
        throw new DeliveryError('binding_mismatch')
      }
      // A second request for the same replacement reuses it, rather than branching the history.
      if (same && same.supersedesDeliveryId === original.deliveryId) return same
      if (original.preparationState === 'preparing' || this.active.has(original.deliveryId)
        || (active && active.deliveryId !== original.deliveryId)) throw new DeliveryError('not_ready')
      if (superseded.has(original.deliveryId)) throw new DeliveryError('idempotency_conflict')
      return undefined
    }
    if (active?.preparationState === 'unknown') {
      if (same?.deliveryId === active.deliveryId) return active
      throw new DeliveryError('outcome_unknown')
    }
    if (active && same?.deliveryId !== active.deliveryId) {
      throw new DeliveryError(sameBinding(active.binding, binding) ? 'idempotency_conflict' : 'binding_mismatch')
    }
    if (saved && !same) throw new DeliveryError('idempotency_conflict')
    return same
  }

  private usage(): { bytes: number; count: number } {
    let bytes = 0
    let count = 0
    for (const record of this.store.records.values()) {
      if (record.deletedAt || record.expiredAt) continue
      if (record.package) {
        bytes += record.package.bytes
        count++
      } else if (record.preparationState === 'preparing') {
        bytes += Math.min(this.limits.maxBytes, MAX_PACKAGE_BYTES)
        count++
      } else if (record.preparationState === 'unknown') count++
    }
    return { bytes, count }
  }

  async prepare(context: CallContext, input: PrepareDeliveryInput): Promise<DeliveryView> {
    this.caller(context)
    const normalized = normalize(input)
    const { requestId, preflightId } = normalized
    const fingerprint = sha256(JSON.stringify({ ...normalized, requestId: undefined }))
    const owner = context.ownerId
    const replay = this.replay(owner, requestId, fingerprint)
    if (replay) return this.view(replay)
    const gate = this.options.preflightGate
    if (!gate) throw new DeliveryError('integration_unavailable')
    this.preflightReaders.set(preflightId, (this.preflightReaders.get(preflightId) ?? 0) + 1)
    try {
      return await this.prepareVerified(context, gate, normalized, fingerprint)
    } finally {
      const count = this.preflightReaders.get(preflightId)!
      if (count === 1) this.preflightReaders.delete(preflightId)
      else this.preflightReaders.set(preflightId, count - 1)
    }
  }

  /** Includes callers between gate admission and the publication of a delivery record. */
  referencedPreflightIds(): ReadonlySet<string> {
    const ids = new Set(this.preflightReaders.keys())
    for (const record of this.store.records.values()) {
      if (!record.deletedAt) ids.add(record.binding.preflightId)
    }
    return ids
  }

  private async prepareVerified(context: CallContext, gate: PreflightGate, normalized: NormalizedInput, fingerprint: string) {
    const { preflightId, expectedRevision, source, requestId } = normalized
    const owner = context.ownerId
    let binding: VerifiedPreflight
    try {
      binding = verifiedBinding(await gate.validateForPrepare(context, { preflightId, expectedRevision, source: { ...source } }))
    } catch (error) {
      if (context.signal.aborted) throw new DeliveryError('not_ready')
      throw safeError(error)
    }
    this.caller(context)
    if (binding.preflightId !== preflightId || !sameBoundary(binding.source, source)) throw new DeliveryError('binding_mismatch')
    if (binding.revision !== expectedRevision) throw new DeliveryError('stale_preflight')

    // Everything through reservation and publication of the identity is synchronous.
    const concurrentReplay = this.replay(owner, requestId, fingerprint)
    if (concurrentReplay) return this.view(concurrentReplay)
    const same = this.matchingOperation(owner, binding, normalized, fingerprint)
    if (same) {
      if (same.requests.length >= MAX_REQUEST_KEYS) throw new DeliveryError('capacity_exceeded')
      const updated = this.updated(same, { requests: [...same.requests, { id: requestId, fingerprint }] })
      this.requestIndex.set(key(owner, requestId), { deliveryId: same.deliveryId, fingerprint })
      return this.view(updated)
    }
    const { bytes, count } = this.usage()
    if (count >= this.limits.maxDeliveries || bytes + Math.min(this.limits.maxBytes, MAX_PACKAGE_BYTES) > this.limits.maxBytes
      || this.store.records.size >= MAX_METADATA_RECORDS) throw new DeliveryError('capacity_exceeded')
    const deliveryId = randomUUID()
    const receiptAttemptId = randomUUID()
    const record = this.updated({
      schema: DELIVERY_SCHEMA, deliveryId, ownerId: owner, requests: [{ id: requestId, fingerprint }],
      binding, receiptAttempt: { receiptAttemptId, sourceType: 'content', sourceId: 'dsh-ack:' + receiptAttemptId },
      preparationState: 'preparing', dispatched: false, pinned: false,
      createdAt: new Date(this.now).toISOString(), updatedAt: '',
      supersedesDeliveryId: normalized.supersedesDeliveryId,
    }, {})
    this.requestIndex.set(key(owner, requestId), { deliveryId, fingerprint })
    const operation: Operation = { controller: new AbortController() }
    this.active.set(deliveryId, operation)
    const signal = combineSignals([context.signal, operation.controller.signal])
    operation.done = this.run({ ownerId: owner, signal }, record, normalized)
    return operation.done
  }

  private async run(context: CallContext, initial: DeliveryRecord, input: NormalizedInput): Promise<DeliveryView> {
    const id = initial.deliveryId
    const latest = () => this.store.records.get(id)!
    const { signal } = context
    let final: DeliveryRecord
    try {
      const carrier = await prepareCarrier(context, initial.binding, input, id, this.options.resolveConnection,
        this.options.readBoundary, () => {
          signal.throwIfAborted()
          if (!latest().dispatched) this.updated(latest(), { dispatched: true })
        })
      signal.throwIfAborted()
      if (!carrier) final = this.updated(latest(), { preparationState: 'needs_input' })
      else {
        const bytes = Buffer.from(JSON.stringify(packageData(initial, carrier)))
        if (bytes.length > this.limits.maxBytes || bytes.length > MAX_PACKAGE_BYTES) {
          throw new DeliveryError('capacity_exceeded')
        }
        const staged = this.updated(latest(), { package: { bytes: bytes.length, sha256: sha256(bytes) } })
        this.store.writePackage(id, bytes)
        final = this.updated(staged, { preparationState: 'prepared' })
      }
    } catch (error) {
      const record = latest()
      const known = knownPreparationFailure(error)
      if (record.preparationState !== 'preparing') final = record
      else if (record.package) final = this.recover(record)
      else {
        let state: DeliveryRecord['preparationState']
        let code: DeliveryErrorCode | undefined
        if (record.dispatched && !known) {
          state = 'unknown'
          code = error instanceof DeliveryError && error.code === 'capacity_exceeded' ? error.code : 'outcome_unknown'
        } else if (signal.aborted) state = 'cancelled'
        else {
          state = 'failed'
          code = (known ?? safeError(error)).code
        }
        final = this.updated(record, { preparationState: state, error: code })
      }
    } finally {
      this.active.delete(id)
    }
    return this.view(final)
  }

  async get(context: CallContext, deliveryId: string): Promise<DeliveryView> {
    return this.view(this.owned(context, deliveryId))
  }

  async list(context: CallContext): Promise<DeliveryView[]> {
    this.caller(context)
    return [...this.store.records.values()].filter(record => record.ownerId === context.ownerId && !record.deletedAt)
      .sort((a, b) => b.createdAt.localeCompare(a.createdAt)).map(record => this.view(record))
  }

  private saved(record: DeliveryRecord) {
    record = this.reconcile(record)
    if (record.expiredAt) throw new DeliveryError('not_found')
    if (record.preparationState !== 'prepared') {
      throw new DeliveryError(record.preparationState === 'unknown' ? 'outcome_unknown' : 'not_ready')
    }
    try {
      return this.validatedPackage(record)
    } catch {
      this.updated(record, { preparationState: 'unknown', error: 'outcome_unknown' })
      throw new DeliveryError('outcome_unknown')
    }
  }

  private launchBundle(record: DeliveryRecord): LaunchBundle {
    const { carrier, sha256 } = this.saved(record)
    return structuredClone({
      deliveryId: record.deliveryId,
      source: record.binding.source,
      binding: record.binding,
      packagePath: this.store.packagePath(record.deliveryId),
      packageSha256: sha256,
      handoffSelection: carrier.kind === 'prepared' ? { kind: 'prepared', digest: carrier.digest }
        : { kind: 'revision', revision: { scope_id: record.binding.scopeId, ...carrier.revision } },
      receiptAttempt: record.receiptAttempt,
    })
  }

  private holdPath(deliveryId: string): () => void {
    this.launchReaders.set(deliveryId, (this.launchReaders.get(deliveryId) ?? 0) + 1)
    return () => {
      const remaining = this.launchReaders.get(deliveryId)! - 1
      if (remaining) this.launchReaders.set(deliveryId, remaining)
      else this.launchReaders.delete(deliveryId)
    }
  }

  async getForLaunch(context: CallContext, deliveryId: string): Promise<LaunchBundle> {
    const bundle = this.launchBundle(this.owned(context, deliveryId))
    // A concurrent delete in the same turn must not unlink a path being returned.
    queueMicrotask(this.holdPath(deliveryId))
    return bundle
  }

  /** Use when a later asynchronous step needs the path to remain on disk. */
  async withLaunchBundle<T>(
    context: CallContext, deliveryId: string, use: (bundle: LaunchBundle) => Promise<T>,
  ): Promise<T> {
    const bundle = this.launchBundle(this.owned(context, deliveryId))
    const release = this.holdPath(deliveryId)
    try {
      return await use(bundle)
    } finally {
      release()
    }
  }

  async exportPackage(context: CallContext, deliveryId: string): Promise<PackageExport> {
    const { bytes, sha256 } = this.saved(this.owned(context, deliveryId))
    return { bytes, packageSha256: sha256, mediaType: 'application/json', filename: deliveryId + '.json' }
  }

  async cancelPreparation(context: CallContext, deliveryId: string): Promise<DeliveryView> {
    let record = this.owned(context, deliveryId)
    if (record.preparationState === 'preparing') {
      if (record.package) record = this.recover(record)
      else record = this.updated(record, record.dispatched
        ? { preparationState: 'unknown', error: 'outcome_unknown' }
        : { preparationState: 'cancelled' })
      this.active.get(deliveryId)?.controller.abort()
    } else if (record.preparationState === 'needs_input') {
      record = this.updated(record, { preparationState: 'cancelled' })
    }
    return this.view(record)
  }

  async pin(context: CallContext, deliveryId: string, pinned: boolean): Promise<DeliveryView> {
    const record = this.owned(context, deliveryId)
    if (record.expiredAt) throw new DeliveryError('not_found')
    if (typeof pinned !== 'boolean') invalid()
    return this.view(this.updated(record, { pinned }))
  }

  async delete(context: CallContext, deliveryId: string): Promise<void> {
    const record = this.owned(context, deliveryId)
    if (this.active.has(deliveryId) || this.launchReaders.has(deliveryId)) throw new DeliveryError('not_ready')
    this.updated(record, { deletedAt: new Date(this.now).toISOString(), package: undefined })
    try {
      this.store.removePackage(deliveryId)
    } catch (error) {
      throw safeError(error)
    }
  }

  /** Only the future exact ACK verifier may supply confirmedAt. No ACK state is written here. */
  async prune(
    context: CallContext,
    confirmedAt?: (bundle: LaunchBundle) => Promise<number | undefined>,
  ): Promise<void> {
    this.caller(context)
    for (const original of [...this.store.records.values()]) {
      this.caller(context)
      if (original.ownerId !== context.ownerId || original.pinned && !original.deletedAt
        || this.active.has(original.deliveryId) || this.launchReaders.has(original.deliveryId)) continue
      const id = original.deliveryId
      const age = this.now - Date.parse(original.deletedAt ?? original.expiredAt ?? original.updatedAt)
      if ((original.deletedAt || original.expiredAt || ['failed', 'cancelled', 'needs_input'].includes(original.preparationState))
        && age >= THIRTY_DAYS) {
        try {
          this.store.forget(id)
          for (const request of original.requests) this.requestIndex.delete(key(original.ownerId, request.id))
        } catch (error) {
          throw safeError(error)
        }
      } else if (confirmedAt && original.preparationState === 'prepared' && !original.expiredAt && !original.deletedAt) {
        let at: number | undefined
        try {
          at = await this.withLaunchBundle(context, id, confirmedAt)
        } catch (error) {
          if (error instanceof DeliveryError && error.code === 'outcome_unknown') continue
          throw safeError(error)
        }
        this.caller(context)
        const current = this.store.records.get(id)
        if (current !== original || this.launchReaders.has(id) || !Number.isSafeInteger(at) || at === undefined
          || at < Date.parse(original.createdAt) || this.now - at < SEVEN_DAYS) continue
        this.updated(original, { expiredAt: new Date(at).toISOString(), package: undefined })
        try {
          this.store.removePackage(id)
        } catch (error) {
          throw safeError(error)
        }
      }
    }
  }

  /** Dispose on Host shutdown. Requests that may have run recover as unknown. */
  close(): Promise<void> {
    if (this.launchReaders.size) return Promise.reject(new DeliveryError('not_ready'))
    return this.closing ??= (async () => {
      for (const operation of this.active.values()) operation.controller.abort()
      await Promise.allSettled([...this.active.values()].map(operation => operation.done))
      this.store.close()
    })()
  }
}
