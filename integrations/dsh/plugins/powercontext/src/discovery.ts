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

import { combineSignals, createTimeoutSignal, type ClientOptions, type ClientSuccess } from './client.ts'
import {
  InvalidResponseError, MAX_RESPONSE_BYTES, PLUGIN_USER_AGENT, REQUEST_ID_HEADER,
  RequestNotSentError, ResponseReadError, safeRequestId, ServerResponseError, TransportError, UnavailableError,
} from './errors.ts'

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
    if (buffer.byteLength > maxBytes) throw new InvalidResponseError(responsePath(response), undefined, undefined, 'response_too_large')
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
      throw new InvalidResponseError(responsePath(response), undefined, undefined, 'response_too_large')
    }
    chunks.push(value)
  }
  return concatBytes(chunks, total)
}

export async function readOpenApi(options: ClientOptions, signal?: AbortSignal): Promise<ClientSuccess> {
  const path = '/openapi.json'
  const budget = combineSignals([createTimeoutSignal(options.requestTimeoutMs), ...signal ? [signal] : []])
  if (budget.aborted) throw new RequestNotSentError(path, budget.reason)
  let response: Response | undefined
  try {
    response = await fetch(options.baseUrl + path, {
      method: 'GET', redirect: 'manual', signal: budget,
      headers: { Accept: 'application/json', 'User-Agent': PLUGIN_USER_AGENT,
        ...options.authorization ? { Authorization: options.authorization } : {} },
    })
    const requestId = safeRequestId(response.headers.get(REQUEST_ID_HEADER) ?? undefined)
    if (response.status >= 300 && response.status < 400) {
      throw new InvalidResponseError(path, requestId, response.status, 'redirect')
    }
    const bytes = await readLimitedBody(response)
    if (response.status !== 200) throw new ServerResponseError({ statusCode: response.status, path, requestId })
    try {
      return { kind: 'json', value: JSON.parse(Buffer.from(bytes).toString('utf8')), status: 200, requestId }
    } catch { throw new InvalidResponseError(path, requestId, response.status, 'invalid_json') }
  } catch (error) {
    const requestId = safeRequestId(response?.headers.get(REQUEST_ID_HEADER) ?? undefined)
    if (error instanceof InvalidResponseError) throw new InvalidResponseError(path, requestId, response?.status, error.issue)
    if (error instanceof ServerResponseError || error instanceof TransportError) throw error
    const cause = budget.aborted ? budget.reason : error
    if (response) throw new ResponseReadError(path, cause, response.status, requestId)
    throw new UnavailableError(path, cause)
  }
}
