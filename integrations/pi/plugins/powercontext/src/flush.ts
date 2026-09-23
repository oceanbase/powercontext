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

import { combineSignals, createTimeoutSignal, type PowerContextClient } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { flushThrough } from './checkpoints.ts'

const CLOSING_BUDGET_MS = 1000

export interface PendingSourceFlusher {
  record(scopeId: string, position: number): void
  flush(signal?: AbortSignal): Promise<void>
}

export type DiagnosticFailure = (event: string, error: unknown) => void

export function createPendingSourceFlusher(
  client: PowerContextClient,
  config: ResolvedConfig,
  onFailure?: DiagnosticFailure,
): PendingSourceFlusher {
  const pending = new Map<string, number>()
  let inFlight: Promise<void> | undefined

  async function flushAll(signal?: AbortSignal): Promise<void> {
    if (pending.size === 0) return
    const signals = [createTimeoutSignal(Math.min(config.httpBudgetMs, CLOSING_BUDGET_MS))]
    if (signal) signals.push(signal)
    const combined = combineSignals(signals)
    for (const [scopeId, position] of pending) {
      try {
        const reached = await flushThrough(client, scopeId, position, config.flushMaxCalls, combined)
        if (reached && pending.get(scopeId) === position) pending.delete(scopeId)
      } catch (error) {
        try { onFailure?.('flush_memory', error) } catch { /* Diagnostics cannot affect shutdown. */ }
      }
    }
  }

  return {
    record(scopeId, position) {
      const current = pending.get(scopeId)
      pending.set(scopeId, Math.max(current ?? 0, position))
    },
    flush(signal) {
      if (inFlight) return inFlight
      const task = flushAll(signal)
      inFlight = task
      void task.finally(() => {
        if (inFlight === task) inFlight = undefined
      })
      return task
    },
  }
}
