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

import type { JsonObject, PowerContextClient } from './client.ts'
import { ServerResponseError, UnknownOperationError } from './errors.ts'
import { OPERATIONS, type OperationId } from './operations.generated.ts'
import { containsSecret } from './secrets.ts'

export interface ToolResult {
  ok: boolean
  code?: string
  message?: string
  status?: number
  request_id?: string
  data?: unknown
}

const WRITE_OPERATIONS = new Set<OperationId>([
  'remember_memory',
  'capture_content_source',
  'revise_memory_entry',
  'retire_memory_entry',
  'activate_handoff',
  'commit_handoff',
  'generate_experience',
  'generate_skill',
])

export function operationMutates(id: OperationId): boolean {
  return WRITE_OPERATIONS.has(id)
}

function hasSecret(value: unknown): boolean {
  if (typeof value === 'string') return containsSecret(value)
  if (Array.isArray(value)) return value.some(hasSecret)
  return Boolean(value && typeof value === 'object' && Object.values(value).some(hasSecret))
}

function errorResult(error: unknown): ToolResult {
  if (error instanceof ServerResponseError) {
    if (error.statusCode === 401) {
      return { ok: false, code: 'authentication_failed', message: 'PowerContext authentication failed.', status: 401 }
    }
    if (error.statusCode === 409) {
      return {
        ok: false,
        code: error.code ?? 'conflict',
        message: error.serverMessage ?? 'Citation conflict; refresh and retry once.',
        status: 409,
        request_id: error.requestId,
      }
    }
    return {
      ok: false,
      code: error.code ?? (error.statusCode === 404 ? 'not_found' : 'invalid_request'),
      message: error.serverMessage ?? `PowerContext returned HTTP ${error.statusCode}.`,
      status: error.statusCode,
      request_id: error.requestId,
    }
  }
  if (error instanceof UnknownOperationError) return { ok: false, code: 'unknown_operation', message: error.message }
  return { ok: false, code: 'unavailable', message: 'PowerContext is unavailable; continue the task.' }
}

export async function invokeOperation(
  client: PowerContextClient,
  operationId: OperationId,
  payload: JsonObject,
  scopeId: string,
  signal?: AbortSignal,
): Promise<ToolResult> {
  const mode = OPERATIONS[operationId].scopeMode
  const body = mode === 'selection'
    ? { ...payload, selection: { mode: 'exact', scope_ids: [scopeId] } }
    : mode === 'current'
      ? { ...payload, scope_id: scopeId }
      : payload
  if (operationMutates(operationId) && hasSecret(body)) {
    return { ok: false, code: 'secret_rejected', message: 'Refused to send secret-like content to PowerContext.' }
  }
  try {
    const result = await client.request(operationId, body, signal)
    return { ok: true, status: result.status, request_id: result.requestId, data: result.value }
  } catch (error) {
    return errorResult(error)
  }
}
