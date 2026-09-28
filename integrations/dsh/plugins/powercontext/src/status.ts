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

import { createHash } from 'node:crypto'
import { operationFailure } from './doctor.ts'
import { sessionCwd } from './scope.ts'
import { type BodyFailureDetails, InvalidResponseError, RequestNotSentError, ResponseReadError, ServerResponseError, TransportError,
  writeFailureConfirmation } from './errors.ts'

export const STATUS_SESSION_LIMIT = 64
export const STATUS_STALE_AFTER_MS = 300_000

const OPERATIONS = {
  scope: 'resolve_scope_binding', prepare: 'prepare_context', capture: 'capture_content_source',
  flush: 'flush_memory', injection: 'context_inject',
} as const
export type StatusStage = keyof typeof OPERATIONS
type State = 'not_yet_observed' | 'running' | 'resolved' | 'ready' | 'empty' | 'accepted'
  | 'completed' | 'incomplete' | 'appended' | 'skipped' | 'unavailable'

export interface StageResult extends BodyFailureDetails {
  state: State
  code?: string
  message?: string
  recovery?: string
  http_status?: number
  request_id?: string
  protocol_issue?: string
  content_bytes?: number
  confirmation?: 'rejected' | 'unconfirmed'
}

interface Observation extends StageResult {
  operation: string
  observed_at: number | null
}

interface Attempt {
  attempt: number
  turn: string
  started_at: number
  scope_id?: string
  scope_key?: string
  stages: Record<StatusStage, Observation>
}

export interface StatusAttempt {
  scope: (scopeId: string) => void
  record: (stage: StatusStage, result: StageResult) => void
  skip: (stage: StatusStage, reason: SkipReason) => void
  fail: (stage: StatusStage, error: unknown, writeAttempted?: boolean, signal?: AbortSignal) => void
}

const SKIP_REASONS = {
  no_messages: 'No messages were supplied to this pre-step.',
  empty_input: 'The supplied messages contain no non-empty text.',
  no_user_text: 'No non-empty user-authored text was eligible for capture.',
  capture_disabled: 'Automatic prompt capture is disabled in the running plugin.',
  source_too_long: 'The user text exceeds the Source length limit.',
  sensitive_content: 'The user text matched the secret exclusion rules.',
  scope_unresolved: 'No Scope was resolved for this attempt.',
  scope_failed: 'Scope resolution failed; see the scope observation.',
  cancelled: 'The automatic-path signal was cancelled before this stage started.',
  deadline_exceeded: 'The automatic-path deadline expired before this stage started.',
  no_prepared_content: 'No usable prepared content was returned; see the prepare observation.',
  downstream_rejected: 'The downstream pre-step did not enter a model request.',
  flush_disabled: 'Automatic flushing after Source capture is disabled.',
  capture_not_confirmed: 'Source acceptance was not confirmed; flushing was not started.',
  capture_rejected: 'The capture request was rejected; flushing was not started.',
  capture_skipped: 'Source capture was skipped; see the capture observation.',
  source_position_missing: 'The capture response did not provide a valid position for flushing.',
} as const
export type SkipReason = keyof typeof SKIP_REASONS

export function cancellationReason(signal?: AbortSignal): SkipReason {
  return signal?.reason instanceof Error && signal.reason.name === 'TimeoutError' ? 'deadline_exceeded' : 'cancelled'
}

function fingerprint(value: string): string {
  return createHash('sha256').update(value).digest('hex')
}

function sessionKey(sessionId: string, cwd?: string): string {
  return fingerprint(JSON.stringify([sessionId, sessionCwd(cwd) ?? null]))
}

function initialStages(): Attempt['stages'] {
  return Object.fromEntries(Object.entries(OPERATIONS).map(([stage, operation]) => [stage, {
    operation, state: 'not_yet_observed', observed_at: null,
  }])) as Attempt['stages']
}

/** Content-free observations owned by one running plugin, never reconstructed from logs. */
export class RuntimeStatus {
  private readonly sessions = new Map<string, Attempt>()
  private sequence = 0

  private readonly now: () => number

  constructor(now: () => number = Date.now) { this.now = now }

  begin(sessionId: string, cwd: string | undefined, turn: string): StatusAttempt {
    const key = sessionKey(sessionId, cwd)
    const attempt: Attempt = {
      attempt: ++this.sequence, turn: /^\d{1,20}$/.test(turn) ? turn : '(unavailable)',
      started_at: this.now(), stages: initialStages(),
    }
    this.sessions.delete(key)
    this.sessions.set(key, attempt)
    if (this.sessions.size > STATUS_SESSION_LIMIT) this.sessions.delete(this.sessions.keys().next().value!)
    const record = (stage: StatusStage, result: StageResult) => {
      // An older completion, including an evicted/recreated session, cannot replace the latest attempt.
      if (this.sessions.get(key) !== attempt) return
      attempt.stages[stage] = { ...result, operation: OPERATIONS[stage], observed_at: this.now() }
    }
    return {
      scope: (scopeId) => {
        if (this.sessions.get(key) !== attempt) return
        attempt.scope_key = fingerprint(scopeId)
        attempt.scope_id = /^[a-zA-Z0-9_-]{1,128}$/.test(scopeId) ? scopeId : '(redacted)'
        record('scope', { state: 'resolved' })
      },
      record,
      skip: (stage, reason) => record(stage, { state: 'skipped', code: reason, message: SKIP_REASONS[reason] }),
      fail: (stage, error, writeAttempted = false, signal) => {
        let observedError = error
        if (signal?.aborted && !(error instanceof ServerResponseError) && !(error instanceof InvalidResponseError)
          && !(error instanceof ResponseReadError)) {
          const cause = new DOMException('Automatic operation stopped',
            cancellationReason(signal) === 'deadline_exceeded' ? 'TimeoutError' : 'AbortError')
          observedError = error instanceof RequestNotSentError ? new RequestNotSentError('', cause) : new TransportError('', cause)
        }
        const { state: _state, operation: _operation, ...failure } = operationFailure(OPERATIONS[stage], observedError)
        const confirmation = writeAttempted ? writeFailureConfirmation(observedError) : undefined
        record(stage, { ...failure, state: 'unavailable',
          ...(confirmation ? { confirmation } : {}) })
      },
    }
  }

  read(sessionId: string | undefined, cwd: string | undefined, currentScope?: string) {
    const attempt = sessionId ? this.sessions.get(sessionKey(sessionId, cwd)) : undefined
    const now = this.now()
    const age = attempt ? Math.max(0, now - attempt.started_at) : null
    const staleReason = !attempt ? undefined
      : !currentScope ? 'scope_unverified'
      : !attempt.scope_key ? 'scope_not_observed'
      : attempt.scope_key !== fingerprint(currentScope) ? 'scope_changed'
      : age! >= STATUS_STALE_AFTER_MS ? 'age_limit' : undefined
    const stages = attempt?.stages ?? initialStages()
    return {
      observation: 'local_automatic_path',
      freshness: !attempt ? 'not_yet_observed' : staleReason ? 'stale' : 'current',
      ...(staleReason ? { stale_reason: staleReason } : {}),
      ...(!sessionId ? { reason: 'session_identity_unavailable' } : {}),
      attempt: attempt?.attempt ?? null, turn: attempt?.turn ?? null,
      started_at: attempt ? new Date(attempt.started_at).toISOString() : null,
      age_ms: age, stale_after_ms: STATUS_STALE_AFTER_MS,
      observed_scope: attempt?.scope_id ?? null,
      stages: Object.fromEntries(Object.entries(stages).map(([stage, value]) => [stage, {
        ...value,
        observed_at: value.observed_at === null ? null : new Date(value.observed_at).toISOString(),
        age_ms: value.observed_at === null ? null : Math.max(0, now - value.observed_at),
      }])),
      coverage: 'Local observation age is not Memory freshness. Source acceptance and flush progress do not prove Memory production. '
        + 'Appended means added to pre-step messages, not proof of model consumption. Unconfirmed writes may have taken effect. '
        + 'Rejected describes the failed request only; earlier capture or flush work is not rolled back. '
        + 'Use /pc doctor for current Server/configuration diagnosis.',
    }
  }
}
