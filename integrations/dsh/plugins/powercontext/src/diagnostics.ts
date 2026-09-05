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

import { InvalidResponseError, ServerResponseError, TransportError } from './errors.ts'

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
  '/v1/scope-bindings/resolve',
])

// Only public protocol codes may cross the diagnostic/model boundary.
const PUBLIC_ERROR_CODES = new Set([
  'not_found', 'scope_not_found', 'memory_not_found', 'artifact_not_found',
  'candidate_not_found', 'handoff_evidence_not_found', 'source_definition_not_found',
  'external_skill_not_found', 'conflict', 'revision_conflict', 'memory_entry_inactive',
  'source_conflict', 'candidate_conflict', 'artifact_conflict', 'candidate_terminal',
  'scope_version_conflict', 'scope_idempotency_conflict', 'artifact_publication_conflict',
  'connector_checkpoint_conflict', 'generation_conflict', 'external_skill_snapshot_unavailable',
  'handoff_report_inconsistent', 'invalid_request', 'invalid_scope_relationship',
  'invalid_source_ingestion', 'invalid_lifecycle', 'artifact_publication_unsupported',
  'capability_not_supported', 'unauthorized', 'forbidden', 'authentication_failed',
  'runtime_not_ready', 'generation_unavailable', 'inference_timeout', 'inference_unavailable',
  'handoff_generation_unavailable', 'external_skill_registry_unavailable',
  'handoff_report_unavailable', 'handoff_report_too_large', 'invalid_handoff_generation',
  'remote_skill_distribution_error', 'invalid_target_credential', 'invalid_enrollment',
  'invalid_target_state', 'publication_generation_conflict', 'invalid_skill_lifecycle',
  'internal_error',
])

export function publicErrorCode(code: unknown): string | undefined {
  return typeof code === 'string' && PUBLIC_ERROR_CODES.has(code) ? code : undefined
}

export function isVersionMismatch(error: ServerResponseError): boolean {
  return error.statusCode === 404
    && error.code === undefined
    && COMPATIBILITY_OR_AVAILABILITY_PATHS.has(error.path)
}

const AUTOMATIC_OPERATION_PATHS = new Map([
  ['scope_resolve', '/v1/scope-bindings/resolve'],
  ['context_prepare', '/v1/context/prepare'],
  ['capture_content_source', '/v1/sources/content'],
  ['flush_memory', '/v1/memory/flush'],
])

function responseDiagnostic(event: string, outcome: string, error: ServerResponseError): DiagnosticEvent {
  const code = publicErrorCode(error.code)
  return {
    event,
    outcome,
    http_status: error.statusCode,
    ...(code ? { error_code: code } : {}),
  }
}

function isDomainStatus(status: number): boolean {
  return status === 404 || status === 409 || status === 422
}

export function failureEvent(event: string, error: unknown): DiagnosticEvent | undefined {
  if (error instanceof ServerResponseError) {
    if (error.statusCode === 401) return responseDiagnostic(event, 'authentication_failed', error)
    if (isVersionMismatch(error)) {
      return responseDiagnostic(event, 'version_mismatch', error)
    }
    if (error.statusCode === 503) {
      return {
        ...responseDiagnostic(event, 'server_unavailable', error),
        recovery: 'powercontext doctor',
      }
    }
    if (isDomainStatus(error.statusCode) && AUTOMATIC_OPERATION_PATHS.get(event) !== error.path) return undefined
    return responseDiagnostic(event, 'invalid_response', error)
  }
  if (error instanceof TransportError) {
    return { event, outcome: 'server_unavailable', recovery: 'powercontext doctor' }
  }
  if (error instanceof InvalidResponseError) return { event, outcome: 'invalid_response' }
  return { event, outcome: 'invalid_response' }
}

export function logSafely(log: (event: Record<string, unknown>) => unknown, event: Record<string, unknown>): void {
  try {
    // A diagnostic writer must neither break nor hold up the automatic path.
    void Promise.resolve(log(event)).catch(() => undefined)
  } catch {
    // Native logger failures are best effort, including success/debug logging.
  }
}

export function reportFailure(
  log: (event: Record<string, unknown>) => unknown,
  event: string,
  error: unknown,
): void {
  const diagnostic = failureEvent(event, error)
  if (diagnostic) logSafely(log, diagnostic)
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
    return write(JSON.stringify(normalized))
  }
}
