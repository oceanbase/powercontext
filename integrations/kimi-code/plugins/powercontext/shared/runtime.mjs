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

import { readFileSync, realpathSync } from 'node:fs'
import { createHash } from 'node:crypto'
import { isAbsolute, resolve } from 'node:path'
import { isIP } from 'node:net'
import { fileURLToPath } from 'node:url'

export const MAX_CONTEXT_BYTES = 8000
export const TOKEN_ENV = 'POWERCONTEXT_KIMI_TOKEN'

export class Failure extends Error {
  constructor(code, httpStatus, requestSent = false) {
    super(code)
    this.code = code
    this.httpStatus = httpStatus
    this.requestSent = requestSent
  }
}

export function loadSettings({ credentials = true } = {}) {
  let value
  try {
    const bytes = readFileSync(fileURLToPath(new URL('../powercontext.json', import.meta.url)))
    if (bytes.length > 16384) throw new Error()
    value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes))
  } catch { throw new Failure('invalid_settings') }
  let url
  try { url = new URL(value.server_url) } catch { throw new Failure('invalid_settings') }
  // Match the shared transport policy: IPv4 loopback covers the entire 127/8 range.
  const loopback = ['localhost', '[::1]'].includes(url.hostname) ||
    (isIP(url.hostname) === 4 && url.hostname.startsWith('127.'))
  if (!(url.protocol === 'https:' || url.protocol === 'http:' && loopback) ||
      url.username || url.password || url.search || url.hash || url.pathname !== '/') throw new Failure('invalid_settings')
  if (!(value.scope_id === null || isScopeId(value.scope_id)) ||
      typeof value.capture_prompts !== 'boolean' || typeof value.use_token !== 'boolean') throw new Failure('invalid_settings')
  return { serverUrl: url.origin, scopeId: value.scope_id, capturePrompts: value.capture_prompts,
    profile: value.profile ?? 'minimal', useToken: value.use_token,
    authorization: credentials ? authorization(value.use_token) : undefined }
}

export function profileTools(profile) {
  try {
    const value = JSON.parse(readFileSync(fileURLToPath(new URL('../profiles.json', import.meta.url)), 'utf8'))
    const tools = Object.hasOwn(value.profiles, profile) ? value.profiles[profile] : undefined
    if (value.schema !== 'powercontext.kimi-code.profiles.v1' || !Array.isArray(tools) || !tools.length ||
        tools.some(tool => typeof tool !== 'string' || !tool) || new Set(tools).size !== tools.length) throw new Error()
    return tools
  } catch { throw new Failure('invalid_settings') }
}

export function authorization(useToken) {
  const token = useToken ? process.env[TOKEN_ENV] : undefined
  if (useToken && token === undefined) throw new Failure('authentication_failed')
  if (token !== undefined && (!token || !/^[!-~]+$/u.test(token) || token.length > 8192)) {
    throw new Failure('invalid_credentials')
  }
  return token === undefined ? undefined : 'Bearer ' + token
}

export async function request(settings, method, path, body, budgetSignal) {
  const signal = AbortSignal.any([budgetSignal, AbortSignal.timeout(1000)])
  if (signal.aborted) throw new Failure('server_unavailable')
  let response
  try {
    response = await fetch(settings.serverUrl + path, {
      method, redirect: 'error', signal,
      headers: { 'Content-Type': 'application/json',
        ...(settings.authorization ? { Authorization: settings.authorization } : {}) },
      ...(body === undefined ? {} : { body: JSON.stringify(body) }),
    })
  } catch { throw new Failure('server_unavailable', undefined, true) }
  if (!response.ok) {
    await response.body?.cancel().catch(() => {})
    const code = response.status === 401 ? 'authentication_failed' :
      response.status === 403 ? 'forbidden' : response.status === 503 ? 'server_unavailable' :
      response.status === 404 ? 'not_found' : 'invalid_response'
    throw new Failure(code, response.status, true)
  }
  try {
    if (!response.body) throw new Error()
    const reader = response.body.getReader()
    const chunks = []
    let size = 0
    try {
      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        size += value.length
        if (size > 1048576) throw new Error()
        chunks.push(value)
      }
    } finally { await reader.cancel().catch(() => {}) }
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks)))
    if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error()
    return { status: response.status, value }
  } catch { throw new Failure(signal.aborted ? 'server_unavailable' : 'invalid_response', response.status, true) }
}

export function isScopeId(value) {
  return typeof value === 'string' && [...value].length <= 256 && /[^\p{White_Space}\u001c-\u001f]/u.test(value)
}

export function workspaceKey(cwd) {
  if (typeof cwd !== 'string' || !isAbsolute(cwd)) throw new Failure('workspace_required')
  let directory
  try { directory = realpathSync.native(cwd) } catch { directory = resolve(cwd) }
  return { integration: 'kimi-code', kind: 'workspace',
    external_id: createHash('sha256').update(directory).digest('hex') }
}

export async function resolveScope(settings, signal, input = {}) {
  const keys = settings.scopeId ? [] : [workspaceKey(input.cwd)]
  const { status, value } = await request(settings, 'POST', '/v1/scope-bindings/resolve', {
    explicit_scope_id: settings.scopeId, binding_keys: keys,
  }, signal)
  if (status !== 200 || !isScopeId(value.scope_id) || settings.scopeId && value.scope_id !== settings.scopeId) {
    throw new Failure('invalid_response')
  }
  return value.scope_id
}

export function preparedText(value) {
  if (value.schema !== 'powercontext.prepared-context.v1' ||
      !Number.isSafeInteger(value.content_bytes) || value.content_bytes < 0) throw new Failure('invalid_response')
  if (value.status === 'empty' && value.content === null && value.content_bytes === 0) return ''
  if (value.status !== 'ready' || typeof value.content !== 'string' || !value.content.trim() ||
      Buffer.byteLength(value.content, 'utf8') !== value.content_bytes ||
      value.content_bytes > MAX_CONTEXT_BYTES) throw new Failure('invalid_response')
  return value.content
}

export function diagnostic(stage, error, write = line => process.stderr.write(line + '\n')) {
  const failure = error instanceof Failure ? error : new Failure('invalid_response')
  write(JSON.stringify({ component: 'powercontext.kimi_code', event: stage, outcome: failure.code,
    ...(failure.httpStatus === undefined ? {} : { http_status: failure.httpStatus }),
    ...(failure.code === 'server_unavailable' ? { recovery: 'powercontext doctor kimi-code' } : {}),
    ...(stage === 'capture_source' && failure.requestSent &&
      !(failure.httpStatus >= 400 && failure.httpStatus < 500) ? { result: 'unknown' } : {}),
  }))
}
