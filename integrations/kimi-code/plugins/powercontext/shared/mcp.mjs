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

import { Failure } from './runtime.mjs'

// Read-only MCP discovery: no tools/call and no domain writes.
export async function discoverTools(settings, expected) {
  const budget = AbortSignal.timeout(3000)
  let session
  let protocol = '2025-03-26'
  async function rpc(message) {
    let response
    try {
      response = await fetch(settings.serverUrl + '/mcp/', {
        method: 'POST', redirect: 'error', signal: budget,
        headers: { 'Content-Type': 'application/json', Accept: 'application/json, text/event-stream',
          ...(settings.authorization ? { Authorization: settings.authorization } : {}),
          ...(session ? { 'Mcp-Session-Id': session } : {}), 'MCP-Protocol-Version': protocol },
        body: JSON.stringify({ jsonrpc: '2.0', ...message }),
      })
    } catch { throw new Failure('server_unavailable') }
    if (!response.ok) {
      await response.body?.cancel().catch(() => {})
      throw new Failure(response.status === 401 ? 'authentication_failed' : response.status === 403 ? 'forbidden' :
        response.status === 404 ? 'not_found' : 'server_unavailable', response.status)
    }
    session = response.headers.get('mcp-session-id') ?? session
    if (!Object.hasOwn(message, 'id')) {
      await response.body?.cancel().catch(() => {})
      return
    }
    const reader = response.body?.getReader()
    if (!reader) throw new Failure('invalid_response')
    const chunks = []
    let size = 0
    try {
      for (;;) {
        const { done, value } = await reader.read()
        if (done) break
        size += value.length
        if (size > 1048576) throw new Failure('invalid_response')
        chunks.push(value)
      }
    } catch (error) {
      throw error instanceof Failure ? error : new Failure('server_unavailable')
    } finally { await reader.cancel().catch(() => {}) }
    let value
    try {
      const text = new TextDecoder('utf-8', { fatal: true }).decode(Buffer.concat(chunks))
      if (response.headers.get('content-type')?.includes('text/event-stream')) {
        const messages = text.split(/\r?\n\r?\n/u).map(event => event.split(/\r?\n/u)
          .filter(line => line.startsWith('data:')).map(line => line.slice(5).trimStart()).join('\n'))
          .filter(Boolean).map(data => JSON.parse(data))
        value = messages.find(item => item.id === message.id)
      } else value = JSON.parse(text)
    } catch { throw new Failure('invalid_response') }
    if (value?.jsonrpc !== '2.0' || value.id !== message.id || value.error || !value.result) throw new Failure('invalid_response')
    return value.result
  }
  try {
    const initialized = await rpc({ id: 1, method: 'initialize', params: {
      protocolVersion: protocol, capabilities: {}, clientInfo: { name: 'powercontext-kimi-doctor', version: '1' },
    } })
    if (typeof initialized.protocolVersion !== 'string' || !initialized.capabilities?.tools) throw new Failure('invalid_response')
    protocol = initialized.protocolVersion
    await rpc({ method: 'notifications/initialized' })
    const available = new Set()
    const cursors = new Set()
    let cursor
    for (let page = 0; page < 8; page++) {
      const listed = await rpc({ id: page + 2, method: 'tools/list', params: cursor ? { cursor } : {} })
      if (!Array.isArray(listed.tools) || listed.tools.some(tool => typeof tool.name !== 'string')) throw new Failure('invalid_response')
      for (const tool of listed.tools) available.add(tool.name)
      cursor = listed.nextCursor
      if (cursor === undefined) {
        const missing = expected.filter(tool => !available.has(tool))
        return { status: missing.length ? 'failed' : 'ok',
          detail: missing.length ? 'Selected MCP tools are unavailable.' : 'Server MCP discovery succeeded; host loading and operations are not verified.',
          checks: { expected_tools: String(expected.length), available_tools: String(available.size), missing_tools: missing.join(', ') } }
      }
      if (typeof cursor !== 'string' || !cursor || cursors.has(cursor)) throw new Failure('invalid_response')
      cursors.add(cursor)
    }
    throw new Failure('invalid_response')
  } finally {
    if (session) {
      await fetch(settings.serverUrl + '/mcp/', {
        method: 'DELETE', redirect: 'error', signal: AbortSignal.timeout(250),
        headers: { 'Mcp-Session-Id': session, 'MCP-Protocol-Version': protocol,
          ...(settings.authorization ? { Authorization: settings.authorization } : {}) },
      }).then(response => response.body?.cancel()).catch(() => {})
    }
  }
}
