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

const MAX_RESPONSE_BYTES = 1_048_576
export const REQUEST_TIMEOUT_MS = 1_000
export const HOOK_BUDGET_MS = 4_000

export function serverOrigin(settings) {
  let endpoint
  try {
    endpoint = new URL(settings.serverUrl)
  } catch {
    throw new Error('invalid_server_url')
  }
  const loopback = ['127.0.0.1', '[::1]', 'localhost'].includes(endpoint.hostname)
  if (!(endpoint.protocol === 'https:' || endpoint.protocol === 'http:' && (loopback || settings.allowInsecureHttp)) ||
      endpoint.username || endpoint.password || endpoint.search || endpoint.hash || endpoint.pathname !== '/') {
    throw new Error('invalid_server_url')
  }
  return endpoint.origin
}

async function readJson(response, signal) {
  if (!response.body) throw new Error('missing_body')
  const reader = response.body.getReader()
  const chunks = []
  let size = 0
  try {
    while (true) {
      if (signal.aborted) throw new Error('timeout')
      const { done, value } = await reader.read()
      if (done) break
      size += value.byteLength
      if (size > MAX_RESPONSE_BYTES) throw new Error('response_too_large')
      chunks.push(value)
    }
  } finally {
    await reader.cancel().catch(() => {})
  }
  const payload = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)))
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) throw new Error('invalid_json')
  return payload
}

export async function request(settings, method, path, body, budgetSignal) {
  const origin = serverOrigin(settings)
  const signal = AbortSignal.any([budgetSignal, AbortSignal.timeout(REQUEST_TIMEOUT_MS)])
  signal.throwIfAborted()
  let status
  try {
    const response = await fetch(`${origin}${path}`, {
      method, redirect: 'error', signal,
      headers: {
        Accept: 'application/json', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }),
        ...(settings.authorization ? { Authorization: settings.authorization } : {}),
      },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
    status = response.status
    if (!response.ok) throw new Error(`http_${status}`)
    return { status, body: await readJson(response, signal) }
  } catch (error) {
    // After fetch starts, delivery cannot be disproved by a transport error.
    error.requestSent = true
    if (status !== undefined) error.httpStatus = status
    throw error
  }
}

export function failureCode(error) {
  const message = error instanceof Error ? error.message : ''
  const localCodes = ['invalid_server_url', 'invalid_input', 'session_conflict', 'invalid_session', 'remote_binding_disabled',
    'scope_unresolved', 'invalid_arguments', 'binding_not_confirmed']
  if (localCodes.includes(message)) return message
  if (message === 'unscoped') return 'scope_unresolved'
  if (['invalid_prepared', 'invalid_receipt', 'invalid_flush', 'invalid_status', 'missing_body', 'response_too_large', 'invalid_json'].includes(message) ||
      error instanceof SyntaxError || error instanceof TypeError && message !== 'fetch failed') return 'invalid_response'
  const httpCodes = { http_401: 'unauthorized', http_403: 'forbidden', http_404: 'not_found', http_409: 'conflict', http_422: 'invalid_request', http_503: 'server_unavailable' }
  if (httpCodes[message]) return httpCodes[message]
  if (message === 'timeout' || error?.name === 'TimeoutError' || error?.name === 'AbortError') return 'timeout'
  return 'server_unavailable'
}
