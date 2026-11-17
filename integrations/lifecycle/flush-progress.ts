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

// The flush acknowledgement cases in tests/integration_conformance/cases.json exercise the flush response check
// through each plugin.

export interface FlushProgress {
  currentCursor: number
  remainingWork: boolean
}

export function nonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

/** Return the progress a memory flush response reports, or undefined when the response is not a flush result. */
export function readFlushProgress(value: unknown): FlushProgress | undefined {
  if (!value || typeof value !== 'object') return undefined
  const result = value as Record<string, unknown>
  if (
    !['idle', 'processed'].includes(String(result.status))
    || !nonNegativeInteger(result.previous_cursor)
    || !nonNegativeInteger(result.current_cursor)
    || !nonNegativeInteger(result.high_watermark)
    || !nonNegativeInteger(result.processed_source_count)
    || (result.remaining_work !== undefined && typeof result.remaining_work !== 'boolean')
  ) {
    return undefined
  }
  return {
    currentCursor: result.current_cursor,
    remainingWork: result.remaining_work === true,
  }
}

/** Return a short, stable label that correlates one Scope's diagnostics without naming the Scope. */
export function scopeCorrelation(scopeId: string): string {
  return createHash('sha256').update(scopeId).digest('hex').slice(0, 16)
}
