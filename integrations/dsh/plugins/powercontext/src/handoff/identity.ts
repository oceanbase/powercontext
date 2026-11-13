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

import type { ReceiptAttempt, VerifiedPreflight } from './contracts.ts'
import { boundary } from './materials.ts'
import { invalid, object, trimmed } from './wire.ts'

export const deliveryIdPattern = /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/
export const checksumPattern = /^[0-9a-f]{64}$/

export function verifiedBinding(value: unknown): VerifiedPreflight {
  if (!object(value)) invalid()
  const { preflightId, revision, source, targetId, targetVersion, adapterVersion, effectiveCwd,
    serverIdentity, connectionRef, connectionGeneration, scopeId } = value
  if (!trimmed(preflightId, 256) || typeof revision !== 'number' || !Number.isSafeInteger(revision) || revision < 1
    || !object(source) || !trimmed(targetId, 256) || !trimmed(targetVersion, 256) || !trimmed(adapterVersion, 256)
    || !trimmed(effectiveCwd, 4096) || effectiveCwd.includes('\0') || !trimmed(serverIdentity, 256)
    || !trimmed(connectionRef, 256) || (typeof connectionGeneration !== 'number' || !Number.isSafeInteger(connectionGeneration) || connectionGeneration < 0) || !trimmed(scopeId, 256)) invalid()
  // Pick only the shared contract. Never spread credentials or other Host-private fields to disk.
  return Object.freeze({ preflightId, revision,
    source: Object.freeze(boundary(source as unknown as VerifiedPreflight['source'])),
    targetId, targetVersion, adapterVersion, effectiveCwd, serverIdentity, connectionRef, connectionGeneration, scopeId })
}

export function receiptIdentity(value: unknown): ReceiptAttempt {
  if (!object(value) || typeof value.receiptAttemptId !== 'string' || !deliveryIdPattern.test(value.receiptAttemptId)
    || value.sourceType !== 'content' || value.sourceId !== 'dsh-ack:' + value.receiptAttemptId) invalid()
  return { receiptAttemptId: value.receiptAttemptId, sourceType: value.sourceType, sourceId: value.sourceId }
}
