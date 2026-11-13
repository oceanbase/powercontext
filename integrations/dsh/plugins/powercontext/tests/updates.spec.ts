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

import { createServer } from 'node:http'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DshUpdates, createUpdateRouteHandler, updateLedger, UPDATE_CHECK_INTERVAL_MS, UPDATE_STARTUP_DELAY_MS, type NativePluginManager } from '../src/update-bridge.ts'
import { UPDATE_ACTION_HEADER, UPDATE_PLAN_HEADER } from '../src/update-contract.ts'

const NAME = 'powercontext-dsh'
const NPM = 'https://registry.npmjs.org/'
const SHA256 = 'a'.repeat(64)

type BundleCapability = Pick<Awaited<ReturnType<NativePluginManager['listBundles']>>[number], 'updatable' | 'update'>

function release(python = '1.5.1', npm = python, prerelease = false) {
  return {
    tag_name: 'powercontext-v' + python, published_at: '2026-11-09T12:23:48Z', draft: false, prerelease,
    assets: [
      { name: 'powercontext-dsh-' + npm + '.tgz', state: 'uploaded' },
      { name: 'powercontext-' + python + '-py3-none-any.whl', state: 'uploaded', digest: 'sha256:' + SHA256 },
    ],
  }
}

function fixture(options: {
  version?: string; server?: string | null; channel?: 'stable' | 'preview'; held?: boolean; busy?: boolean
  capability?: BundleCapability
  releases?: ReturnType<typeof release>[]; source?: string; incompatible?: boolean; absent?: boolean; badWheel?: boolean; clock?: () => number
} = {}) {
  const ledger = updateLedger()
  const version = options.version ?? '1.5.0'
  const target = options.channel === 'preview' ? '1.6.0-rc.2' : '1.5.1'
  let installed = version
  let server = options.server === undefined ? '1.5.1' : options.server
  let now = 0
  const source = options.source ?? '1.5.0'
  const manager = {
    listBundles: vi.fn(() => [{
      name: NAME, version: installed, enabled: true, installed: true, source,
      ...(options.capability ?? { updatable: !source.startsWith('link:') }),
    }]),
    registries: vi.fn(async () => ({ registry: null, resolved: NPM, fallbackRegistries: ['https://registry.npmmirror.com/'] })),
    listVersions: vi.fn(async () => ({
      status: 'listed' as const, installed, registry: null,
      versions: options.absent ? [] : [{ version: target, bundle: true, ...(options.incompatible ? { incompatible: {} } : {}) }],
    })),
    updateBundle: vi.fn<NativePluginManager['updateBundle']>(async (_name, next) => {
      installed = next
      return { result: { application: 'restart-required', version: next } }
    }),
  }
  const fetcher = vi.fn<typeof fetch>(async url => String(url).startsWith('https://api.github.com')
    ? Response.json(options.releases ?? [release(), release('1.6.0rc2', '1.6.0-rc.2', true)])
    : Response.json({ urls: [{
      filename: 'powercontext-' + (options.channel === 'preview' ? '1.6.0rc2' : '1.5.1') + '-py3-none-any.whl',
      yanked: false, digests: { sha256: options.badWheel ? 'b'.repeat(64) : SHA256 },
    }] }))
  const host = (running = version) => new DshUpdates({
    ledger, pluginVersion: running, channel: options.channel ?? 'stable', held: options.held ?? false,
    manager: () => manager, serverVersion: async () => server, canInstall: () => !options.busy,
    fetch: fetcher, now: options.clock ?? (() => now),
  })
  return { host, manager, fetcher, setServer: (value: string | null) => { server = value }, tick: (ms: number) => { now += ms } }
}

async function check(updates: DshUpdates, phase: string) {
  expect(updates.check().phase).toBe('checking')
  await vi.waitFor(() => expect(updates.read().phase).toBe(phase))
  return updates.read()
}

describe('DSH release checks and updates', () => {
  it('checks after startup and once per hour without a panel or an automatic install', async () => {
    vi.useFakeTimers()
    const f = fixture({ clock: Date.now })
    const updates = f.host()
    const stop = updates.scheduleChecks()
    try {
      await vi.advanceTimersByTimeAsync(UPDATE_STARTUP_DELAY_MS - 1)
      expect(updates.read().phase).toBe('idle')
      await vi.advanceTimersByTimeAsync(1)
      const initial = updates.read()
      expect(initial.phase).toBe('available')
      expect(initial.plan).toBeTruthy()
      await vi.advanceTimersByTimeAsync(UPDATE_CHECK_INTERVAL_MS - 1)
      expect(updates.read()).toMatchObject({ phase: 'available', plan: initial.plan })
      expect(f.fetcher).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(1)
      expect(updates.read().plan).not.toBe(initial.plan)
      expect(f.fetcher).toHaveBeenCalledTimes(2)
      expect(f.manager.updateBundle).not.toHaveBeenCalled()
      stop()
      await vi.advanceTimersByTimeAsync(UPDATE_CHECK_INTERVAL_MS)
      expect(f.fetcher).toHaveBeenCalledTimes(2)
    } finally {
      stop()
      vi.useRealTimers()
    }
  })

  it('reschedules an hourly check relative to a manual check', async () => {
    vi.useFakeTimers()
    const f = fixture({ clock: Date.now })
    const updates = f.host()
    const stop = updates.scheduleChecks()
    try {
      await vi.advanceTimersByTimeAsync(1_000)
      updates.check()
      await vi.advanceTimersByTimeAsync(UPDATE_CHECK_INTERVAL_MS - 1)
      expect(f.fetcher).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(1)
      expect(f.fetcher).toHaveBeenCalledTimes(2)
    } finally {
      stop()
      vi.useRealTimers()
    }
  })

  it('checks stable by default and reports independently when the Server is already current', async () => {
    const f = fixture({ version: '1.5.1' })
    const result = await check(f.host(), 'current')
    expect(result).toMatchObject({ plugin_version: '1.5.1', plugin_target: '1.5.1', server_state: 'current' })
    expect(f.manager.updateBundle).not.toHaveBeenCalled()
  })

  it.each([{ updatable: true }, { update: 'registry' }] satisfies BundleCapability[])(
    'accepts exactly the checked preview target once across reloads with %j',
    async capability => {
      const f = fixture({ channel: 'preview', server: '1.6.0rc2', capability })
      const host = f.host()
      const available = await check(host, 'available')
      expect(available.plugin_target).toBe('1.6.0-rc.2')
      expect(host.install('wrong plan')).toBeNull()
      expect(host.install(available.plan!)).toMatchObject({ phase: 'installing', plan: null })
      expect(f.host().install(available.plan!)).toBeNull()
      await vi.waitFor(() => expect(f.host().read().phase).toBe('restart_required'))
      expect(f.manager.updateBundle).toHaveBeenCalledExactlyOnceWith(NAME, '1.6.0-rc.2', { registry: NPM })
      expect(f.host('1.6.0-rc.2').read()).toMatchObject({ phase: 'updated', plugin_version: '1.6.0-rc.2' })
    },
  )

  it('keeps Git bundles manual even if the legacy update flag is also set', async () => {
    const f = fixture({ capability: { update: 'git', updatable: true } })
    expect(await check(f.host(), 'manual')).toMatchObject({ plan: null })
    expect(f.manager.listVersions).not.toHaveBeenCalled()
  })

  it('revalidates the registry update capability before applying an existing plan', async () => {
    const f = fixture({ capability: { update: 'registry' } })
    const host = f.host()
    const result = await check(host, 'available')
    const [bundle] = f.manager.listBundles()
    f.manager.listBundles.mockReturnValue([{ ...bundle, update: 'git', updatable: true }])
    expect(host.install(result.plan!)?.phase).toBe('installing')
    await vi.waitFor(() => expect(host.read().phase).toBe('manual'))
    expect(f.manager.updateBundle).not.toHaveBeenCalled()
  })

  it('still checks with the Server offline and offers matching Server guidance without an install plan', async () => {
    const f = fixture({ server: null })
    expect(await check(f.host(), 'server_first')).toMatchObject({
      plugin_target: '1.5.1', server_target: '1.5.1', server_state: 'available', plan: null,
    })
  })

  it('does not advertise a Server package whose PyPI digest differs from the release wheel', async () => {
    const f = fixture({ server: '1.5.0', badWheel: true })
    expect(await check(f.host(), 'server_first')).toMatchObject({ server_state: 'artifact_unavailable', plan: null })
  })

  it.each([
    [{ source: 'link:/checkout' }, 'manual'],
    [{ incompatible: true }, 'incompatible'],
    [{ absent: true }, 'artifact_unavailable'],
    [{ held: true }, 'held'],
  ] as const)('gives guidance for %j without arming installation', async (options, phase) => {
    const f = fixture(options)
    expect(await check(f.host(), phase)).toMatchObject({ plugin_target: '1.5.1', plan: null })
    expect(f.manager.updateBundle).not.toHaveBeenCalled()
  })

  it('keeps installations from custom registries on their current update path', async () => {
    const f = fixture()
    f.manager.registries.mockResolvedValue({ registry: null, resolved: 'https://registry.example/', fallbackRegistries: [] })
    expect(await check(f.host(), 'manual')).toMatchObject({ plan: null })
    expect(f.manager.listVersions).not.toHaveBeenCalled()
  })

  it('does not install after a target expires or the running Server changes', async () => {
    const f = fixture()
    const host = f.host()
    const result = await check(host, 'available')
    f.tick(UPDATE_CHECK_INTERVAL_MS + 6 * 60_000)
    expect(host.install(result.plan!)).toBeNull()
    const fresh = await check(host, 'available')
    f.setServer('1.5.0')
    expect(host.install(fresh.plan!)?.phase).toBe('installing')
    await vi.waitFor(() => expect(host.read().phase).toBe('server_first'))
    expect(f.manager.updateBundle).not.toHaveBeenCalled()
  })

  it('defers installation while an ordinary DSH turn is active', async () => {
    const f = fixture({ busy: true })
    const host = f.host()
    const result = await check(host, 'available')
    host.install(result.plan!)
    await vi.waitFor(() => expect(host.read().phase).toBe('busy'))
    expect(f.manager.updateBundle).not.toHaveBeenCalled()
  })

  it('reports native recovery only after the previous package is present', async () => {
    const f = fixture()
    f.manager.updateBundle.mockResolvedValue({
      result: { application: 'cancelled' }, restore: { application: 'applied' },
    })
    const host = f.host()
    host.install((await check(host, 'available')).plan!)
    await vi.waitFor(() => expect(host.read().phase).toBe('restored'))
  })

  it('keeps unknown outcomes distinct from completion when native update rejects', async () => {
    const f = fixture()
    f.manager.updateBundle.mockRejectedValue(new Error('process interrupted'))
    const host = f.host()
    host.install((await check(host, 'available')).plan!)
    await vi.waitFor(() => expect(host.read().phase).toBe('unconfirmed'))
  })
})

describe('update Host route', () => {
  const servers: ReturnType<typeof createServer>[] = []
  afterEach(() => {
    for (const server of servers.splice(0)) server.close()
  })

  it('allows only an authenticated local owner with the matching plan to start a native update', async () => {
    const f = fixture()
    const host = f.host()
    const target = await check(host, 'available')
    const server = createServer(createUpdateRouteHandler(host, {
      requestRejection: request => request.headers.authorization ? undefined : 401,
      admit: request => ({ peer: { principal: { kind: request.headers.authorization === 'owner' ? 'owner' : 'guest' } } }),
    }, { ownerLocal: true, desktop: false, ssh: false }))
    servers.push(server)
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
    const addr = server.address()
    if (!addr || typeof addr === 'string') throw new Error('no listener address')
    const url = 'http://127.0.0.1:' + addr.port
    const request = (auth: string, plan?: string) => fetch(url, { method: 'POST', headers: {
      ...(auth ? { authorization: auth } : {}),
      ...(plan ? { [UPDATE_ACTION_HEADER]: 'install', [UPDATE_PLAN_HEADER]: plan } : {}),
    } })
    expect((await request('', target.plan!)).status).toBe(401)
    expect((await request('guest', target.plan!)).status).toBe(403)
    expect((await request('owner')).status).toBe(400)
    expect((await request('owner', 'stale-plan')).status).toBe(409)
    expect(f.manager.updateBundle).not.toHaveBeenCalled()
    expect((await request('owner', target.plan!)).status).toBe(202)
    await vi.waitFor(() => expect(host.read().phase).toBe('restart_required'))
  })
})
