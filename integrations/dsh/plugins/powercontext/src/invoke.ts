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

import type { PowerContextClient, JsonObject } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import { failureEvent, isVersionMismatch, publicErrorCode, SCOPE_BINDING_TARGET_MISSING_RECOVERY } from './diagnostics.ts'
import {
  authenticationRejection,
  bodyFailureDetails,
  type BodyFailureDetails,
  InvalidResponseError,
  ResponseReadError,
  SecretRejectedError,
  ServerResponseError,
  TransportError,
  UnknownOperationError,
} from './errors.ts'
import { OPERATIONS, type OperationId } from './operations.generated.ts'
import { MemoryOperationError, requestMemoryOperation } from './memory-operations.ts'
import { containsSecret } from './secrets.ts'

export interface ToolResult extends BodyFailureDetails {
  ok: boolean
  code?: string
  error_code?: string
  message?: string
  status?: number
  request_id?: string
  etag?: string
  data?: unknown
  details?: Record<string, unknown>
}

const WRITE_OPS = new Set<OperationId>([
  'remember_memory',
  'capture_content_source',
  'revise_memory_entry',
  'replace_artifact',
  'change_atomic_memory_lifecycle',
])

export function toolResultSchema(): Record<string, unknown> {
  return {
    type: 'object',
    additionalProperties: true,
    properties: {
      ok: { type: 'boolean', required: true },
      code: { type: 'string' },
      error_code: { type: 'string' },
      message: { type: 'string' },
      status: { type: 'number' },
      request_id: { type: 'string' },
      etag: { type: 'string' },
      failure_phase: { type: 'string' },
      response_body_error: { type: 'string' },
      data: { type: 'object', additionalProperties: true },
      details: { type: 'object', additionalProperties: true },
    },
  }
}

export function renderToolResult(_args: unknown, value: ToolResult): Array<{ type: 'text'; text: string }> {
  return [{ type: 'text', text: JSON.stringify(value) }]
}

function requestIdField(requestId: string | undefined): { request_id?: string } {
  // DSH validates tool outputs as lossless JSON, including optional fields.
  return requestId === undefined ? {} : { request_id: requestId }
}

/**
 * Maps a Server response error onto the tool result the host sees.
 *
 * Every status `src/powercontext/server/app.py` maps a domain error to gets a branch of
 * its own, because the tail message ("PowerContext is unavailable, continue the task.")
 * is only true for an availability outcome. Reaching the tail with a domain error tells
 * the model to abandon an operation that a retry would have completed, and records an
 * outage that never happened. 5xx deliberately falls through: those are availability
 * outcomes, not domain outcomes.
 */
function mapServerErrorCore(error: ServerResponseError): ToolResult {
  const code = publicErrorCode(error.code)
  if (error.statusCode === 400) {
    // The Server's 400 is not only a malformed request: `invalid_cursor` (RFC 1502) is the
    // malformed or mismatched half of the cursor pair whose expired half is the 410 below.
    // Naming the code is not enough on its own — "fix the request shape" and "restart the
    // listing from the beginning" are different instructions, and only the second applies.
    return { ok: false, code: code ?? 'invalid_request', message: code === 'invalid_cursor' ? 'PowerContext rejected a pagination cursor that is invalid or does not match this request. Restart the listing from the beginning.' : 'PowerContext rejected the request as malformed.', status: 400, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 401) {
    return { ok: false, code: 'authentication_failed', message: 'PowerContext authentication failed. Check Authorization.', status: 401, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 403) {
    return { ok: false, code: 'authorization_failed', message: 'PowerContext authorization failed. Check the principal and Scope permissions.', status: 403, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 404) {
    if (isVersionMismatch(error)) {
      return { ok: false, code: 'version_mismatch', message: 'A required PowerContext endpoint is unavailable. Check the Server endpoint and compatible plugin/Server versions.', status: 404, ...requestIdField(error.requestId) }
    }
    return { ok: false, code: 'not_found', ...(code ? { error_code: code } : {}), message: code === 'scope_not_found' ? 'PowerContext could not resolve the requested Scope. Check its configuration.' : 'PowerContext resource was not found.', status: 404, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 409) {
    if (code === 'scope_binding_target_missing') {
      return { ok: false, code, message: SCOPE_BINDING_TARGET_MISSING_RECOVERY, status: 409, ...requestIdField(error.requestId) }
    }
    return { ok: false, code: code ?? 'conflict', message: 'PowerContext operation conflicts with the current state. Inspect the current reference before retrying.', status: 409, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 410) {
    return { ok: false, code: code ?? 'cursor_expired', message: 'PowerContext rejected an expired pagination cursor. Restart the listing from the beginning.', status: 410, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 412) {
    // Both codes the Server returns at 412 mean the same thing to the caller: the state
    // this request was built against has moved, so re-read it and retry. Neither is an
    // outage, and `revision_conflict` arrives here even though it is already published.
    // The fallback is the contract's own `precondition_failed` (docs/en/rfcs/1437_source_artifact_rest_api.md).
    return { ok: false, code: code ?? 'precondition_failed', message: 'PowerContext rejected the request because a precondition no longer matches the current state. Re-read the current revision or tag, then retry with the fresh value.', status: 412, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 413) {
    // The Server's only 413 is the Handoff Report size limit, and that code is published,
    // so the fallback names the same condition instead of inventing a code no Server sends.
    return { ok: false, code: code ?? 'handoff_report_too_large', message: 'PowerContext rejected the request because the result exceeds the response limit. Narrow the selection and retry.', status: 413, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 422) {
    return { ok: false, code: code ?? 'invalid_request', message: 'PowerContext rejected the request.', status: 422, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 428) {
    return { ok: false, code: code ?? 'precondition_required', message: 'PowerContext requires the current ETag in If-Match for this mutation. Read the resource, then retry with its ETag.', status: 428, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 429) {
    return { ok: false, code: code ?? 'capacity_exceeded', message: 'PowerContext reached a capacity limit. Retry after a short delay.', status: 429, ...requestIdField(error.requestId) }
  }
  if (error.statusCode === 503) {
    return { ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.', status: 503, ...requestIdField(error.requestId) }
  }
  return {
    ok: false,
    code: code ?? 'server_error',
    message: 'PowerContext is unavailable, continue the task.',
    status: error.statusCode,
    ...requestIdField(error.requestId),
  }
}

/**
 * Recovery fields the contract documents for a published code.
 *
 * `docs/en/development/plugin-contract.md` requires the direct surfaces to return a
 * generic failure result without exposing request details, so a response body may only
 * cross that boundary where a published code says which fields the caller can act on.
 * A code the plugin cannot name is mapped onto a generic one, and a generic one has no
 * recovery fields: the body is not a model-facing channel.
 */
const RECOVERY_DETAIL_FIELDS: Record<string, readonly string[]> = {
  memory_capacity_exceeded: ['dimension', 'limit', 'observed'],
}

function recoveryDetails(
  code: string | undefined,
  details: Record<string, unknown> | undefined,
): { details?: Record<string, unknown> } {
  if (code === undefined || details === undefined) return {}
  const allowed = RECOVERY_DETAIL_FIELDS[code]
  if (allowed === undefined) return {}
  const kept = Object.fromEntries(
    allowed.filter(field => details[field] !== undefined).map(field => [field, details[field]]),
  )
  return Object.keys(kept).length === 0 ? {} : { details: kept }
}

function mapServerError(error: ServerResponseError): ToolResult {
  const mapped = mapServerErrorCore(error)
  return { ...mapped, ...recoveryDetails(mapped.code, error.serverDetails) }
}

export function toToolResult(error: unknown): ToolResult {
  if (error instanceof MemoryOperationError) return { ok: false, code: error.code, message: error.message }
  if (error instanceof SecretRejectedError) {
    return { ok: false, code: 'secret_rejected', message: error.message }
  }
  if (error instanceof UnknownOperationError) {
    return { ok: false, code: 'unknown_operation', message: error.message }
  }
  if (error instanceof ServerResponseError) return mapServerError(error)
  const rejection = authenticationRejection(error)
  if (rejection) return { ...mapServerError(rejection), ...bodyFailureDetails(error) }
  if (error instanceof ResponseReadError) return {
    ok: false, code: 'unavailable', message: 'PowerContext response-body reading failed; the operation result was not validated.',
    status: error.statusCode, ...requestIdField(error.requestId), ...bodyFailureDetails(error),
  }
  if (error instanceof InvalidResponseError) {
    return { ok: false, code: 'invalid_response', message: 'PowerContext returned an invalid response.', ...requestIdField(error.requestId), ...bodyFailureDetails(error) }
  }
  if (error instanceof TransportError) {
    return { ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.' }
  }
  return { ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.' }
}

export function injectScope(
  operationId: OperationId,
  payload: JsonObject | undefined,
  scopeId: string,
): JsonObject | undefined {
  const mode = OPERATIONS[operationId].scopeMode
  if (mode === 'selection') {
    return { ...payload, selection: { mode: 'exact', scope_ids: [scopeId] } }
  }
  return mode === 'current' || operationId === 'get_atomic_memory_state' ? { ...payload, scope_id: scopeId } : payload
}

function encodeSuccess(result: Awaited<ReturnType<PowerContextClient['request']>>): ToolResult {
  if (result.kind === 'bytes') {
    return { ok: true, status: result.status, ...requestIdField(result.requestId), data: { bytes_base64: Buffer.from(result.value).toString('base64') } }
  }
  if (result.kind === 'text') {
    return { ok: true, status: result.status, ...requestIdField(result.requestId), data: { markdown: result.value } }
  }
  return { ok: true, status: result.status, ...requestIdField(result.requestId),
    ...(result.etag === undefined ? {} : { etag: result.etag }), data: result.value }
}

function hasSecret(value: unknown): boolean {
  if (typeof value === 'string') return containsSecret(value)
  if (Array.isArray(value)) return value.some(hasSecret)
  return Boolean(value && typeof value === 'object' && Object.values(value).some(hasSecret))
}

export async function invokeOperation(
  client: PowerContextClient,
  operationId: string,
  payload: JsonObject | undefined,
  scopeId: string,
  signal?: AbortSignal,
  onFailure?: (error: unknown) => unknown,
): Promise<ToolResult> {
  if (!(operationId in OPERATIONS)) return toToolResult(new UnknownOperationError(operationId))
  const id = operationId as OperationId
  const body = injectScope(id, payload, scopeId)
  if (WRITE_OPS.has(id) && hasSecret(body)) {
    return toToolResult(new SecretRejectedError())
  }
  try {
    if (signal?.aborted) throw new TransportError('', signal.reason)
    const memory = await requestMemoryOperation(client, id, body, scopeId, signal)
    return encodeSuccess(memory ?? await client.request(id, body, signal))
  } catch (error) {
    try {
      await onFailure?.(error)
    } catch {
      // Reporting must not turn an operation failure into a host exception.
    }
    return toToolResult(error)
  }
}

export async function reportDirectFailure(runtime: PluginRuntime, event: string, error: unknown): Promise<ToolResult> {
  try {
    const diagnostic = failureEvent(event, error)
    if (diagnostic) await runtime.log(diagnostic)
  } catch {
    // Diagnostics are best effort, including failures before operation dispatch.
  }
  return toToolResult(error)
}

export interface PluginRuntime {
  status?: import('./status.ts').RuntimeStatus
  client: PowerContextClient
  config: ResolvedConfig
  resolveScope: (cwd?: string, signal?: AbortSignal) => Promise<string | undefined>
  log: (event: Record<string, unknown>) => void
}
