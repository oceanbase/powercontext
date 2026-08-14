import type { PowerContextClient, JsonObject } from './client.ts'
import type { ResolvedConfig } from './config.ts'
import {
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
  if (error.statusCode === 401) {
    return { ok: false, code: 'authentication_failed', message: 'PowerContext authentication failed. Check Authorization.', status: 401, request_id: error.requestId }
  }
  if (error.statusCode === 404) {
    return { ok: false, code: 'not_found', message: error.serverMessage ?? 'PowerContext resource was not found.', status: 404, request_id: error.requestId }
  }
  if (error.statusCode === 409) {
    return { ok: false, code: error.code ?? 'conflict', message: error.serverMessage ?? 'citation conflict; refresh and retry once.', status: 409, request_id: error.requestId }
  }
  if (error.statusCode === 422) {
    return { ok: false, code: error.code ?? 'invalid_request', message: error.serverMessage ?? 'PowerContext rejected the request.', status: 422, request_id: error.requestId }
  }
  if (error.statusCode === 503) {
    return { ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.', status: 503, request_id: error.requestId }
  }
  return {
    ok: false,
    code: error.code ?? 'server_error',
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
  if (!OPERATIONS[operationId].scope) return payload
  return { ...payload, scope_id: scopeId }
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
    return encodeSuccess(await client.request(id, body, signal))
  } catch (error) {
    return toToolResult(error)
  }
}

export interface PluginRuntime {
  client: PowerContextClient
  config: ResolvedConfig
  resolveScope: (cwd: string) => Promise<string>
  log: (event: Record<string, unknown>) => void
}
