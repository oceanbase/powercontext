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
import { combineSignals, createTimeoutSignal, type PowerContextClient } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { InvalidResponseError } from './errors.ts'

const CLOSING_BUDGET_MS = 1000

export interface FlushProgress {
  currentCursor: number
  remainingWork: boolean
}

export interface PendingFlushResult {
  acknowledged: boolean
  pendingScopes: number
}

function nonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

export function flushProgress(value: unknown): FlushProgress {
  if (!value || typeof value !== 'object') throw new InvalidResponseError('/v1/memory/flush')
  const result = value as Record<string, unknown>
  if (
    !['idle', 'processed'].includes(String(result.status))
    || !nonNegativeInteger(result.previous_cursor)
    || !nonNegativeInteger(result.current_cursor)
    || !nonNegativeInteger(result.high_watermark)
    || !nonNegativeInteger(result.processed_source_count)
    || (result.remaining_work !== undefined && typeof result.remaining_work !== 'boolean')
  ) {
    throw new InvalidResponseError('/v1/memory/flush')
  }
  return {
    currentCursor: result.current_cursor,
    remainingWork: result.remaining_work === true,
  }
}

function scopeCorrelation(scopeId: string): string {
  return createHash('sha256').update(scopeId).digest('hex').slice(0, 16)
}

export interface PendingSourceFlusher {
  record(scopeId: string, position: number): void
  flush(lifecycleEvent: string, signal?: AbortSignal): Promise<PendingFlushResult>
}

export type DiagnosticFailure = (
  event: string,
  error: unknown,
  extra?: Record<string, unknown>,
) => void

function settleWithinBoundary<T>(
  task: Promise<T>,
  signal: AbortSignal,
  onAbort: () => T,
  onDeadline: () => void,
): Promise<T> {
  const abort = () => {
    onDeadline()
    return onAbort()
  }
  if (signal.aborted) return Promise.resolve(abort())
  return new Promise<T>((resolve, reject) => {
    const handleAbort = () => resolve(abort())
    signal.addEventListener('abort', handleAbort, { once: true })
    task.then(
      (value) => {
        signal.removeEventListener('abort', handleAbort)
        resolve(value)
      },
      (error) => {
        signal.removeEventListener('abort', handleAbort)
        reject(error)
      },
    )
  })
}

export function createPendingSourceFlusher(
  client: PowerContextClient,
  config: ResolvedConfig,
  onFailure?: DiagnosticFailure,
  createBoundarySignal: () => AbortSignal = () => (
    createTimeoutSignal(Math.min(config.httpBudgetMs, CLOSING_BUDGET_MS))
  ),
): PendingSourceFlusher {
  const pending = new Map<string, number>()
  const confirmedCursors = new Map<string, number>()
  let queue = Promise.resolve<PendingFlushResult>({ acknowledged: true, pendingScopes: 0 })

  function reportDeadline(
    lifecycleEvent: string,
    reason: unknown,
    targets: ReadonlyMap<string, number>,
    acknowledgedTargets: ReadonlySet<string>,
  ): void {
    for (const [scopeId, position] of targets) {
      if (acknowledgedTargets.has(scopeId) || (confirmedCursors.get(scopeId) ?? -1) >= position) continue
      try {
        onFailure?.(
          lifecycleEvent === 'session_before_compact' ? 'pre_compaction_checkpoint' : 'flush_memory',
          reason,
          {
            outcome: 'deadline_exceeded',
            failure_class: 'deadline_exceeded',
            host: 'pi',
            lifecycle_event: lifecycleEvent,
            scope_correlation: scopeCorrelation(scopeId),
            target_position: position,
          },
        )
      } catch {
        // Diagnostics are best effort and must not affect the host boundary.
      }
    }
  }

  async function flushAll(
    lifecycleEvent: string,
    targets: ReadonlyMap<string, number>,
    acknowledgedTargets: Set<string>,
    signal: AbortSignal | undefined,
    deadlineExpired: () => boolean,
  ): Promise<PendingFlushResult> {
    if (targets.size === 0) return { acknowledged: true, pendingScopes: pending.size }
    if (signal?.aborted) {
      return { acknowledged: acknowledgedTargets.size === targets.size, pendingScopes: pending.size }
    }
    let acknowledged = true
    let remainingCalls = config.flushMaxCalls
    for (const [scopeId, position] of targets) {
      const confirmedCursor = confirmedCursors.get(scopeId) ?? -1
      const currentPosition = pending.get(scopeId)
      if (currentPosition !== undefined && confirmedCursor >= currentPosition) {
        pending.delete(scopeId)
      }
      if (currentPosition === undefined || confirmedCursor >= position) {
        acknowledgedTargets.add(scopeId)
        continue
      }
      if (remainingCalls === 0) {
        acknowledged = false
        break
      }
      let observedCursor: number | undefined
      let failure: unknown
      let scopeAcknowledged = false
      while (remainingCalls > 0) {
        remainingCalls -= 1
        try {
          const result = await client.request('flush_memory', { scope_id: scopeId }, signal)
          if (deadlineExpired()) break
          const progress = flushProgress(result.kind === 'json' ? result.value : undefined)
          observedCursor = Math.max(observedCursor ?? 0, progress.currentCursor)
          confirmedCursors.set(scopeId, Math.max(confirmedCursors.get(scopeId) ?? 0, progress.currentCursor))
          scopeAcknowledged ||= progress.currentCursor >= position
          if (scopeAcknowledged) {
            acknowledgedTargets.add(scopeId)
            const currentPosition = pending.get(scopeId)
            if (currentPosition !== undefined && observedCursor >= currentPosition) {
              pending.delete(scopeId)
            }
          }
          if (scopeAcknowledged && !progress.remainingWork) {
            break
          }
        } catch (error) {
          failure = error
          break
        }
      }
      if (scopeAcknowledged) {
        continue
      }
      acknowledged = false
      if (deadlineExpired()) continue
      failure ??= new InvalidResponseError('/v1/memory/flush')
      try {
        onFailure?.(lifecycleEvent === 'session_before_compact' ? 'pre_compaction_checkpoint' : 'flush_memory', failure, {
          host: 'pi',
          lifecycle_event: lifecycleEvent,
          scope_correlation: scopeCorrelation(scopeId),
          target_position: position,
          ...(observedCursor === undefined ? {} : { observed_cursor: observedCursor }),
        })
      } catch {
        // Diagnostics are best effort and must not affect the host boundary.
      }
    }
    return {
      acknowledged: acknowledged && acknowledgedTargets.size === targets.size,
      pendingScopes: pending.size,
    }
  }

  return {
    record(scopeId, position) {
      const current = pending.get(scopeId)
      const target = Math.max(current ?? 0, position)
      if ((confirmedCursors.get(scopeId) ?? -1) >= target) {
        pending.delete(scopeId)
      } else {
        pending.set(scopeId, target)
      }
    },
    flush(lifecycleEvent, signal) {
      const targets = new Map(pending)
      const acknowledgedTargets = new Set<string>()
      for (const [scopeId, position] of targets) {
        if ((confirmedCursors.get(scopeId) ?? -1) >= position) {
          acknowledgedTargets.add(scopeId)
        }
      }
      const boundarySignal = createBoundarySignal()
      const combined = signal ? combineSignals([boundarySignal, signal]) : boundarySignal
      let deadlineExpired = boundarySignal.aborted
      const task = queue.then(() => flushAll(
        lifecycleEvent,
        targets,
        acknowledgedTargets,
        combined,
        () => deadlineExpired,
      ))
      queue = task.catch(() => ({ acknowledged: false, pendingScopes: pending.size }))
      return settleWithinBoundary(task, boundarySignal, () => ({
        acknowledged: acknowledgedTargets.size === targets.size,
        pendingScopes: pending.size,
      }), () => {
        deadlineExpired = true
        reportDeadline(lifecycleEvent, boundarySignal.reason, targets, acknowledgedTargets)
      })
    },
  }
}
