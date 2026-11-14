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
import type { CallContext, DeliveryView, LaunchBundle } from './contracts.ts'

export type ReceiptState = 'awaiting' | 'accepted' | 'needs_clarification' | 'declined'
export type ReceiptReadState = 'not_found' | 'verified' | 'unavailable' | 'forbidden' | 'invalid' | 'stale'
export type ReceiptAttribution =
  | { readonly kind: 'self_reported'; readonly receiver: string }
  | { readonly kind: 'authenticated'; readonly receiver: string; readonly principal: { readonly type: string; readonly id: string } }

export interface ReceiptView {
  readonly deliveryId: string
  readonly state: ReceiptState
  readonly readState: ReceiptReadState
  readonly observedAt: string
  readonly error?: 'read_unavailable' | 'access_denied' | 'receipt_invalid'
  readonly attribution?: ReceiptAttribution
  readonly confirmedAt?: string
}

/** Caller enforces owner and source-session authorization and supplies the saved bundle. */
export interface ReceiptReader {
  read(context: CallContext, bundle: LaunchBundle): Promise<ReceiptView>
  /** Historical confirmation only; undefined means no indexed, verified observation. */
  saved(context: CallContext, delivery: DeliveryView): Promise<ReceiptView | undefined>
}
