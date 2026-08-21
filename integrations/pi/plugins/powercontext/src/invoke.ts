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
import {
  SecretRejectedError,
  ServerResponseError,
  UnknownOperationError,
} from './errors.ts'
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

export function unavailableResult(): ToolResult {
  return { ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.' }
}

export interface DurableWriteConfirmationContext {
  hasUI: boolean
  ui: {
    confirm: (title: string, message: string) => Promise<boolean>
  }
}

export interface ScopedOperationRuntime {
  client: PowerContextClient
  resolveScope: (cwd: string) => Promise<string>
}

export async function confirmDurableWrite(
  context: DurableWriteConfirmationContext,
  operation: string,
): Promise<ToolResult | undefined> {
  if (!context.hasUI) {
    return {
      ok: false,
      code: 'confirmation_required',
      message: 'PowerContext refuses explicit durable writes without interactive confirmation.',
    }
  }
  try {
    const approved = await context.ui.confirm(
      'Allow PowerContext change?',
      `The ${operation} changes durable project context. Continue?`,
    )
    return approved ? undefined : { ok: false, code: 'cancelled', message: 'PowerContext change cancelled.' }
  } catch {
    return unavailableResult()
  }
}

const WRITE_OPERATIONS = new Set<OperationId>([
  'remember_memory',
  'capture_content_source',
  'revise_memory_entry',
  'retire_memory_entry',
  'activate_handoff',
  'commit_handoff',
])

function mapServerError(error: ServerResponseError): ToolResult {
  if (error.statusCode === 401) {
    return {
      ok: false,
      code: 'authentication_failed',
      message: 'PowerContext authentication failed. Check Authorization.',
      status: 401,
      request_id: error.requestId,
    }
  }
  if (error.statusCode === 404) {
    return {
      ok: false,
      code: 'not_found',
      message: error.serverMessage ?? 'PowerContext resource was not found.',
      status: 404,
      request_id: error.requestId,
    }
  }
  if (error.statusCode === 409) {
    return {
      ok: false,
      code: error.code ?? 'conflict',
      message: error.serverMessage ?? 'citation conflict; refresh and retry once.',
      status: 409,
      request_id: error.requestId,
    }
  }
  if (error.statusCode === 422) {
    return {
      ok: false,
      code: error.code ?? 'invalid_request',
      message: error.serverMessage ?? 'PowerContext rejected the request.',
      status: 422,
      request_id: error.requestId,
    }
  }
  return {
    ok: false,
    code: error.code ?? 'unavailable',
    message: 'PowerContext is unavailable, continue the task.',
    status: error.statusCode,
    request_id: error.requestId,
  }
}

export function toToolResult(error: unknown): ToolResult {
  if (error instanceof SecretRejectedError) return { ok: false, code: 'secret_rejected', message: error.message }
  if (error instanceof UnknownOperationError) return { ok: false, code: 'unknown_operation', message: error.message }
  if (error instanceof ServerResponseError) return mapServerError(error)
  return unavailableResult()
}

export function injectScope(
  operationId: OperationId,
  payload: JsonObject | undefined,
  scopeId: string,
): JsonObject | undefined {
  if (!OPERATIONS[operationId].scope) return payload
  return { ...payload, scope_id: scopeId }
}

function hasSecret(value: unknown): boolean {
  if (typeof value === 'string') return containsSecret(value)
  if (Array.isArray(value)) return value.some(hasSecret)
  if (value && typeof value === 'object') return Object.values(value).some(hasSecret)
  return false
}

function encodeSuccess(result: Awaited<ReturnType<PowerContextClient['request']>>): ToolResult {
  return { ok: true, status: result.status, request_id: result.requestId, data: result.value }
}

export async function invokeOperation(
  client: PowerContextClient,
  operationId: string,
  payload: JsonObject | undefined,
  scopeId: string,
  signal?: AbortSignal,
): Promise<ToolResult> {
  if (!(operationId in OPERATIONS)) return toToolResult(new UnknownOperationError(operationId))
  const id = operationId as OperationId
  const body = injectScope(id, payload, scopeId)
  if (WRITE_OPERATIONS.has(id) && hasSecret(body)) return toToolResult(new SecretRejectedError())
  try {
    return encodeSuccess(await client.request(id, body, signal))
  } catch (error) {
    return toToolResult(error)
  }
}

export async function invokeScopedOperation(
  runtime: ScopedOperationRuntime,
  context: { cwd: string; signal?: AbortSignal },
  operationId: string,
  payload: JsonObject | undefined,
): Promise<ToolResult> {
  if (!(operationId in OPERATIONS)) return toToolResult(new UnknownOperationError(operationId))
  const id = operationId as OperationId
  try {
    const scopeId = await runtime.resolveScope(context.cwd)
    return invokeOperation(runtime.client, id, payload, scopeId, context.signal)
  } catch {
    return unavailableResult()
  }
}
