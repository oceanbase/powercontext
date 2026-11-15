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
import { expect, it, vi } from 'vitest'
import { registerHandoffBridge } from '../src/handoff/host.ts'

const home = vi.hoisted(() => ({ path: '' }))
vi.mock('node:os', async importOriginal => ({
  ...await importOriginal<typeof import('node:os')>(), homedir: () => home.path,
}))

it.skipIf(process.platform !== 'darwin')('ships Codex diagnostics through the owner-local Host without enabling launch', async () => {
  home.path = await mkdtemp(join(tmpdir(), 'dsh-codex-host-'))
  const disposals: Array<() => unknown> = []
  let handler!: RequestListener
  const host = {
    get: (key: string) => key === 'profileContext' ? { home: home.path, name: 'desktop' } : undefined,
    connection: { requestRejection: (request: Parameters<RequestListener>[0]) => request.headers['x-owner'] ? undefined : 401 },
    webServer: { host: '127.0.0.1', register(route: { path: string; kind: string; handler: RequestListener }) {
      if (route.path === '/powercontext/targets' && route.kind === 'prefix') handler = route.handler
      return () => {}
    } },
    effect: (register: () => () => unknown) => { disposals.push(register()) },
  }
  const ctx = { inject: (_names: string[], plugin: (ctx: typeof host) => void) => plugin(host) }
  const server = createServer((req, res) => handler(req, res))
  try {
    registerHandoffBridge(ctx as never, { config: { baseUrl: 'http://127.0.0.1:1' } } as never)
    expect(handler).toBeTypeOf('function')
    const address = await new Promise<number>(resolve => server.listen(0, '127.0.0.1', () => {
      resolve((server.address() as { port: number }).port)
    }))
    const url = `http://127.0.0.1:${address}/powercontext/targets`
    expect((await fetch(url + '/codex-desktop/diagnose')).status).toBe(401)
    const headers = { 'x-owner': 'yes' }
    const catalog = await (await fetch(url, { headers })).json() as { targets: Array<{ targetId: string }> }
    expect(catalog.targets.find(target => target.targetId === 'codex-desktop')).toMatchObject({
      diagnostics: 'available', qualification: null, availability: 'unavailable',
    })
    const detection = await fetch(url + '/codex-desktop/diagnose', { headers })
    expect(detection.status).toBe(200)
    expect(await detection.json()).toMatchObject({ targetId: 'codex-desktop',
      state: expect.stringMatching(/^(present|missing|unknown|unsupported)$/) })
  } finally {
    await new Promise<void>(resolve => server.close(() => resolve()))
    for (const dispose of disposals.reverse()) await dispose()
    await rm(home.path, { recursive: true, force: true })
  }
}, 30_000)
