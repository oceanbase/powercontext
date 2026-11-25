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
import type { PowerContextClient } from '../client.ts'
import { readdirSync, unlinkSync } from 'node:fs'
import { join } from 'node:path'
import { ServerResponseError } from '../errors.ts'
import type { CallContext, DeliveryView, LaunchBundle, VerifiedPreflight } from './contracts.ts'
import { receiptIdentity, verifiedBinding } from './identity.ts'
import { sameBoundary } from './materials.ts'
import { HandoffRecords, keyFor, privateDirectory } from './records.ts'
import type { ReceiptAttribution, ReceiptReader, ReceiptState, ReceiptView } from './receipt-contract.ts'
import { object, sha256, trimmed, wire } from './wire.ts'

type Principal = { type: string; id: string }
export interface ReceiptConnection {
  client: Pick<PowerContextClient, 'request'>
  /** Trusted deployment fact; an absent or unknown mode must not be treated as disabled. */
  accessMode: 'enforced' | 'disabled'
  expectedPrincipal?: Principal
}
/** The Host must change connectionGeneration when access mode or the expected Principal changes. */
export type ReceiptConnectionResolver = (context: CallContext, binding: VerifiedPreflight) => Promise<ReceiptConnection>
interface Observation {
  schema: 'powercontext.dsh-receipt-observation.v1'
  fingerprint: string
  state: Exclude<ReceiptState, 'awaiting'>
  attribution: ReceiptAttribution
  confirmedAt: string
}
const schema = 'powercontext.dsh-receipt-observation.v1' as const
const indexSchema = 'powercontext.dsh-receipt-index.v1' as const
const retentionMs = 30 * 86_400_000
interface ObservationIndex {
  schema: typeof indexSchema
  deliveryId: string
  ownerKey: string
  bindingKey: string
  fingerprint: string
  confirmedAt: string
  orphanedAt?: string
}

function indexKey(owner: string, deliveryId: string): string { return keyFor(owner, deliveryId) }
function bindingKey(binding: VerifiedPreflight, attempt: LaunchBundle['receiptAttempt']): string {
  return keyFor(JSON.stringify(verifiedBinding(binding)), JSON.stringify(receiptIdentity(attempt)))
}

function fingerprint(owner: string, bundle: LaunchBundle): string {
  receiptIdentity(bundle.receiptAttempt)
  return keyFor(owner, bundle.deliveryId, JSON.stringify(bundle.binding), bundle.packageSha256,
    JSON.stringify(bundle.handoffSelection), JSON.stringify(bundle.receiptAttempt))
}

function checkReceipt(source: unknown, bundle: LaunchBundle, connection: ReceiptConnection): Omit<Observation, 'schema' | 'fingerprint' | 'confirmedAt'> {
  if (!object(source) || source.handoff_receipt !== true || source.scope_id !== bundle.binding.scopeId
    || source.source_type !== 'content' || source.source_id !== bundle.receiptAttempt.sourceId
    || !Number.isSafeInteger(source.position) || (source.position as number) < 1
    || typeof source.content !== 'string'
    || source.content_digest !== 'sha256:' + sha256(JSON.stringify(source.content))) throw new Error('invalid receipt')
  const value: unknown = JSON.parse(source.content)
  if (!object(value) || value.schema !== 'powercontext.handoff-receipt.v1' || value.trust !== 'untrusted_observation'
    || !trimmed(value.receiver, 256) || !['accepted', 'needs_clarification', 'declined'].includes(value.status as string)
    || !['available', 'unavailable'].includes(value.evidence_status as string)
    || !Array.isArray(value.unavailable_evidence) || value.unavailable_evidence.length > 2080
    || (value.evidence_status === 'available') !== (value.unavailable_evidence.length === 0)) throw new Error('invalid receipt')
  const fields = ['schema', 'trust', 'receiver', 'status', 'selection', 'selected_revision', 'prepared_digest', 'receiver_checks', 'evidence_status', 'unavailable_evidence', 'message']
  if (Object.keys(value).some(key => !fields.includes(key)) || fields.some(key => !Object.hasOwn(value, key))) throw new Error('invalid receipt')
  for (const citation of value.unavailable_evidence) {
    if (object(citation) && citation.kind === 'source' && object(citation.source_ref)) {
      const { source_type, ...rest } = citation.source_ref
      wire('HandoffCitation', { ...citation, source_ref: { ...rest, name: source_type } })
    } else wire('HandoffCitation', citation)
  }
  if (bundle.handoffSelection.kind === 'prepared') {
    if (value.selection !== 'prepared' || value.selected_revision !== null || value.prepared_digest !== bundle.handoffSelection.digest) throw new Error('invalid receipt')
  } else {
    const expected = bundle.handoffSelection.revision
    const revision = wire('ArtifactReference', value.selected_revision)
    if (value.selection !== 'exact' || value.prepared_digest !== null || revision.family !== expected.family
      || revision.artifact_id !== expected.artifact_id || revision.revision !== expected.revision) throw new Error('invalid receipt')
  }
  const checks = value.receiver_checks
  if (checks !== null && (!object(checks) || Object.keys(checks).sort().join(',') !== 'authorization,capability,live_state'
    || !['confirmed', 'mismatch', 'not_checked'].includes(checks.live_state as string)
    || !['confirmed', 'insufficient', 'not_checked'].includes(checks.capability as string)
    || !['confirmed', 'insufficient', 'not_checked'].includes(checks.authorization as string))) throw new Error('invalid receipt')
  if (value.status === 'accepted' && (value.evidence_status !== 'available' || !object(checks)
    || checks.live_state !== 'confirmed' || checks.capability !== 'confirmed' || checks.authorization !== 'confirmed')) throw new Error('invalid receipt')
  if (value.status !== 'accepted' && (typeof value.message !== 'string' || !value.message.trim() || Array.from(value.message).length > 8192)) throw new Error('invalid receipt')
  if (value.message !== null && typeof value.message !== 'string') throw new Error('invalid receipt')
  let attribution: ReceiptAttribution
  if (connection.accessMode === 'enforced') {
    const identity = source.receipt_identity
    if (!object(identity) || identity.receiver_identity_matches !== true || !object(identity.principal)
      || !trimmed(identity.principal.id, 256) || !trimmed(identity.principal.type, 128)
      || identity.principal.id !== value.receiver
      || (connection.expectedPrincipal && (identity.principal.id !== connection.expectedPrincipal.id
        || identity.principal.type !== connection.expectedPrincipal.type))) throw new Error('invalid identity')
    attribution = { kind: 'authenticated', receiver: value.receiver,
      principal: { type: identity.principal.type, id: identity.principal.id } }
  } else if (connection.accessMode === 'disabled' && !connection.expectedPrincipal && source.receipt_identity == null) {
    attribution = { kind: 'self_reported', receiver: value.receiver }
  } else throw new Error('unknown access mode')
  return { state: value.status as Exclude<ReceiptState, 'awaiting'>, attribution }
}

/** Independent receipt observations; use under the Host's existing single-writer lease. */
export class LocalReceiptReader implements ReceiptReader {
  private readonly records: HandoffRecords<Observation>
  private readonly indexes: HandoffRecords<ObservationIndex>
  private readonly indexDirectory: string
  constructor(private readonly options: { directory: string; resolveConnection: ReceiptConnectionResolver; now?: () => number }) {
    privateDirectory(options.directory)
    this.records = new HandoffRecords(options.directory, schema)
    this.indexDirectory = join(options.directory, 'by-delivery')
    privateDirectory(this.indexDirectory)
    this.indexes = new HandoffRecords(this.indexDirectory, indexSchema)
  }

  private previous(context: CallContext, bundle: LaunchBundle): Observation | undefined {
    const id = fingerprint(context.ownerId, bundle)
    const record = this.records.read(id)
    if (!record) return undefined
    if (record.fingerprint !== id || !['accepted', 'needs_clarification', 'declined'].includes(record.state)
      || !object(record.attribution) || !['authenticated', 'self_reported'].includes(record.attribution.kind)
      || !trimmed(record.attribution.receiver, 256) || (record.attribution.kind === 'authenticated'
        && (!object(record.attribution.principal) || !trimmed(record.attribution.principal.type, 128)
          || !trimmed(record.attribution.principal.id, 256) || record.attribution.principal.id !== record.attribution.receiver))
      || !Number.isFinite(Date.parse(record.confirmedAt))) throw new Error('invalid observation')
    return record
  }

  private index(context: CallContext, bundle: LaunchBundle, record: Observation): void {
    const id = indexKey(context.ownerId, bundle.deliveryId)
    const existing = this.indexes.read(id)
    const next: ObservationIndex = { schema: indexSchema, deliveryId: bundle.deliveryId, ownerKey: keyFor(context.ownerId),
      bindingKey: bindingKey(bundle.binding, bundle.receiptAttempt), fingerprint: record.fingerprint, confirmedAt: record.confirmedAt }
    if (existing && (existing.deliveryId !== next.deliveryId || existing.ownerKey !== next.ownerKey
      || existing.bindingKey !== next.bindingKey || existing.fingerprint !== next.fingerprint
      || existing.confirmedAt !== next.confirmedAt)) throw new Error('invalid receipt index')
    if (!existing || existing.orphanedAt) this.indexes.save(id, next, !existing)
  }

  async saved(context: CallContext, delivery: DeliveryView): Promise<ReceiptView | undefined> {
    context.signal.throwIfAborted()
    const index = this.indexes.read(indexKey(context.ownerId, delivery.deliveryId))
    if (!index) return undefined
    if (index.deliveryId !== delivery.deliveryId || index.ownerKey !== keyFor(context.ownerId)
      || index.bindingKey !== bindingKey(delivery.binding, delivery.receiptAttempt)
      || !sameBoundary(delivery.source, delivery.binding.source)
      || !/^[0-9a-f]{64}$/.test(index.fingerprint)) throw new Error('invalid receipt index')
    const record = this.records.read(index.fingerprint)
    // An interrupted first write may leave the index without its observation.
    if (!record) return undefined
    if (record.fingerprint !== index.fingerprint || record.confirmedAt !== index.confirmedAt
      || !['accepted', 'needs_clarification', 'declined'].includes(record.state)
      || !object(record.attribution) || !['authenticated', 'self_reported'].includes(record.attribution.kind)
      || !trimmed(record.attribution.receiver, 256) || (record.attribution.kind === 'authenticated'
        && (!object(record.attribution.principal) || !trimmed(record.attribution.principal.type, 128)
          || !trimmed(record.attribution.principal.id, 256) || record.attribution.principal.id !== record.attribution.receiver))
      || !Number.isFinite(Date.parse(record.confirmedAt))) throw new Error('invalid receipt observation')
    return { deliveryId: delivery.deliveryId, state: record.state, readState: 'stale',
      observedAt: new Date(this.options.now?.() ?? Date.now()).toISOString(),
      confirmedAt: record.confirmedAt, attribution: record.attribution }
  }

  /** Call under the Host lease with all live delivery IDs for this owner, after delivery maintenance. */
  async maintain(context: CallContext, liveDeliveryIds: ReadonlySet<string>): Promise<void> {
    context.signal.throwIfAborted()
    const now = this.options.now?.() ?? Date.now()
    const ownerKey = keyFor(context.ownerId)
    for (const name of readdirSync(this.indexDirectory)) {
      context.signal.throwIfAborted()
      if (!/^[0-9a-f]{64}\.json$/.test(name)) continue
      const key = name.slice(0, -5)
      let entry: ObservationIndex | undefined
      try { entry = this.indexes.read(key) } catch { continue }
      if (!entry || entry.ownerKey !== ownerKey || key !== indexKey(context.ownerId, entry.deliveryId)
        || !/^[0-9a-f]{64}$/.test(entry.fingerprint)) continue
      if (liveDeliveryIds.has(entry.deliveryId)) {
        if (entry.orphanedAt) { const { orphanedAt: _, ...active } = entry; this.indexes.save(key, active) }
      } else if (!entry.orphanedAt) {
        this.indexes.save(key, { ...entry, orphanedAt: new Date(now).toISOString() })
      } else if (Number.isFinite(Date.parse(entry.orphanedAt)) && now - Date.parse(entry.orphanedAt) >= retentionMs) {
        // Only erase a matching observation; preserve unsupported or damaged files.
        let record: Observation | undefined
        try { record = this.records.read(entry.fingerprint) } catch { continue }
        if (record && (record.fingerprint !== entry.fingerprint || record.confirmedAt !== entry.confirmedAt)) continue
        if (record) unlinkSync(join(this.options.directory, entry.fingerprint + '.json'))
        unlinkSync(join(this.indexDirectory, name))
      }
    }
  }

  /** For LocalDeliveryService.prune: never performs a network request. */
  async confirmedAt(context: CallContext, bundle: LaunchBundle): Promise<number | undefined> {
    const value = this.previous(context, bundle)
    if (value) this.index(context, bundle, value)
    return value ? Date.parse(value.confirmedAt) : undefined
  }

  async read(context: CallContext, bundle: LaunchBundle): Promise<ReceiptView> {
    context.signal.throwIfAborted()
    const observedAt = new Date(this.options.now?.() ?? Date.now()).toISOString()
    const previous = this.previous(context, bundle)
    if (previous) this.index(context, bundle, previous)
    const view = (readState: ReceiptView['readState'], error?: ReceiptView['error']): ReceiptView => {
      // A parallel request may have confirmed the receipt while this read was in flight.
      const latest = this.previous(context, bundle) ?? previous
      return { deliveryId: bundle.deliveryId, state: latest?.state ?? 'awaiting', readState, observedAt,
        ...(error ? { error } : {}), ...(latest ? { attribution: latest.attribution, confirmedAt: latest.confirmedAt } : {}) }
    }
    let result: Awaited<ReturnType<PowerContextClient['request']>>
    let connection: ReceiptConnection
    try {
      connection = await this.options.resolveConnection(context, bundle.binding)
      if (previous && (connection.accessMode === 'disabled' && (previous.attribution.kind !== 'self_reported' || connection.expectedPrincipal)
        || connection.accessMode === 'enforced' && (previous.attribution.kind !== 'authenticated'
          || connection.expectedPrincipal && (previous.attribution.principal.type !== connection.expectedPrincipal.type
            || previous.attribution.principal.id !== connection.expectedPrincipal.id)))) {
        return { deliveryId: bundle.deliveryId, state: 'awaiting', readState: 'invalid', observedAt, error: 'receipt_invalid' }
      }
      result = await connection.client.request('get_source', { scope_id: bundle.binding.scopeId,
        source_type: 'content', source_id: bundle.receiptAttempt.sourceId }, context.signal)
    } catch (error) {
      context.signal.throwIfAborted()
      if (error instanceof ServerResponseError && error.statusCode === 404) return view('not_found')
      if (error instanceof ServerResponseError && [401, 403].includes(error.statusCode)) return view('forbidden', 'access_denied')
      return view('unavailable', 'read_unavailable')
    }
    let verified: ReturnType<typeof checkReceipt>
    try {
      if (result.kind !== 'json') throw new Error('invalid response')
      verified = checkReceipt(result.value, bundle, connection)
    } catch {
      return view('invalid', 'receipt_invalid')
    }
    try {
      context.signal.throwIfAborted()
      // Re-read after the asynchronous request so concurrent observers cannot overwrite confirmation.
      const current = this.previous(context, bundle)
      if (current && JSON.stringify({ state: current.state, attribution: current.attribution }) !== JSON.stringify(verified)) return view('invalid', 'receipt_invalid')
      const pending = this.indexes.read(indexKey(context.ownerId, bundle.deliveryId))
      if (pending && !Number.isFinite(Date.parse(pending.confirmedAt))) throw new Error('invalid receipt index')
      const record: Observation = current ?? { schema, fingerprint: fingerprint(context.ownerId, bundle), ...verified,
        confirmedAt: pending?.confirmedAt ?? observedAt }
      // Index first: an interrupted observation write leaves a discoverable, non-confirming entry
      // that can be retried or reclaimed by maintenance.
      this.index(context, bundle, record)
      if (!current) this.records.save(record.fingerprint, record, true)
      return { deliveryId: bundle.deliveryId, state: record.state, readState: 'verified', observedAt,
        attribution: record.attribution, confirmedAt: record.confirmedAt }
    } catch {
      context.signal.throwIfAborted()
      return view('unavailable', 'read_unavailable')
    }
  }
}
