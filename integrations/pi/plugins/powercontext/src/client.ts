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
  if (signals.length === 1) return signals[0]
  if (typeof AbortSignal.any === 'function') return AbortSignal.any([...signals])
  const controller = new AbortController()
  for (const signal of signals) {
    if (signal.aborted) {
      controller.abort(signal.reason)
      break
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  return controller.signal
}

export function createTimeoutSignal(timeoutMs: number): AbortSignal {
  if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(timeoutMs)
  const controller = new AbortController()
  const timeout = setTimeout(() => controller.abort(), timeoutMs)
  timeout.unref()
  return controller.signal
}

function concatBytes(chunks: readonly Uint8Array[], total: number): Uint8Array {
  const output = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    output.set(chunk, offset)
    offset += chunk.byteLength
  }
  return output
}

function responsePath(response: Response): string {
  try {
    return response.url ? new URL(response.url).pathname : '/'
  } catch {
    return '/'
  }
}

export async function readLimitedBody(response: Response, maxBytes = MAX_RESPONSE_BYTES): Promise<Uint8Array> {
  const contentLength = response.headers.get('content-length')
  if (contentLength && Number(contentLength) > maxBytes) throw new InvalidResponseError(responsePath(response))
  if (!response.body) {
    const buffer = new Uint8Array(await response.arrayBuffer())
    if (buffer.byteLength > maxBytes) throw new InvalidResponseError(responsePath(response))
    return buffer
  }
  const reader = response.body.getReader()
  const chunks: Uint8Array[] = []
  let total = 0
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    total += value.byteLength
    if (total > maxBytes) {
      await reader.cancel()
      throw new InvalidResponseError(responsePath(response))
    }
    chunks.push(value)
  }
  return concatBytes(chunks, total)
}

function decodeError(bytes: Uint8Array): { code?: string; message?: string } {
  try {
    const parsed = JSON.parse(Buffer.from(bytes).toString('utf8')) as {
      error?: { code?: string; message?: string }
    }
    return { code: parsed.error?.code, message: parsed.error?.message }
  } catch {
    return {}
  }
}

function isRedirect(status: number): boolean {
  return status >= 300 && status < 400
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
  private readonly baseUrl: string
  private readonly authorization: string | undefined
  private readonly requestTimeoutMs: number
  private readonly fetchImpl: FetchFn

  constructor(options: ClientOptions) {
    this.baseUrl = options.baseUrl.replace(/\/+$/, '')
    this.authorization = options.authorization
    this.requestTimeoutMs = options.requestTimeoutMs
    this.fetchImpl = options.fetch ?? fetch
  }

  async request(id: string, payload?: JsonObject, signal?: AbortSignal): Promise<ClientSuccess> {
    if (!(id in OPERATIONS)) throw new UnknownOperationError(id)
    const spec = OPERATIONS[id as OperationId]
    const bound = bindOperationPath(spec, payload)
    const url = this.buildUrl(bound.path)
    try {
      const response = await this.fetchImpl(url, this.buildInit(spec, bound.payload, signal))
      return await this.parseResponse(spec, response)
    } catch (error) {
      if (
        error instanceof ServerResponseError
        || error instanceof InvalidResponseError
        || error instanceof UnknownOperationError
      ) {
        throw error
      }
      throw new UnavailableError(bound.path, error)
    }
  }

  private buildUrl(path: string): string {
    return `${this.baseUrl}${path}`
  }

  private buildInit(spec: OperationSpec, payload: JsonObject | undefined, signal?: AbortSignal): RequestInit {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      'User-Agent': PLUGIN_USER_AGENT,
    }
    if (this.authorization) headers.Authorization = this.authorization
    const signals = [createTimeoutSignal(this.requestTimeoutMs)]
    if (signal) signals.push(signal)
    const init: RequestInit = {
      method: spec.method,
      headers,
      redirect: 'manual',
      signal: combineSignals(signals),
    }
    if (spec.location === 'body') {
      headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(payload ?? {})
    }
    return init
  }

  private async parseResponse(
    spec: OperationSpec,
    response: Response,
  ): Promise<ClientSuccess> {
    if (isRedirect(response.status)) throw new InvalidResponseError(spec.path)
    const bytes = await readLimitedBody(response)
    const requestId = response.headers.get(REQUEST_ID_HEADER) ?? undefined
    if (response.status < 200 || response.status >= 300) {
      throw this.httpError(response.status, spec.path, requestId, bytes)
    }
    try {
      return { kind: 'json', value: JSON.parse(Buffer.from(bytes).toString('utf8')), status: response.status, requestId }
    } catch {
      throw new InvalidResponseError(spec.path, requestId)
    }
  }

  private httpError(
    status: number,
    path: string,
    requestId: string | undefined,
    bytes: Uint8Array,
  ): ServerResponseError {
    const decoded = decodeError(bytes)
    return new ServerResponseError({
      statusCode: status,
      path,
      requestId,
      code: decoded.code,
      message: decoded.message,
    })
  }
}
