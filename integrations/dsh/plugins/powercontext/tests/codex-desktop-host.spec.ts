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

import { mkdtemp, rm } from 'node:fs/promises'
import { createServer, type RequestListener } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { registerHandoffBridge } from '../src/handoff/host.ts'

const home = vi.hoisted(() => ({ path: '' }))
vi.mock('node:os', async importOriginal => ({
  ...await importOriginal<typeof import('node:os')>(), homedir: () => home.path,
}))

const hosts: Array<() => Promise<void>> = []

beforeEach(async () => { home.path = await mkdtemp(join(tmpdir(), 'dsh-codex-host-')) })
afterEach(async () => {
  try {
    for (const stop of hosts.splice(0).reverse()) await stop()
  } finally { await rm(home.path, { recursive: true, force: true }) }
})

async function startHost() {
  const disposals: Array<() => unknown> = []
  const routes = new Map<string, RequestListener>()
  const host = {
    get: (key: string) => {
      if (key === 'profileContext') return { home: home.path, name: 'web' }
      if (key === 'launchEnvironment') return { getFrom: () => ({ value: '' }) }
    },
    connection: { requestRejection: (request: Parameters<RequestListener>[0]) => request.headers['x-owner'] ? undefined : 401 },
    webServer: { host: '127.0.0.1', register(route: { path: string; kind: string; handler: RequestListener }) {
      if (route.kind === 'prefix') routes.set(route.path, route.handler)
      return () => { routes.delete(route.path) }
    } },
    effect: (register: () => () => unknown) => { disposals.push(register()) },
  }
  const ctx = { inject: (_names: string[], plugin: (ctx: typeof host) => void) => plugin(host) }
  registerHandoffBridge(ctx as never, { config: { baseUrl: 'http://127.0.0.1:1', ownerLocal: true } } as never)
  const server = createServer((req, res) => {
    const collection = req.url?.split('/').slice(0, 3).join('/')
    const handle = collection && routes.get(collection)
    if (handle) handle(req, res)
    else { res.writeHead(404); res.end() }
  })
  const address = await new Promise<number>(resolve => server.listen(0, '127.0.0.1', () => {
    resolve((server.address() as { port: number }).port)
  }))
  hosts.push(async () => {
    await new Promise<void>(resolve => server.close(() => resolve()))
    for (const dispose of disposals.reverse()) await dispose()
  })
  return (path: string, authenticated = true) => fetch(`http://127.0.0.1:${address}/powercontext${path}`,
    { headers: authenticated ? { 'x-owner': 'yes' } : {} })
}

const diagnosePath = '/targets/codex-desktop/diagnose'
const deliveryPath = '/deliveries/missing?sessionId=session'

it('ships the default target catalog and diagnostics through the owner-local Host', async () => {
  const request = await startHost()
  expect((await request(diagnosePath, false)).status).toBe(401)
  const response = await request('/targets')
  expect(response.status).toBe(200)
  const catalog = await response.json() as { targets: Array<{ targetId: string }> }
  const availability = process.platform === 'darwin' ? 'requires_preflight' : 'unavailable'
  for (const targetId of ['codex-desktop', 'claude-code-cli']) {
    expect(catalog.targets.find(target => target.targetId === targetId)).toMatchObject({ diagnostics: 'available', availability })
  }
  expect(catalog.targets.find(target => target.targetId === 'claude-desktop')).toMatchObject({
    diagnostics: 'available', qualification: null, availability: 'unavailable', action: 'manual_setup_or_export',
  })
  const detection = await request(diagnosePath)
  expect(detection.status).toBe(200)
  expect(await detection.json()).toMatchObject({ targetId: 'codex-desktop',
    state: process.platform === 'darwin' ? expect.stringMatching(/^(present|missing|unknown|unsupported)$/) : 'unsupported' })
}, 30_000)

it.skipIf(process.platform !== 'darwin')('serves catalog and diagnostics while another Host holds the delivery writer lease', async () => {
  const reader = await startHost()
  const writer = await startHost()
  expect((await reader('/targets')).status).toBe(200)
  // Opening delivery services takes the real writer lease, even for a missing record.
  const opened = await writer(deliveryPath)
  expect(opened.status).toBe(404)
  expect(await opened.json()).toMatchObject({ code: 'not_found' })
  const blocked = await reader(deliveryPath)
  expect(blocked.status).toBe(503)
  expect(await blocked.json()).toMatchObject({ code: 'integration_unavailable' })
  expect((await reader('/targets')).status).toBe(200)
  expect((await reader(diagnosePath)).status).toBe(200)
}, 30_000)
