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
import { failureEvent, isVersionMismatch, publicErrorCode } from './diagnostics.ts'
import {
  InvalidResponseError,
  SecretRejectedError,
  ServerResponseError,
  TransportError,
  UnknownOperationError,
} from './errors.ts'
import { OPERATIONS, type OperationId } from './operations.generated.ts'
import { containsSecret } from './secrets.ts'

export interface ToolResult {
  ok: boolean
  code?: string
  error_code?: string
  message?: string
  status?: number
  request_id?: string
  data?: unknown
}

const WRITE_OPS = new Set<OperationId>([
  'remember_memory',
  'capture_content_source',
  'revise_memory_entry',
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
      data: { type: 'object', additionalProperties: true },
    },
  }
}

export function renderToolResult(_args: unknown, value: ToolResult): Array<{ type: 'text'; text: string }> {
  return [{ type: 'text', text: JSON.stringify(value) }]
}

function mapServerError(error: ServerResponseError): ToolResult {
  const code = publicErrorCode(error.code)
  if (error.statusCode === 401) {
    return { ok: false, code: 'authentication_failed', message: 'PowerContext authentication failed. Check Authorization.', status: 401, request_id: error.requestId }
  }
  if (error.statusCode === 404) {
    if (isVersionMismatch(error)) {
      return { ok: false, code: 'version_mismatch', message: 'A required PowerContext endpoint is unavailable. Check the Server endpoint and compatible plugin/Server versions.', status: 404, request_id: error.requestId }
    }
    return { ok: false, code: 'not_found', ...(code ? { error_code: code } : {}), message: code === 'scope_not_found' ? 'PowerContext could not resolve the requested Scope. Check its configuration.' : 'PowerContext resource was not found.', status: 404, request_id: error.requestId }
  }
  if (error.statusCode === 409) {
    return { ok: false, code: code ?? 'conflict', message: 'PowerContext operation conflicts with the current state. Inspect the current reference before retrying.', status: 409, request_id: error.requestId }
  }
  if (error.statusCode === 422) {
    return { ok: false, code: code ?? 'invalid_request', message: 'PowerContext rejected the request.', status: 422, request_id: error.requestId }
  }
  if (error.statusCode === 503) {
    return { ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.', status: 503, request_id: error.requestId }
  }
  return {
    ok: false,
    code: code ?? 'server_error',
    message: 'PowerContext is unavailable, continue the task.',
    status: error.statusCode,
    request_id: error.requestId,
  }
}

export function toToolResult(error: unknown): ToolResult {
  if (error instanceof SecretRejectedError) {
    return { ok: false, code: 'secret_rejected', message: error.message }
  }
  if (error instanceof UnknownOperationError) {
    return { ok: false, code: 'unknown_operation', message: error.message }
  }
  if (error instanceof ServerResponseError) return mapServerError(error)
  if (error instanceof InvalidResponseError) {
    return { ok: false, code: 'invalid_response', message: 'PowerContext returned an invalid response.', request_id: error.requestId }
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
  return mode === 'current' ? { ...payload, scope_id: scopeId } : payload
}

function encodeSuccess(result: Awaited<ReturnType<PowerContextClient['request']>>): ToolResult {
  if (result.kind === 'bytes') {
    return { ok: true, status: result.status, request_id: result.requestId, data: { bytes_base64: Buffer.from(result.value).toString('base64') } }
  }
  if (result.kind === 'text') {
    return { ok: true, status: result.status, request_id: result.requestId, data: { markdown: result.value } }
  }
  return { ok: true, status: result.status, request_id: result.requestId, data: result.value }
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
  if (WRITE_OPS.has(id) && typeof body?.text === 'string' && containsSecret(body.text)) {
    return toToolResult(new SecretRejectedError())
  }
  if (WRITE_OPS.has(id) && typeof body?.content === 'string' && containsSecret(body.content)) {
    return toToolResult(new SecretRejectedError())
  }
  try {
    if (signal?.aborted) throw new TransportError('', signal.reason)
    return encodeSuccess(await client.request(id, body, signal))
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
  client: PowerContextClient
  config: ResolvedConfig
  resolveScope: (cwd?: string, signal?: AbortSignal) => Promise<string | undefined>
  log: (event: Record<string, unknown>) => void
}
