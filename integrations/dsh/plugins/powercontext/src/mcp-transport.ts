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

import { randomUUID } from 'node:crypto'
import { createServer, request as httpRequest, type ClientRequest, type IncomingHttpHeaders, type ServerResponse } from 'node:http'
import { request as httpsRequest } from 'node:https'
import type { Context } from '@deepseek-ai/cordis'

function headersForForwarding(headers: IncomingHttpHeaders): IncomingHttpHeaders {
  const forwarded = { ...headers }
  const connectionHeaders = String(headers.connection ?? '').split(',').map(header => header.trim().toLowerCase())
  for (const header of [...connectionHeaders, 'host', 'connection', 'keep-alive', 'proxy-authenticate',
    'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade']) delete forwarded[header]
  return forwarded
}

function httpFailureCode(status: number): string {
  switch (status) {
    case 401: return 'authentication_failed'
    case 403: return 'authorization_failed'
    case 404: return 'not_found'
    case 409: return 'conflict'
    case 400:
    case 422: return 'invalid_request'
    case 429: return 'rate_limited'
    default: return status >= 500 ? 'unavailable' : 'http_error'
  }
}

function fail(res: ServerResponse, code: string, status = 502): void {
  if (res.destroyed) return
  if (res.headersSent) { res.destroy(); return }
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify({ error: {
    code, status,
    message: 'PowerContext MCP request failed. Upstream diagnostics were suppressed; do not assume the operation completed.',
  } }))
}

export async function protectMcpEndpoint(ctx: Pick<Context, 'effect'>, endpoint: string): Promise<string> {
  const target = new URL(endpoint)
  if (!['http:', 'https:'].includes(target.protocol)) throw new Error('Unsupported PowerContext MCP transport')
  const forward = target.protocol === 'https:' ? httpsRequest : httpRequest
  const path = `/mcp/${randomUUID()}`
  const upstreams = new Set<ClientRequest>()
  // The pinned native client has no requestInit/fetch customization. Relay only MCP frames to the fixed
  // configured endpoint; Node's HTTP requests never follow redirects. Do not patch the host's global fetch.
  const server = createServer((req, res) => {
    if (req.url !== path || !['GET', 'POST', 'DELETE'].includes(req.method ?? '')) {
      res.writeHead(404).end()
      return
    }
    const upstream = forward(target, {
      method: req.method, headers: headersForForwarding(req.headers), rejectUnauthorized: true,
    }, (response) => {
      response.on('error', () => res.destroy())
      if (response.statusCode && response.statusCode >= 300 && response.statusCode < 400) {
        response.resume()
        fail(res, 'redirect_rejected')
        return
      }
      const status = response.statusCode ?? 502
      if (status < 200 || status >= 400) {
        // Native MCP clients include HTTP error bodies in tool failures. Never relay upstream
        // diagnostics or error headers; keep the status for native auth and session recovery.
        response.resume()
        fail(res, httpFailureCode(status), status)
        return
      }
      res.writeHead(response.statusCode ?? 502, headersForForwarding(response.headers))
      response.pipe(res)
    })
    upstreams.add(upstream)
    upstream.once('close', () => upstreams.delete(upstream))
    upstream.on('error', () => fail(res, 'mcp_transport_unavailable'))
    req.once('aborted', () => upstream.destroy())
    req.on('error', () => upstream.destroy())
    res.once('close', () => upstream.destroy())
    req.pipe(upstream)
  })
  await new Promise<void>((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', () => { server.off('error', reject); resolve() })
  })
  const close = async () => {
    for (const upstream of upstreams) upstream.destroy()
    server.closeAllConnections()
    if (server.listening) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
  }
  try {
    ctx.effect(() => close)
  } catch (error) {
    await close()
    throw error
  }
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('PowerContext MCP relay is unavailable')
  return `http://127.0.0.1:${address.port}${path}`
}
