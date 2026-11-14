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

import { combineSignals } from '../client.ts'
import type { JsonObject, PowerContextClient } from '../client.ts'
import { RequestNotSentError, ServerResponseError } from '../errors.ts'
import { hasSecretContent } from '../secrets.ts'
import type { CallContext, PrepareDeliveryInput, VerifiedPreflight } from './contracts.ts'
import { DeliveryError } from './contracts.ts'
import { collectMaterials } from './materials.ts'
import type { BoundaryReader } from './materials.ts'
import { invalid, object, preparedDigest, wire } from './wire.ts'
import type { Wire } from './wire.ts'

export type Carrier =
  | { kind: 'prepared'; digest: string; prepared: Wire<'PreparedHandoff'>;
    sourceReceipts: [Wire<'WorkSourceReceipt'> | Wire<'CaptureContentSourceResponse'>] }
  | { kind: 'revision'; revision: Wire<'ArtifactReference'> }

export interface PreparationConnection {
  client: Pick<PowerContextClient, 'request'>
  /** Synchronous Host guard for changes since connection resolution. */
  assertDispatch?: () => void
  /** Opt in only when the trusted connection has a configured generation pipeline. */
  generation?: { maxInputBytes: number; maxOutputBytes?: number }
}

/** Resolve the client for the exact saved binding. Never fall back to a default connection. */
export type PreparationConnectionResolver = (
  context: CallContext, binding: VerifiedPreflight,
) => Promise<PreparationConnection>

export async function prepareCarrier(
  context: CallContext,
  binding: VerifiedPreflight,
  input: Omit<PrepareDeliveryInput, 'workInput'> & { workInput?: Wire<'CurrentWorkHandoff'> },
  deliveryId: string,
  resolve: PreparationConnectionResolver,
  readBoundary: BoundaryReader | undefined,
  markDispatched: () => void,
): Promise<Carrier | undefined> {
  const { client, generation, assertDispatch } = await resolve(context, binding)
  const scope = binding.scopeId
  const call = async (operation: string, payload: JsonObject, mayWrite = false, signal = context.signal) => {
    signal.throwIfAborted()
    if (hasSecretContent(operation, payload)) invalid()
    assertDispatch?.()
    if (mayWrite) markDispatched()
    const result = await client.request(operation, { ...payload, scope_id: scope }, signal)
    if (result.kind !== 'json') invalid()
    return result.value
  }
  if (input.revision) {
    const ref = input.revision
    const result = await call('get_artifact_revision', ref)
    if (!object(result) || result.scope_id !== scope || result.family !== ref.family
      || result.artifact_id !== ref.artifact_id || result.revision !== ref.revision) invalid()
    if (!object(result.content) || result.content.schema !== 'powercontext.handoff.v1') invalid()
    return { kind: 'revision', revision: ref }
  }

  const sourceId = 'dsh-delivery:' + deliveryId
  if (input.workInput) {
    const result = wire('PreparedWorkHandoff', await call('handoff_current_work', {
      source_id: sourceId, handoff: input.workInput,
    }, true))
    return validateCarrier({ kind: 'prepared', digest: preparedDigest(result.handoff), prepared: result.handoff,
      sourceReceipts: [result.boundary] }, binding, deliveryId)
  }
  if (!generation || !readBoundary) return undefined
  if (!Number.isSafeInteger(generation.maxInputBytes) || generation.maxInputBytes < 1024
    || (generation.maxOutputBytes !== undefined && (!Number.isSafeInteger(generation.maxOutputBytes)
      || generation.maxOutputBytes < 512 || generation.maxOutputBytes > 32768))) {
    throw new DeliveryError('integration_unavailable')
  }
  const materials = await collectMaterials(context, binding.source, readBoundary, generation.maxInputBytes)
  if (!materials) return undefined
  const receipt = wire('CaptureContentSourceResponse', await call('capture_content_source', {
    source_id: sourceId, content: materials.serialized, metadata: { origin: 'dsh', kind: 'handoff-input' },
  }, true))
  if (receipt.source.name !== 'content' || receipt.source.source_id !== sourceId) invalid()
  const draft = wire('HandoffDraft', await call('prepare_handoff', {
    objective: materials.objective,
    evidence: [{ kind: 'source', source_ref: receipt.source }],
    max_bytes: generation.maxOutputBytes ?? 8000,
  }, true, combineSignals([context.signal, AbortSignal.timeout(90_000)])))
  // Core validates and checks the draft's exact evidence on finalize. Keep the receipt and the
  // draft unchanged; never send data returned by the model if it matches the plugin secret filter.
  const prepared = wire('PreparedHandoff', await call('finalize_handoff', { draft }, true))
  return validateCarrier({ kind: 'prepared', digest: preparedDigest(prepared), prepared,
    sourceReceipts: [receipt] }, binding, deliveryId)
}

export function validateCarrier(value: unknown, binding: VerifiedPreflight, deliveryId: string): Carrier {
  if (!object(value)) invalid()
  if (value.kind === 'revision') {
    const revision = wire('ArtifactReference', value.revision)
    if (revision.family !== 'handoff') invalid()
    return { kind: 'revision', revision }
  }
  if (value.kind !== 'prepared' || !Array.isArray(value.sourceReceipts) || value.sourceReceipts.length !== 1) invalid()
  const prepared = wire('PreparedHandoff', value.prepared)
  if (prepared.scope_id !== binding.scopeId || (prepared.base !== null && prepared.base.family !== 'handoff')
    || (prepared.content.generation && prepared.content.generation.scope_id !== binding.scopeId)) {
    throw new DeliveryError('binding_mismatch')
  }
  const digest = preparedDigest(prepared)
  if (value.digest !== digest || hasSecretContent('commit_handoff', { handoff: prepared })) invalid()
  const receipt = value.sourceReceipts[0]
  if (!object(receipt)) invalid()
  const actual = 'kind' in receipt ? wire('WorkSourceReceipt', receipt) : wire('CaptureContentSourceResponse', receipt)
  if (('kind' in actual && actual.kind !== 'handoff-boundary')
    || actual.source.name !== 'content' || actual.source.source_id !== 'dsh-delivery:' + deliveryId
    || (actual.source.locator?.kind === 'sequence' && actual.source.locator.sequence !== actual.position)) invalid()
  return { kind: 'prepared', digest, prepared, sourceReceipts: [actual] }
}

export function knownPreparationFailure(error: unknown): DeliveryError | undefined {
  if (error instanceof RequestNotSentError) return new DeliveryError('integration_unavailable')
  if (error instanceof ServerResponseError && error.statusCode >= 400 && error.statusCode < 500
    && error.statusCode !== 408 && error.statusCode !== 499) {
    if (error.statusCode === 404) return new DeliveryError('not_found')
    return new DeliveryError([400, 409, 422].includes(error.statusCode) ? 'payload_invalid' : 'integration_unavailable')
  }
  return undefined
}
