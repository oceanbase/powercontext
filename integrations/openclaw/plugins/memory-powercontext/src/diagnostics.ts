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

import { PowerContextRequestError } from './http.js'

export interface DiagnosticEvent {
  event: string
  outcome: string
  http_status?: number
  error_code?: string
  recovery?: string
  [key: string]: unknown
}

const COMPATIBILITY_OR_AVAILABILITY_PATHS = new Set([
  '/health/live',
  '/health/ready',
  '/v1/capabilities',
  '/v1/context/prepare',
])

const AUTOMATIC_OPERATION_PATHS = new Map([
  ['context_prepare', '/v1/context/prepare'],
  ['capture_source', '/v1/sources/content'],
  ['pre_compaction_flush', '/v1/memory/flush'],
  ['session_end_flush', '/v1/memory/flush'],
])

function responseDiagnostic(event: string, outcome: string, error: PowerContextRequestError): DiagnosticEvent {
  return {
    event,
    outcome,
    ...(error.status !== undefined ? { http_status: error.status } : {}),
    ...(error.code ? { error_code: error.code } : {}),
  }
}

function isDomainStatus(status: number): boolean {
  return status === 404 || status === 409 || status === 422
}

export function failureEvent(event: string, error: unknown): DiagnosticEvent | undefined {
  if (error instanceof PowerContextRequestError) {
    if (error.status === 401) return responseDiagnostic(event, 'authentication_failed', error)
    if (error.status === 404 && COMPATIBILITY_OR_AVAILABILITY_PATHS.has(error.path) && error.code === undefined) {
      return responseDiagnostic(event, 'version_mismatch', error)
    }
    if (error.status === 503) {
      return {
        ...responseDiagnostic(event, 'server_unavailable', error),
        recovery: 'powercontext doctor',
      }
    }
    if (
      error.status !== undefined
      && isDomainStatus(error.status)
      && AUTOMATIC_OPERATION_PATHS.get(event) !== error.path
    ) return undefined
    if (error.status !== undefined) return responseDiagnostic(event, 'invalid_response', error)
    return { event, outcome: 'server_unavailable', recovery: 'powercontext doctor' }
  }
  return { event, outcome: 'invalid_response' }
}

export function createDiagnosticEmitter(
  write: (line: string) => void,
  now: () => number = Date.now,
  cooldownMs = 60_000,
): (event: Record<string, unknown>) => void {
  const lastEmitted = new Map<string, number>()
  return (event) => {
    const outcome = typeof event.outcome === 'string' ? event.outcome : undefined
    const normalized = {
      ...event,
      ...(outcome === 'server_unavailable' && event.recovery === undefined
        ? { recovery: 'powercontext doctor' }
        : {}),
    }
    if (outcome && !['ready', 'ok', 'empty', 'skipped'].includes(outcome)) {
      const key = outcome
      const timestamp = now()
      const previous = lastEmitted.get(key)
      if (previous !== undefined && timestamp - previous < cooldownMs) return
      lastEmitted.set(key, timestamp)
    }
    write(JSON.stringify(normalized))
  }
}
