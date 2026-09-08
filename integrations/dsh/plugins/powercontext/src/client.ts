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
  TransportError,
  UnavailableError,
  UnknownOperationError,
} from './errors.ts'
import { OPERATIONS, type OperationId, type OperationSpec } from './operations.generated.ts'

export type JsonObject = Record<string, unknown>
export type FetchFn = (input: string, init: RequestInit) => Promise<Response>

export type ClientSuccess =
  | { kind: 'json'; value: unknown; status: number; requestId: string | undefined; etag?: string }
  | { kind: 'text'; value: string; status: number; requestId: string | undefined; etag?: string }
  | { kind: 'bytes'; value: Uint8Array; status: number; requestId: string | undefined; etag?: string }

export interface ClientOptions {
  baseUrl: string
  authorization?: string
  requestTimeoutMs: number
  fetch?: FetchFn
}

export function combineSignals(signals: AbortSignal[]): AbortSignal {
  const present = signals.filter(Boolean)
  if (typeof AbortSignal.any === 'function') return AbortSignal.any(present)
  const controller = new AbortController()
  for (const signal of present) {
    if (signal.aborted) {
      controller.abort(signal.reason)
      break
    }
    signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  return controller.signal
}

function timeoutSignal(ms: number): AbortSignal {
  if (typeof AbortSignal.timeout === 'function') return AbortSignal.timeout(ms)
  const controller = new AbortController()
  setTimeout(() => controller.abort(), ms)
  return controller.signal
}

function concatBytes(chunks: Uint8Array[], total: number): Uint8Array {
  const out = new Uint8Array(total)
  let offset = 0
  for (const chunk of chunks) {
    out.set(chunk, offset)
    offset += chunk.byteLength
  }
  return out
}

function responsePath(response: Response): string {
  try {
    return response.url ? new URL(response.url).pathname : '/'
  } catch {
    return '/'
  }
}

export async function readLimitedBody(response: Response, maxBytes = MAX_RESPONSE_BYTES): Promise<Uint8Array> {
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

function queryString(payload: JsonObject | undefined): string {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(payload ?? {})) {
    if (value === undefined || value === null) continue
    for (const item of Array.isArray(value) ? value : [value]) params.append(key, String(item))
  }
  const encoded = params.toString()
  return encoded ? `?${encoded}` : ''
}

interface PreparedRequest {
  path: string
  query: string
  headers: Record<string, string>
  body: JsonObject | undefined
}

function encodePathSegment(value: unknown): string {
  return encodeURIComponent(String(value)).replace(/[!'()*]/g, (character) => (
    `%${character.charCodeAt(0).toString(16).toUpperCase()}`
  ))
}

function headerPayloadKey(name: string): string {
  return name.toLowerCase().replaceAll('-', '_')
}

function prepareRequest(spec: OperationSpec, payload: JsonObject | undefined): PreparedRequest {
  const remaining = { ...(payload ?? {}) }
  let path = spec.path as string
  for (const name of spec.pathParameters as readonly string[]) {
    const value = remaining[name]
    if (value === undefined || value === null) {
      throw new TypeError(`${spec.method} ${spec.path} requires ${name}`)
    }
    path = path.replace(`{${name}}`, encodePathSegment(value))
    delete remaining[name]
  }

  const headers: Record<string, string> = {}
  for (const name of spec.headerParams as readonly string[]) {
    const alias = headerPayloadKey(name)
    const value = remaining[name] ?? remaining[alias]
    delete remaining[name]
    delete remaining[alias]
    if (value !== undefined && value !== null) headers[name] = String(value)
  }

  const queryPayload: JsonObject = {}
  for (const name of spec.queryParams as readonly string[]) {
    const value = remaining[name]
    delete remaining[name]
    if (value !== undefined && value !== null) queryPayload[name] = value
  }
  return {
    path,
    query: queryString(queryPayload),
    headers,
    body: spec.location === 'body' ? remaining : undefined,
  }
}

function hasStatus(statuses: readonly number[], status: number): boolean {
  return statuses.includes(status)
}

function isRedirect(status: number): boolean {
  return status >= 300 && status < 400
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

  async request(
    id: string,
    payload?: JsonObject,
    signal?: AbortSignal,
  ): Promise<ClientSuccess> {
    if (!(id in OPERATIONS)) throw new UnknownOperationError(id)
    const spec = OPERATIONS[id as OperationId]
    const prepared = prepareRequest(spec, payload)
    const url = `${this.baseUrl}${prepared.path}${prepared.query}`
    try {
      const response = await this.fetchImpl(url, this.buildInit(spec, prepared, signal))
      return await this.parseResponse(id, spec, payload, response)
    } catch (error) {
      if (error instanceof ServerResponseError || error instanceof InvalidResponseError) throw error
      if (error instanceof UnknownOperationError) throw error
      throw this.wrapTransport(prepared.path, error)
    }
  }

  private buildInit(spec: OperationSpec, request: PreparedRequest, signal?: AbortSignal): RequestInit {
    const headers: Record<string, string> = {
      Accept: 'application/json',
      'User-Agent': PLUGIN_USER_AGENT,
      ...request.headers,
    }
    if (this.authorization) headers.Authorization = this.authorization
    const init: RequestInit = {
      method: spec.method,
      headers,
      redirect: 'manual',
      signal: combineSignals([timeoutSignal(this.requestTimeoutMs), ...signal ? [signal] : []]),
    }
    if (spec.location === 'body') {
      headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(request.body ?? {})
    }
    return init
  }

  private wrapTransport(path: string, error: unknown): TransportError {
    if (error instanceof Error && error.name === 'TimeoutError') return new UnavailableError(path, error)
    if (error instanceof DOMException && error.name === 'AbortError') return new UnavailableError(path, error)
    return new UnavailableError(path, error)
  }

  private async parseResponse(
    id: string,
    spec: OperationSpec,
    payload: JsonObject | undefined,
    response: Response,
  ): Promise<ClientSuccess> {
    const success = (response.status >= 200 && response.status < 300)
      || hasStatus(spec.successStatuses as readonly number[], response.status)
    if (isRedirect(response.status) && !success) throw new InvalidResponseError(spec.path)
    const bytes = await readLimitedBody(response)
    const requestId = response.headers.get(REQUEST_ID_HEADER) ?? undefined
    if (!success) {
      throw this.httpError(response.status, spec.path, requestId, bytes)
    }
    if (hasStatus(spec.emptyStatuses as readonly number[], response.status)) {
      if (bytes.byteLength !== 0) throw new InvalidResponseError(spec.path, requestId)
      return { kind: 'json', value: null, status: response.status, requestId, etag: response.headers.get('ETag') ?? undefined }
    }
    if (id === 'get_handoff_report' && payload?.download === true) {
      return { kind: 'bytes', value: bytes, status: response.status, requestId }
    }
    if (id === 'get_handoff_report' && payload?.format !== 'json') {
      return { kind: 'text', value: Buffer.from(bytes).toString('utf8'), status: response.status, requestId }
    }
    try {
      return { kind: 'json', value: JSON.parse(Buffer.from(bytes).toString('utf8')), status: response.status, requestId, etag: response.headers.get('ETag') ?? undefined }
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
