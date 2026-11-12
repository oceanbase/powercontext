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


const ERRORS = {
  not_found: [404, 'The requested record is unavailable.'],
  not_ready: [409, 'The handoff is not ready for this action.'],
  stale_preflight: [409, 'Preflight conditions changed. Check the selected response and run preflight again.'],
  binding_mismatch: [409, 'The destination no longer matches the saved directory, Server, Scope, or target. Run preflight again.'],
  idempotency_conflict: [409, 'The request conflicts with an existing operation. Read its state before continuing.'],
  integration_unavailable: [503, 'The required handoff integration is unavailable.'],
  capacity_exceeded: [429, 'Handoff bookkeeping reached its capacity.'],
  payload_invalid: [400, 'The handoff request or package is invalid.'],
  outcome_unknown: [409, 'The outcome is unknown. Check the target before choosing to retry.'],
  cancelled: [409, 'The operation was cancelled.'],
  deployment_unsupported: [403, 'Handoff requires a verified owner-local DSH deployment.'],
} as const

export type HandoffErrorCode = keyof typeof ERRORS

export class HandoffError extends Error {
  readonly code: HandoffErrorCode
  readonly status: number

  constructor(code: HandoffErrorCode) {
    super(ERRORS[code][1])
    this.code = code
    this.status = ERRORS[code][0]
  }
}

export function handoffError(error: unknown): HandoffError {
  return error instanceof HandoffError ? error : new HandoffError('integration_unavailable')
}
