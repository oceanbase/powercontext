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
import { describe, expect, it, vi } from 'vitest'
import { dashboardAvailability, createDashboardRouteHandler } from '../src/dashboard-bridge.ts'
import { DASHBOARD_SCHEMA, parseDashboardPayload } from '../src/dashboard-contract.ts'
import type { ResolvedConfig } from '../src/config.ts'
import type { StatusBridge } from '../src/status-bridge.ts'
import { readDashboard } from '../src/ui/dashboard.ts'

const config = {
  baseUrl: 'http://127.0.0.1:18000', requestTimeoutMs: 1_000,
  authorization: 'Bearer host-only-secret',
} as ResolvedConfig

function bridge(session: { id: string; scope_id: string | null; scope_state: string } | null = null) {
  return { status: vi.fn(async () => ({ session, connection: { state: 'ready' } })) } as unknown as StatusBridge
}

function html(status = 200, headers: Record<string, string> = { 'x-dashboard-html': '1' }): Response {
  return new Response('<!doctype html>', { status, headers })
}

describe('Dashboard availability', () => {
  it('opens the existing page for the exact current Scope without putting the credential in the URL', async () => {
    const fetcher = vi.fn(async () => html())
    const result = await dashboardAvailability(config, bridge({ id: 'session-1', scope_id: 'scp_abc', scope_state: 'resolved' }), 'session-1', fetcher)
    expect(result).toEqual({
      schema: DASHBOARD_SCHEMA, state: 'available', session_id: 'session-1', scope_id: 'scp_abc',
      url: 'http://127.0.0.1:18000/dashboard/home?scope=scp_abc',
    })
    expect(fetcher).toHaveBeenCalledWith('http://127.0.0.1:18000/dashboard/home', expect.objectContaining({
      method: 'GET', redirect: 'manual', headers: { Accept: 'text/html', Authorization: 'Bearer host-only-secret' },
    }))
    expect(JSON.stringify(result)).not.toContain('host-only-secret')
  })

  it('has a no-session entry and does not resolve or create a conversation', async () => {
    const status = bridge()
    const result = await dashboardAvailability(config, status, undefined, async () => html())
    expect(result).toMatchObject({ state: 'available', session_id: null, scope_id: null,
      url: 'http://127.0.0.1:18000/dashboard/home' })
    expect(status.status).toHaveBeenCalledWith(undefined)
  })

  it('separates disabled Dashboard and login failure from a healthy API connection', async () => {
    const status = bridge()
    const disabled = await dashboardAvailability(config, status, undefined, async () => html(404))
    const auth = await dashboardAvailability(config, status, undefined, async () => html(401))
    expect(disabled.state).toBe('disabled')
    expect(auth.state).toBe('authentication')
    expect(status.status).toHaveBeenCalledTimes(2)
  })

  it('reports route/content and network failures without claiming custom-provider rejection', async () => {
    const status = bridge()
    expect((await dashboardAvailability(config, status, undefined, async () => html(200, {}))).state)
      .toBe('load_unavailable')
    expect((await dashboardAvailability(config, status, undefined, async () => { throw new Error('offline') })).state)
      .toBe('server_unavailable')
  })

  it('does not open the wrong Scope after a session disappears or resolution fails', async () => {
    const fetcher = vi.fn(async () => html())
    expect((await dashboardAvailability(config, bridge(), 'session-old', fetcher)).state).toBe('session_unavailable')
    expect(fetcher).not.toHaveBeenCalled()
    expect((await dashboardAvailability(config,
      bridge({ id: 'session-2', scope_id: null, scope_state: 'unresolved' }), 'session-2', fetcher)).state)
      .toBe('scope_unresolved')
    expect(fetcher).toHaveBeenCalledTimes(1)
  })

  it('reports Dashboard auth or disabled state before an unresolved Scope', async () => {
    const unresolved = bridge({ id: 'session-2', scope_id: null, scope_state: 'unresolved' })
    expect((await dashboardAvailability(config, unresolved, 'session-2', async () => html(401))).state)
      .toBe('authentication')
    expect((await dashboardAvailability(config, unresolved, 'session-2', async () => html(404))).state)
      .toBe('disabled')
  })
})

describe('Dashboard bridge boundary', () => {
  function response() {
    const output = { statusCode: 200, headers: {} as Record<string, string>, body: '' }
    const writable = {
      get writableEnded() { return false },
      set statusCode(value: number) { output.statusCode = value },
      setHeader(name: string, value: string) { output.headers[name] = value },
      end(value?: string) { output.body = value ?? '' },
    } as ServerResponse
    return { output, writable }
  }

  it('requires DSH authentication and owner-local deployment before reading the Server', async () => {
    const status = bridge()
    const fetcher = vi.fn(async () => html())
    const request = { method: 'GET', url: '/powercontext/dashboard', headers: {}, socket: { remoteAddress: '127.0.0.1' } } as IncomingMessage
    const denied = response()
    await createDashboardRouteHandler(status, config, { requestRejection: () => 401 },
      { ssh: false, desktop: true, ownerLocal: undefined }, fetcher)(request, denied.writable)
    expect(denied.output.statusCode).toBe(401)
    const remote = response()
    await createDashboardRouteHandler(status, config, { requestRejection: () => undefined },
      { ssh: false, desktop: false, ownerLocal: undefined }, fetcher)(request, remote.writable)
    expect(remote.output.statusCode).toBe(403)
    expect(status.status).not.toHaveBeenCalled()
    expect(fetcher).not.toHaveBeenCalled()
  })
})

describe('Dashboard browser contract', () => {
  it('uses the authenticated DSH route with only the Session selector', async () => {
    const payload = { schema: DASHBOARD_SCHEMA, state: 'available', session_id: 'session-1',
      scope_id: 'scope-a', url: 'http://127.0.0.1:18000/dashboard/home?scope=scope-a' }
    const fetcher = vi.fn(async (_input: string | URL, _init?: RequestInit) => new Response(JSON.stringify(payload)))
    expect(await readDashboard('session-1', fetcher)).toEqual(payload)
    expect(fetcher.mock.calls[0]![0]).toBe('/powercontext/dashboard?session_id=session-1')
    expect(fetcher.mock.calls[0]![1]).toMatchObject({ credentials: 'same-origin', method: 'GET' })
    expect(JSON.stringify(fetcher.mock.calls[0]![1])).not.toMatch(/Bearer|host-only-secret/)
  })

  it('rejects a credential-bearing or unrelated browser URL', () => {
    const payload = { schema: DASHBOARD_SCHEMA, state: 'available', session_id: null, scope_id: null }
    expect(parseDashboardPayload({ ...payload, url: 'http://user@127.0.0.1/dashboard/home' })).toBeNull()
    expect(parseDashboardPayload({ ...payload, url: 'http://127.0.0.1/dashboard/home?token=secret' })).toBeNull()
    expect(parseDashboardPayload({ ...payload, scope_id: 'scope-a', url: 'http://127.0.0.1/dashboard/home?scope=scope-b' })).toBeNull()
    expect(parseDashboardPayload({ ...payload, url: 'javascript:alert(1)' })).toBeNull()
  })
})
