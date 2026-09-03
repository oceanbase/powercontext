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

import {
  InvalidResponseError,
  MAX_RESPONSE_BYTES,
  PLUGIN_USER_AGENT,
  REQUEST_ID_HEADER,
  ServerResponseError,
  UnavailableError,
  UnknownOperationError,
} from './errors.ts'
import { OPERATIONS, type OperationId, type OperationSpec } from './operations.generated.ts'

export type JsonObject = Record<string, unknown>
export type FetchFn = (input: string, init: RequestInit) => Promise<Response>
export type ClientSuccess = { kind: 'json'; value: unknown; status: number; requestId: string | undefined }

export interface ClientOptions {
  baseUrl: string
  authorization?: string
  requestTimeoutMs: number
  fetch?: FetchFn
}

export function combineSignals(signals: readonly AbortSignal[]): AbortSignal {
  if (signals.length === 1) return signals[0]!
  if (typeof AbortSignal.any === 'function') return AbortSignal.any([...signals])
  const controller = new AbortController()
  for (const signal of signals) {
    if (signal.aborted) controller.abort(signal.reason)
    else signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  return controller.signal
}

export function createTimeoutSignal(timeoutMs: number): AbortSignal {
  if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(timeoutMs)
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)
  timer.unref()
  return controller.signal
}

async function readLimitedBody(response: Response): Promise<Uint8Array> {
  const declared = response.headers.get('content-length')
  const parsedLength = declared === null ? undefined : Number(declared)
  const declaredBytes = parsedLength !== undefined && Number.isFinite(parsedLength) && parsedLength >= 0
    ? parsedLength
    : undefined
  if (declaredBytes !== undefined && declaredBytes > MAX_RESPONSE_BYTES) {
    try {
      await response.body?.cancel()
    } catch {}
    throw new InvalidResponseError('/')
  }
  if (!response.body) return new Uint8Array()

  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let length = 0
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      if (!value?.byteLength) continue
      if (length + value.byteLength > MAX_RESPONSE_BYTES) {
        try {
          await reader.cancel()
        } catch {}
        throw new InvalidResponseError('/')
      }
      chunks.push(value)
      length += value.byteLength
      if (declaredBytes === undefined && length === MAX_RESPONSE_BYTES) {
        // Unknown-length streams can prefetch the next chunk before cancellation.
        // Stop at the boundary instead of risking an allocation beyond the limit.
        try {
          await reader.cancel()
        } catch {}
        throw new InvalidResponseError('/')
      }
    }
  } finally {
    reader.releaseLock()
  }

  const body = new Uint8Array(length)
  let offset = 0
  for (const chunk of chunks) {
    body.set(chunk, offset)
    offset += chunk.byteLength
  }
  return body
}

function bindOperationPath(
  spec: OperationSpec,
  payload: JsonObject | undefined,
): { path: string; payload: JsonObject | undefined } {
  const pathParameters: readonly string[] = spec.pathParameters
  if (pathParameters.length === 0) return { path: spec.path, payload }

  const transportPayload = { ...payload }
  let path: string = spec.path
  for (const name of pathParameters) {
    const value = transportPayload[name]
    if (typeof value !== 'string' || value.trim().length === 0) {
      throw new TypeError(`operation requires string path parameter ${name}`)
    }
    path = path.replace(`{${name}}`, encodeURIComponent(value))
    delete transportPayload[name]
  }
  return { path, payload: transportPayload }
}

export class PowerContextClient {
  private readonly fetchImpl: FetchFn

  constructor(private readonly options: ClientOptions) {
    this.fetchImpl = options.fetch ?? fetch
  }

  async request(id: string, payload?: JsonObject, signal?: AbortSignal): Promise<ClientSuccess> {
    if (!(id in OPERATIONS)) throw new UnknownOperationError(id)
    const spec = OPERATIONS[id as OperationId]
    const bound = bindOperationPath(spec, payload)
    try {
      const response = await this.fetchImpl(this.url(bound.path), this.init(spec, bound.payload, signal))
      if (response.status >= 300 && response.status < 400) throw new InvalidResponseError(spec.path)
      const bytes = await readLimitedBody(response)
      const requestId = response.headers.get(REQUEST_ID_HEADER) ?? undefined
      if (!response.ok) {
        let error: { error?: { code?: string; message?: string } } = {}
        try {
          error = JSON.parse(Buffer.from(bytes).toString('utf8'))
        } catch {}
        throw new ServerResponseError({
          statusCode: response.status,
          requestId,
          code: error.error?.code,
          message: error.error?.message,
        })
      }
      try {
        return { kind: 'json', value: JSON.parse(Buffer.from(bytes).toString('utf8')), status: response.status, requestId }
      } catch {
        throw new InvalidResponseError(spec.path, requestId)
      }
    } catch (error) {
      if (error instanceof ServerResponseError || error instanceof InvalidResponseError || error instanceof UnknownOperationError) {
        throw error
      }
      throw new UnavailableError(bound.path, error)
    }
  }

  private url(path: string): string {
    return `${this.options.baseUrl.replace(/\/+$/, '')}${path}`
  }

  private init(spec: OperationSpec, payload: JsonObject | undefined, signal?: AbortSignal): RequestInit {
    const headers: Record<string, string> = { Accept: 'application/json', 'User-Agent': PLUGIN_USER_AGENT }
    if (this.options.authorization) headers.Authorization = this.options.authorization
    const signals = [createTimeoutSignal(this.options.requestTimeoutMs)]
    if (signal) signals.push(signal)
    const init: RequestInit = { method: spec.method, headers, redirect: 'manual', signal: combineSignals(signals) }
    if (spec.location === 'body') {
      headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(payload ?? {})
    }
    return init
  }
}
