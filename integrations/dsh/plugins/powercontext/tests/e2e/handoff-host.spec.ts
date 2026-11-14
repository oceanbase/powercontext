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


import { mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, it, vi } from 'vitest'
import { SERVER_E2E_TIMEOUT_MS, startPowerContextServer } from '../../scripts/e2e-server.mjs'
import { PowerContextClient } from '../../src/client.ts'
import { createHandoffServices } from '../../src/handoff/assembly.ts'
import type { DeliveryView, SourceBoundary } from '../../src/handoff/contracts.ts'
import { HandoffError } from '../../src/handoff/errors.ts'
import type { LaunchView } from '../../src/handoff/launch.ts'
import type { PreflightView } from '../../src/handoff/preflight.ts'
import { createHandoffHandler } from '../../src/handoff/routes.ts'
import { TargetCatalog, type TargetAdapter } from '../../src/handoff/targets.ts'

it('revalidates preflight, persists one real Server package, and recovers launch and receipt independently', async () => {
  const server = await startPowerContextServer({ env: { POWERCONTEXT_SERVER_ACCESS_MODE: 'disabled' } })
  const root = mkdtempSync(join(tmpdir(), 'dsh-handoff-host-e2e-'))
  const client = new PowerContextClient({ baseUrl: server.baseUrl, requestTimeoutMs: 5000 })
  const requests = vi.spyOn(client, 'request')
  const dispatch = vi.fn<() => Promise<'unknown' | 'opened'>>(async () => 'unknown')
  let services: ReturnType<typeof createHandoffServices> | undefined
  let lifetime = new AbortController()
  const source: SourceBoundary = {
    sessionId: 'selected-session', messageId: 'selected-reply', throughSeq: 10, cwd: root,
    workspaceObservedAt: new Date().toISOString(),
  }
  let latest = source.messageId
  let readsBeforeConfigChange: number | undefined
  let scopeId: string
  let targetScope: string
  let serverIdentity: string
  const adapter: TargetAdapter = {
    id: 'codex-desktop', version: 'fixture-adapter',
    qualification: { platform: 'darwin', evidence: 'fixture; no product launch' },
    detect: async () => ({ state: 'present', version: 'fixture', configurationKey: 'fixture-profile' }),
    checkIntegration: async () => ({ state: 'ready' }),
    installIntegration: async () => { throw new Error('no installation consent in this test') },
    resolveEnvironment: async (_target, cwd) => ({
      effectiveCwd: cwd, serverIdentity, scopeId: targetScope, configurationGeneration: 'target-config',
      newSession: true, unsentPrefill: true, packageAccess: true,
    }),
    canReadPackage: async () => true,
    launchIntent: (_target, bundle) => ({ draft: 'Read ' + bundle.packagePath + ' and acknowledge ' + bundle.receiptAttempt.sourceId }),
  }
  const catalog = new TargetCatalog([adapter], 'darwin')
  const plan = vi.fn(() => ({ dispatch }))
  const assemble = () => createHandoffServices({
    root, catalog, client, signal: lifetime.signal, plan,
    authority: {
      requireLocal() {},
      async verifySource(context, boundary, requireLatest) {
        if (context.ownerId !== 'local-owner' || boundary.sessionId !== source.sessionId) throw new HandoffError('not_found')
        if (requireLatest && latest !== boundary.messageId) throw new HandoffError('stale_preflight')
      },
      async sourceConnection(context, boundary) {
        const [identity, scope] = await Promise.all([
          client.request('get_connection_identity', {}, context.signal),
          client.request('resolve_scope_binding', { binding_keys: [] }, context.signal),
        ])
        if (identity.kind !== 'json' || scope.kind !== 'json' || boundary.sessionId !== source.sessionId) {
          throw new HandoffError('binding_mismatch')
        }
        return { serverIdentity: (identity.value as { server_id: string }).server_id,
          scopeId: (scope.value as { scope_id: string }).scope_id, connectionRef: 'fixture',
          connectionGeneration: readsBeforeConfigChange !== undefined && readsBeforeConfigChange-- <= 0 ? 2 : 1 }
      },
    },
  })
  const http = createServer((req, res) => createHandoffHandler({
    facts: { desktop: true, ssh: false, ownerLocal: undefined },
    requestRejection: () => undefined,
    authenticatedOwner: request => request.headers['x-fixture-owner'] === 'other' ? 'other-owner' : 'local-owner',
    requireLocal() {}, catalog, signal: lifetime.signal, services: () => services!,
  })(req, res))
  try {
    const identity = await client.request('get_connection_identity')
    const defaultScope = await client.request('get_default_scope')
    if (identity.kind !== 'json' || defaultScope.kind !== 'json') throw new Error('fixture binding unavailable')
    serverIdentity = (identity.value as { server_id: string }).server_id
    scopeId = (defaultScope.value as { scope_id: string }).scope_id
    targetScope = scopeId
    services = assemble()
    await new Promise<void>(resolve => http.listen(0, '127.0.0.1', resolve))
    const address = http.address()
    if (!address || typeof address === 'string') throw new Error('missing fixture address')
    const request = (path: string, body?: unknown, owner = 'local') =>
      fetch(`http://127.0.0.1:${address.port}/powercontext${path}`, {
        ...(body === undefined ? {} : { method: 'POST', body: JSON.stringify(body) }),
        headers: { 'content-type': 'application/json', 'x-fixture-owner': owner }, signal: AbortSignal.timeout(10000),
      })
    const begin = async (requestId: string) => {
      const response = await request('/preflights', { requestId, targetId: adapter.id, source })
      expect(response.status).toBe(200)
      return response.json() as Promise<PreflightView>
    }
    const workInput = {
      schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input',
      objective: '保留完整交接包和回执',
      state: [{ basis: 'declared', text: 'Preserve the selected response.', evidence: [] }],
      disposition: 'blocked', next_action: null, omissions: ['Target prefill is tested with a double.'],
    }
    const prepare = (preflight: PreflightView, requestId: string, fields: Record<string, unknown>) =>
      request('/deliveries', { requestId, preflightId: preflight.preflightId, expectedRevision: preflight.revision, source, ...fields })
    const workWrites = () => requests.mock.calls.map(([operation]) => operation).filter(operation =>
      ['capture_content_source', 'handoff_current_work', 'prepare_handoff', 'finalize_handoff', 'commit_handoff'].includes(operation))
    const launchInput = { requestId: 'launch', sessionId: source.sessionId, targetId: adapter.id }

    targetScope = 'different-scope'
    const failed = await begin('conflicting-scope')
    expect(failed).toMatchObject({ state: 'failed', code: 'binding_mismatch' })
    const denied = await prepare(failed, 'failed-prepare', { workInput })
    expect(denied.status).toBe(409)
    expect(await denied.json()).toMatchObject({ code: 'not_ready' })
    expect(workWrites()).toEqual([])

    targetScope = scopeId
    const ready = await begin('verified-scope')
    latest = 'another-reply'
    const stale = await prepare(ready, 'stale-prepare', { workInput })
    expect(await stale.json()).toMatchObject({ code: 'stale_preflight' })
    expect(workWrites()).toEqual([])
    latest = source.messageId

    const needsInput = await (await prepare(ready, 'inspect-first', {})).json() as { delivery: DeliveryView }
    expect(needsInput.delivery.preparationState).toBe('needs_input')
    const noPackage = await request(`/deliveries/${needsInput.delivery.deliveryId}/launch`, launchInput)
    expect(noPackage.status).toBe(409)
    expect(await noPackage.json()).toMatchObject({ code: 'not_ready' })
    expect(dispatch).not.toHaveBeenCalled()
    expect(workWrites()).toEqual([])

    readsBeforeConfigChange = 1 // The verified connection changes between gate and preparation dispatch.
    const rejected = await (await prepare(ready, 'changed-before-dispatch', {
      workInput, supersedesDeliveryId: needsInput.delivery.deliveryId,
    })).json() as { delivery: DeliveryView }
    expect(rejected.delivery).toMatchObject({ preparationState: 'failed', error: { code: 'binding_mismatch' } })
    const failedPackage = await request(`/deliveries/${rejected.delivery.deliveryId}/launch`, launchInput)
    expect(await failedPackage.json()).toMatchObject({ code: 'not_ready' })
    expect(dispatch).not.toHaveBeenCalled()
    expect(workWrites()).toEqual([])
    readsBeforeConfigChange = undefined

    const prepared = await (await prepare(ready, 'prepare', {
      workInput, supersedesDeliveryId: rejected.delivery.deliveryId,
    })).json() as { delivery: DeliveryView }
    expect(prepared.delivery.preparationState).toBe('prepared')
    expect(workWrites()).toEqual(['handoff_current_work'])
    const { deliveryId, receiptAttempt } = prepared.delivery
    const owner = { ownerId: 'local-owner', signal: lifetime.signal }
    const bundle = await services.delivery.getForLaunch(owner, deliveryId)
    const original = readFileSync(bundle.packagePath)
    const carrier = JSON.parse(original.toString('utf8')).carrier
    expect(carrier).toMatchObject({ kind: 'prepared', sourceReceipts: [{ kind: 'handoff-boundary' }] })
    expect(carrier.prepared).toHaveProperty('base')
    expect(carrier.prepared.content.objective).toBe(workInput.objective)
    expect(bundle.binding).toEqual(ready.binding)
    expect(bundle.receiptAttempt).toEqual(receiptAttempt)
    expect(receiptAttempt.sourceType).toBe('content')

    const deliver = `/deliveries/${deliveryId}`
    expect((await request(deliver + '/launch', { ...launchInput, sessionId: 'different-session' })).status).toBe(404)
    expect((await request(deliver + '?sessionId=' + source.sessionId, undefined, 'other')).status).toBe(404)
    expect((await request(deliver + '/export?sessionId=different-session')).status).toBe(404)
    targetScope = 'new-target-scope'
    const mismatched = await request(deliver + '/launch', launchInput)
    expect(await mismatched.json()).toMatchObject({ code: 'binding_mismatch' })
    expect(dispatch).not.toHaveBeenCalled()
    expect(readFileSync(bundle.packagePath)).toEqual(original)

    targetScope = scopeId
    latest = 'source-can-continue-after-preparation'
    const launched = await (await request(deliver + '/launch', launchInput)).json() as LaunchView
    expect(launched.state).toBe('unknown')
    expect(plan).toHaveBeenCalledWith(adapter.id, bundle.binding.effectiveCwd,
      { draft: 'Read ' + bundle.packagePath + ' and acknowledge ' + receiptAttempt.sourceId })
    expect(dispatch).toHaveBeenCalledTimes(1)

    lifetime.abort()
    await services.close()
    lifetime = new AbortController()
    services = assemble()
    const restarted = await (await request(deliver + '?sessionId=' + source.sessionId)).json()
    expect(restarted).toMatchObject({
      preflight: { state: 'ready', binding: bundle.binding },
      delivery: { preparationState: 'prepared', receiptAttempt, source },
      launchAttempts: [launched],
    })
    expect(await (await request(deliver + '/launch', launchInput)).json()).toEqual(launched)
    expect(await (await request(deliver + '/launch', { ...launchInput, requestId: 'implicit-repeat' })).json())
      .toMatchObject({ code: 'outcome_unknown' })
    expect(dispatch).toHaveBeenCalledTimes(1)

    const retryInput = { ...launchInput, requestId: 'explicit-repeat', retryOf: launched.launchAttemptId }
    dispatch.mockResolvedValueOnce('opened')
    const retry = await (await request(deliver + '/launch', retryInput)).json() as LaunchView
    expect(retry.state).toBe('opened')
    expect(retry.launchAttemptId).not.toBe(launched.launchAttemptId)
    const afterRetry = await (await request(deliver + '/cancel', { sessionId: source.sessionId })).json()
    expect(afterRetry).toMatchObject({ delivery: { preparationState: 'prepared', receiptAttempt }, launchAttempts: [launched, retry] })
    expect(await services.delivery.getForLaunch({ ...owner, signal: lifetime.signal }, deliveryId)).toEqual(bundle)
    expect(dispatch).toHaveBeenCalledTimes(2)
    expect(workWrites()).toEqual(['handoff_current_work'])
    expect(readFileSync(bundle.packagePath)).toEqual(original)

    writeFileSync(bundle.packagePath, 'damaged')
    const damaged = await request(deliver + '/launch', {
      ...launchInput, requestId: 'corrupt-package-retry', retryOf: retry.launchAttemptId,
    })
    expect(damaged.status).toBe(409)
    expect(await damaged.json()).toMatchObject({ code: 'outcome_unknown' })
    expect(await (await request(deliver + '?sessionId=' + source.sessionId)).json()).toMatchObject({
      delivery: { preparationState: 'unknown', receiptAttempt }, launchAttempts: [launched, retry],
    })
    expect(dispatch).toHaveBeenCalledTimes(2)
    expect(workWrites()).toEqual(['handoff_current_work'])
  } finally {
    lifetime.abort()
    http.closeAllConnections()
    await new Promise<void>(resolve => http.close(() => resolve()))
    try { await services?.close() } finally {
      await server.stop()
      rmSync(root, { recursive: true, force: true })
      rmSync(server.home, { recursive: true, force: true })
    }
  }
}, SERVER_E2E_TIMEOUT_MS)
