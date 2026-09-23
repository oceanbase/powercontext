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

import { authenticationRejection, bodyFailureDetails, type BodyFailureDetails, RESPONSE_ISSUES, InvalidResponseError, ResponseReadError, ServerResponseError, TransportError } from './errors.ts'

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
    ...(error.requestId ? { request_id: error.requestId } : {}),
    ...(code ? { error_code: code } : {}),
  }
}

function isDomainStatus(status: number): boolean {
  return status === 404 || status === 409 || status === 422
}

export function failureEvent(event: string, error: unknown): DiagnosticEvent | undefined {
  const rejection = authenticationRejection(error)
  if (rejection && !(error instanceof ServerResponseError)) return {
    ...responseDiagnostic(event, rejection.statusCode === 401 ? 'authentication_failed' : 'invalid_response', rejection),
    ...bodyFailureDetails(error),
  }
  if (error instanceof ResponseReadError) return { event, outcome: 'server_unavailable',
    http_status: error.statusCode, ...(error.requestId ? { request_id: error.requestId } : {}),
    ...bodyFailureDetails(error), recovery: 'powercontext doctor' }
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

export interface OperationFailure extends BodyFailureDetails {
  state: 'ok' | 'failed' | 'degraded' | 'skipped'
  code: string
  operation: string
  message: string
  recovery?: string
  http_status?: number
  request_id?: string
  protocol_issue?: keyof typeof RESPONSE_ISSUES
  dependencies?: Record<string, string>
  operations?: string[]
}

function record(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function check(operation: string, code: string, message: string, recovery?: string,
  state: OperationFailure['state'] = 'failed'): OperationFailure {
  return { state, code, operation, message, ...(recovery ? { recovery } : {}) }
}

function requestId(value: string | undefined): { request_id?: string } {
  return value && /^[a-zA-Z0-9._:-]{1,128}$/.test(value) ? { request_id: value } : {}
}

function transportFailure(error: unknown): [string, string, string] {
  const cause = error instanceof TransportError ? error.cause : error
  if (cause instanceof Error && cause.name === 'TimeoutError') {
    return ['request_timeout', 'The request exceeded its deadline.',
      'Check the effective requestTimeoutMs and the running Server latency; inspect the failing dependency before increasing the timeout.']
  }
  if (cause instanceof Error && cause.name === 'AbortError') {
    return ['cancelled', 'The request was cancelled.', 'Run /pc doctor again when the current cancellation has completed.']
  }
  const detail = record(cause) && record(cause.cause) ? cause.cause : cause
  const code = record(detail) ? detail.code : undefined
  if (code === 'ECONNREFUSED') return ['connection_refused', 'The configured endpoint refused the connection.',
    'Start the intended Server and verify its listening host and port against the running plugin configuration.']
  if (code === 'ENOTFOUND' || code === 'EAI_AGAIN') return ['dns_lookup_failed', 'The configured endpoint hostname could not be resolved.',
    'Check the hostname in POWERCONTEXT_DSH_BASE_URL or the plugin baseUrl and the host DNS configuration.']
  if (typeof code === 'string' && ['CERT_HAS_EXPIRED', 'DEPTH_ZERO_SELF_SIGNED_CERT', 'UNABLE_TO_VERIFY_LEAF_SIGNATURE'].includes(code)) {
    return ['tls_verification_failed', 'TLS certificate verification failed.',
      'Correct the Server certificate chain, trust configuration or hostname; do not disable certificate verification.']
  }
  return ['connection_failed', 'The HTTP transport failed before a usable response was received.',
    'Check the effective endpoint host/port, proxy, network and Server service logs. The transport did not identify a narrower cause.']
}

export function operationFailure(operation: string, error: unknown): OperationFailure {
  const rejection = authenticationRejection(error)
  if (rejection && !(error instanceof ServerResponseError)) {
    const result = operationFailure(operation, rejection)
    const body = bodyFailureDetails(error)
    return { ...result, ...body,
      message: result.message + (body.response_body_error ? ` Reading the response body also failed (${body.response_body_error}).` : ''),
      ...(error instanceof InvalidResponseError && error.issue ? { protocol_issue: error.issue } : {}) }
  }
  if (error instanceof ResponseReadError) {
    const body = bodyFailureDetails(error)
    const code = error.statusCode === 404 ? 'unclassified_not_found'
      : error.statusCode === 503 ? 'service_unavailable'
      : error.statusCode >= 400 ? 'http_error' : body.response_body_error!
    return { ...check(operation, code,
      `Received HTTP ${error.statusCode}, but reading the response body failed (${body.response_body_error}).`
        + (error.statusCode === 404 ? ' The unread error body cannot distinguish a missing resource from a missing route.' : ''),
      'Use this operation, HTTP status and request ID in Server logs; check Server/proxy response-body delivery. The operation result was not validated.'),
      http_status: error.statusCode, ...requestId(error.requestId), ...body }
  }
  if (error instanceof ServerResponseError) {
    const code = publicErrorCode(error.code)
    let result: OperationFailure
    if (error.statusCode === 401) result = check(operation, 'authentication_failed', 'The Server rejected authentication for this operation.',
      'Set POWERCONTEXT_DSH_AUTHORIZATION to the intended Server credential and restart the DSH process so it receives the override.')
    else if (error.statusCode === 403) result = check(operation, 'authorization_failed', 'The authenticated principal is not allowed to perform this operation.',
      'Check the principal permissions for this operation and selected Scope on the running Server.')
    else if (error.statusCode === 404 && error.code === undefined) result = check(operation, 'required_route_missing', 'The required operation returned HTTP 404 without a domain error code.',
      'Check this operation in the Server API and proxy route table, the plugin base-path setting, and the installed Server/plugin refs. A 404 alone cannot identify which configuration is wrong.')
    else if (error.statusCode === 404 && code === 'scope_not_found') result = check(operation, code, 'The Server could not find the requested Scope.',
      'Check POWERCONTEXT_DSH_SCOPE_ID first, then the session workspace binding and Server default Scope. Select an existing Scope explicitly; Doctor does not change bindings.')
    else if (error.statusCode === 404) result = code
      ? check(operation, code, 'The Server returned a recognized domain-level HTTP 404 for this operation.',
        'Inspect the selected resource and Scope in the Server. This domain response does not establish a missing HTTP route.')
      : check(operation, 'unclassified_not_found', 'The operation returned HTTP 404 with an unrecognized error code.',
        'Use the operation and request ID in the Server logs. This response cannot distinguish a missing resource from a missing route; inspect the contract check separately.')
    else if (error.statusCode === 503) result = check(operation, code ?? 'service_unavailable', 'The Server returned HTTP 503 for this operation.',
      'Inspect the separate readiness dependency results and the running Server logs for this operation.')
    else result = check(operation, code ?? 'http_error', 'The Server returned an HTTP error for this operation.',
      'Use this operation, HTTP status and request ID to locate the request in the Server logs.')
    return { ...result, http_status: error.statusCode, ...requestId(error.requestId) }
  }
  if (error instanceof InvalidResponseError) return {
    ...check(operation, 'invalid_response', error.issue ? RESPONSE_ISSUES[error.issue] : 'The response does not satisfy this operation protocol.',
      'Verify the effective endpoint and proxy target serve PowerContext, and use matching Server/plugin refs. Inspect Server logs using the request ID.'),
    ...requestId(error.requestId),
    ...(error.issue ? { protocol_issue: error.issue } : {}),
    ...(error.statusCode === undefined ? {} : { http_status: error.statusCode }),
    ...bodyFailureDetails(error),
  }
  if (error instanceof TransportError || (error instanceof Error && ['AbortError', 'TimeoutError'].includes(error.name))) {
    const [code, message, recovery] = transportFailure(error)
    return check(operation, code, message, recovery)
  }
  return check(operation, 'diagnostic_error', 'A local operation failed before its result could be validated.',
    'Inspect the DSH plugin logs for this operation and report the installed plugin commit. No Server root cause was established.')
}
