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
  /** Existing Source type (for the current ACK capture route, content). */
  readonly sourceType: string
  readonly sourceId: string
}

export type HandoffSelection =
  | { readonly kind: 'prepared'; readonly digest: string }
  | { readonly kind: 'revision'; readonly revision: { readonly scope_id: string; readonly family: string
    readonly artifact_id: string; readonly revision_id: string } }

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

export interface PrepareDeliveryInput extends ValidateForPrepareInput {
  readonly requestId: string
  readonly workInput?: unknown
}

export interface PreflightGate {
  validateForPrepare(context: CallContext, input: ValidateForPrepareInput): Promise<VerifiedPreflight>
}

export interface DeliveryView {
  readonly deliveryId: string
  readonly preparationState: 'preparing' | 'prepared' | 'needs_input' | 'failed' | 'cancelled' | 'unknown'
  readonly source: SourceBoundary
  readonly binding: VerifiedPreflight
  readonly receiptAttempt: ReceiptAttempt
  readonly createdAt: string
  readonly updatedAt: string
  readonly error?: { readonly code: string; readonly message: string }
  readonly recoveryActions: readonly string[]
}

export interface DeliveryService {
  prepare(context: CallContext, input: PrepareDeliveryInput): Promise<DeliveryView>
  get(context: CallContext, deliveryId: string): Promise<DeliveryView>
  /** Return only an authorized, fully saved delivery with verified package integrity. */
  getForLaunch(context: CallContext, deliveryId: string): Promise<LaunchBundle>
  cancelPreparation(context: CallContext, deliveryId: string): Promise<DeliveryView>
  exportPackage(context: CallContext, deliveryId: string): Promise<PackageExport>
}

export interface PackageExport {
  readonly filename: string
  readonly mediaType: 'application/json'
  readonly packageSha256: string
  readonly bytes: Uint8Array
}
