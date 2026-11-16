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

import type { IncomingMessage, ServerResponse } from 'node:http'
import type { ResolvedConfig } from './config.ts'
import { DASHBOARD_ROUTE, DASHBOARD_SCHEMA, type DashboardPayload } from './dashboard-contract.ts'
import { deploymentRejection, type DeploymentFacts, type StatusBridge } from './status-bridge.ts'

type Connection = { requestRejection(request: { headers: IncomingMessage['headers'] }): 401 | 403 | undefined }
type Fetcher = (input: string | URL, init?: RequestInit) => Promise<Response>

function send(response: ServerResponse, status: number, body: unknown): void {
  if (response.writableEnded) return
  response.statusCode = status
  response.setHeader('content-type', 'application/json; charset=utf-8')
  response.setHeader('cache-control', 'no-store')
  response.setHeader('x-content-type-options', 'nosniff')
  response.end(JSON.stringify(body))
}

/** Read only the configured Server's Dashboard route; never return the Host's credential. */
export async function dashboardAvailability(
  config: ResolvedConfig,
  bridge: Pick<StatusBridge, 'status'>,
  sessionId?: string,
  fetcher: Fetcher = fetch,
): Promise<DashboardPayload> {
  const status = await bridge.status(sessionId)
  const session = status.session
  const base: DashboardPayload = {
    schema: DASHBOARD_SCHEMA,
    state: 'server_unavailable',
    session_id: session?.id ?? null,
    scope_id: session?.scope_id ?? null,
  }
  if (sessionId !== undefined && session?.id !== sessionId) return { ...base, state: 'session_unavailable' }

  const url = `${config.baseUrl.replace(/\/+$/, '')}/dashboard/home`
  const headers: Record<string, string> = { Accept: 'text/html' }
  if (config.authorization) headers.Authorization = config.authorization
  try {
    const response = await fetcher(url, {
      method: 'GET', headers, redirect: 'manual',
      signal: AbortSignal.timeout(Math.max(1_000, Math.min(config.requestTimeoutMs, 8_000))),
    })
    await response.body?.cancel()
    if (response.status === 404) return { ...base, state: 'disabled' }
    if (response.status === 401 || response.status === 403) return { ...base, state: 'authentication' }
    if (response.status !== 200 || response.headers.get('x-dashboard-html') !== '1') {
      return { ...base, state: 'load_unavailable' }
    }
    if (session !== null && session.scope_state !== 'resolved') return { ...base, state: 'scope_unresolved' }
    const page = new URL(url)
    if (session?.scope_id) page.searchParams.set('scope', session.scope_id)
    return { ...base, state: 'available', url: page.toString() }
  } catch {
    return { ...base, state: 'server_unavailable' }
  }
}

/** Keep Dashboard availability separate from the API status palette. */
export function createDashboardRouteHandler(
  bridge: Pick<StatusBridge, 'status'>,
  config: ResolvedConfig,
  connection: Connection,
  facts: DeploymentFacts,
  fetcher: Fetcher = fetch,
): (request: IncomingMessage, response: ServerResponse) => Promise<void> {
  return async (request, response) => {
    const rejection = connection.requestRejection(request)
    if (rejection !== undefined) {
      send(response, rejection, { code: rejection === 401 ? 'dsh_authentication_required' : 'dsh_forbidden' })
      return
    }
    const deployment = deploymentRejection(request, facts)
    if (deployment !== undefined) {
      send(response, deployment.status, { code: deployment.code })
      return
    }
    if (request.method !== 'GET') {
      response.statusCode = 405
      response.setHeader('allow', 'GET')
      response.end()
      return
    }
    try {
      const params = new URL(String(request.url), 'http://localhost').searchParams
      const ids = params.getAll('session_id')
      if (ids.length > 1 || (ids.length === 1 && (!ids[0] || ids[0].length > 256))) {
        send(response, 400, { code: 'invalid_session_id' })
        return
      }
      send(response, 200, await dashboardAvailability(config, bridge, ids[0], fetcher))
    } catch {
      send(response, 500, { code: 'dashboard_bridge_error' })
    }
  }
}

export { DASHBOARD_ROUTE }
