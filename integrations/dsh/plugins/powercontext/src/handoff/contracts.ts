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

import type { Wire } from './wire.ts'

// In-process only. The authenticated Host supplies ownerId.
export interface CallContext {
  readonly ownerId: string
  readonly signal: AbortSignal
}

export interface SourceBoundary {
  readonly sessionId: string
  readonly messageId: string
  readonly throughSeq: number
  readonly cwd: string
  /** ISO 8601 time of the selected workspace observation. */
  readonly workspaceObservedAt: string
}

export interface VerifiedPreflight {
  readonly preflightId: string
  readonly revision: number
  readonly source: SourceBoundary
  readonly targetId: string
  readonly targetVersion: string
  readonly adapterVersion: string
  readonly effectiveCwd: string
  readonly serverIdentity: string
  readonly connectionRef: string
  readonly connectionGeneration: number
  readonly scopeId: string
}

export interface ReceiptAttempt {
  readonly receiptAttemptId: string
  readonly sourceType: Wire<'SourceReference'>['name']
  readonly sourceId: string
}

export type HandoffSelection =
  | { readonly kind: 'prepared'; readonly digest: string }
  | { readonly kind: 'revision'; readonly revision: Readonly<Wire<'ArtifactReference'>> & { readonly scope_id: string } }

export interface LaunchBundle {
  readonly deliveryId: string
  readonly source: SourceBoundary
  readonly binding: VerifiedPreflight
  readonly packagePath: string
  readonly packageSha256: string
  readonly handoffSelection: HandoffSelection
  readonly receiptAttempt: ReceiptAttempt
}

export interface ValidateForPrepareInput {
  readonly preflightId: string
  readonly expectedRevision: number
  readonly source: SourceBoundary
}

export interface PreflightGate {
  validateForPrepare(
    context: CallContext,
    input: ValidateForPrepareInput,
  ): Promise<VerifiedPreflight>
}

export type PreparationState = 'preparing' | 'needs_input' | 'prepared' | 'failed' | 'cancelled' | 'unknown'
export type DeliveryErrorCode =
  | 'not_found' | 'not_ready' | 'stale_preflight' | 'binding_mismatch'
  | 'idempotency_conflict' | 'integration_unavailable' | 'capacity_exceeded'
  | 'payload_invalid' | 'outcome_unknown'

const errors: Record<DeliveryErrorCode, string> = {
  not_found: 'The requested delivery or preflight was not found.',
  not_ready: 'The requested operation is not ready.',
  stale_preflight: 'Preflight conditions changed; verify the target again.',
  binding_mismatch: 'The source boundary or delivery binding does not match.',
  idempotency_conflict: 'This request conflicts with an existing delivery.',
  integration_unavailable: 'The delivery integration is unavailable.',
  capacity_exceeded: 'Delivery storage is full. Export and remove a delivery or increase the limit.',
  payload_invalid: 'The delivery input or package is invalid.',
  outcome_unknown: 'Preparation may have run, but no complete package can be recovered.',
}

export function isDeliveryErrorCode(value: unknown): value is DeliveryErrorCode {
  return typeof value === 'string' && Object.hasOwn(errors, value)
}

export class DeliveryError extends Error {
  readonly code: DeliveryErrorCode
  constructor(code: DeliveryErrorCode) {
    super(errors[code])
    this.name = 'DeliveryError'
    this.code = code
  }
}

export interface PrepareDeliveryInput extends ValidateForPrepareInput {
  readonly requestId: string
  readonly workInput?: unknown
  /** An exact, explicitly selected Handoff Revision in the verified Scope. */
  readonly revision?: Wire<'ArtifactReference'>
  /** Explicitly create a new version, retaining the old delivery. Use a new requestId. */
  readonly supersedesDeliveryId?: string
}

export interface DeliveryView {
  readonly deliveryId: string
  readonly preparationState: PreparationState
  readonly source: SourceBoundary
  readonly binding: VerifiedPreflight
  readonly receiptAttempt: ReceiptAttempt
  readonly createdAt: string
  readonly updatedAt: string
  readonly pinned: boolean
  readonly expired: boolean
  readonly error?: { code: DeliveryErrorCode; message: string }
  readonly recoveryActions: readonly ('supply_work_input' | 'create_new_version' | 'export_package')[]
  readonly preview?: { objective: string; disposition: Wire<'HandoffDisposition'>; omissionCount: number }
}

export interface PackageExport {
  readonly filename: string
  readonly mediaType: 'application/json'
  readonly packageSha256: string
  readonly bytes: Uint8Array
}

export interface DeliveryService {
  prepare(context: CallContext, input: PrepareDeliveryInput): Promise<DeliveryView>
  get(context: CallContext, deliveryId: string): Promise<DeliveryView>
  getForLaunch(context: CallContext, deliveryId: string): Promise<LaunchBundle>
  cancelPreparation(context: CallContext, deliveryId: string): Promise<DeliveryView>
  exportPackage(context: CallContext, deliveryId: string): Promise<PackageExport>
}
