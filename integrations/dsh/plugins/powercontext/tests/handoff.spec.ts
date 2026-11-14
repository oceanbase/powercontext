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


import { createHash } from 'node:crypto'
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { CallContext, DeliveryService, DeliveryView, LaunchBundle, VerifiedPreflight } from '../src/handoff/contracts.ts'
import { HandoffError } from '../src/handoff/errors.ts'
import { verifyHostSource } from '../src/handoff/host.ts'
import { LaunchService, launchRecords } from '../src/handoff/launch.ts'
import { nativeLaunchUrl } from '../src/handoff/native-launch.ts'
import { PreflightService, preflightRecords } from '../src/handoff/preflight.ts'
import { createHandoffHandler } from '../src/handoff/routes.ts'
import { TargetCatalog, type InstallationResult, type IntegrationCheck, type TargetAdapter, type TargetDetection } from '../src/handoff/targets.ts'

const roots: string[] = []
const cleanups: (() => Promise<void>)[] = []
const localFacts = { ssh: false, desktop: true, ownerLocal: undefined }
const sessionId = 'session-a'
const signal = new AbortController().signal
const owner = { ownerId: 'owner', signal }
const outsider = { ownerId: 'different-owner', signal }
const time = '2026-11-12T04:00:00.000Z'
const missing = { state: 'missing', repairable: true } as const

afterEach(async () => {
  await Promise.all(cleanups.splice(0).map(close => close()))
  await Promise.all(roots.splice(0).map(root => rm(root, { recursive: true, force: true })))
})

function waitFor<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(r => { resolve = r })
  return { resolve, promise }
}

async function fixture(options: { maxPreflights?: number; referenced?: ReadonlySet<string>; now?: () => number } = {}) {
  const root = await mkdtemp(join(tmpdir(), 'pc-dsh-handoff-'))
  roots.push(root)
  const cwd = await mkdir(join(root, 'worktree')).then(() => join(root, 'worktree'))
  const source = { sessionId, messageId: 'msg-10', throughSeq: 10, cwd, workspaceObservedAt: time }
  const state = {
    local: true, latest: source.messageId, server: 'server-a', scope: 'scope-a', generation: 101,
    targetScope: 'scope-a', targetServer: 'server-a', targetCwd: cwd, canPrefill: true, targetGeneration: 'route-a',
    integration: { state: 'ready' } as IntegrationCheck,
    detection: { state: 'present', version: '1.2', configurationKey: 'same-real-config' } as TargetDetection,
  }
  const install = vi.fn(async (): Promise<InstallationResult> => {
    state.integration = { state: 'ready' }
    return { state: 'completed' }
  })
  const adapter: TargetAdapter = {
    id: 'codex-desktop', version: '1', qualification: { platform: 'darwin', evidence: 'test double' },
    detect: async () => state.detection, checkIntegration: async () => state.integration,
    installIntegration: install, resolveEnvironment: async () => ({
      effectiveCwd: state.targetCwd, serverIdentity: state.targetServer, scopeId: state.targetScope,
      newSession: true, unsentPrefill: state.canPrefill, packageAccess: true, configurationGeneration: state.targetGeneration,
    }),
    canReadPackage: vi.fn(async () => true),
    launchIntent: (_target, bundle) => ({ draft: `Read ${bundle.packagePath} and use ${bundle.receiptAttempt.sourceId}` }),
  }
  const catalog = new TargetCatalog([adapter], 'darwin')
  const authority = {
    requireLocal: () => { if (!state.local) throw new HandoffError('deployment_unsupported') },
    verifySource: async (context: CallContext, selected: typeof source, latest: boolean) => {
      if (context.ownerId !== owner.ownerId || selected.sessionId !== source.sessionId) throw new HandoffError('not_found')
      if (selected.cwd !== source.cwd || (latest && state.latest !== selected.messageId)) throw new HandoffError('stale_preflight')
    },
    sourceConnection: async () => ({
      scopeId: state.scope, serverIdentity: state.server, connectionRef: 'main', connectionGeneration: state.generation,
    }),
  }
  const records = preflightRecords(join(root, 'preflights'), options.maxPreflights)
  const newPreflight = () => new PreflightService({ authority, records, catalog, signal, referencedPreflightIds: () => options.referenced ?? new Set(), now: options.now })
  const preflight = newPreflight()
  const create = (requestId = 'preflight-request') => preflight.create(owner, { requestId, source, targetId: adapter.id })
  const decide = (item: Awaited<ReturnType<typeof create>>, action: 'install' | 'decline' | 'verify', confirmOriginal = false) =>
    preflight.decide(owner, item.preflightId, sessionId, { expectedRevision: item.revision, action, confirmOriginal })
  const validate = (item: Awaited<ReturnType<typeof create>>, context = owner, boundary = source) =>
    preflight.validateForPrepare(context, { preflightId: item.preflightId, expectedRevision: item.revision, source: boundary })

  const recordsLaunch = launchRecords(join(root, 'launch-attempts'))
  const packagePath = join(root, 'package.json')
  const receiptAttempt = { sourceType: 'content', sourceId: 'ack-1', receiptAttemptId: 'receipt-1' }
  const checksum = (bytes: Buffer) => createHash('sha256').update(bytes).digest('hex')
  const id = 'delivery-1'
  let bundle: LaunchBundle | undefined
  let prepared: DeliveryView | undefined
  const setDelivery = async (binding: VerifiedPreflight) => {
    // This is deliberately a service double. Task 3 alone defines carrier integrity and persistence.
    const bytes = Buffer.from('saved bytes from the delivery service double')
    await writeFile(packagePath, bytes, { mode: 0o600 })
    bundle = { deliveryId: id, source, binding, packagePath, packageSha256: checksum(bytes),
      receiptAttempt, handoffSelection: { kind: 'prepared', digest: 'digest-from-task-3' } }
    prepared = { deliveryId: id, source, binding, receiptAttempt, preparationState: 'prepared',
      createdAt: time, updatedAt: time, recoveryActions: [], pinned: false, expired: false } as DeliveryView
    return bundle
  }
  const get = async (context: CallContext, deliveryId: string) => {
    if (context.ownerId !== owner.ownerId || deliveryId !== id || !prepared) throw new HandoffError('not_found')
    return prepared
  }
  const getForLaunch = async (context: CallContext, deliveryId: string) => {
    if (!bundle) throw new HandoffError('not_ready')
    await get(context, deliveryId)
    const bytes = await readFile(bundle.packagePath).catch(() => undefined)
    if (!bytes || checksum(bytes) !== bundle.packageSha256) throw new HandoffError('payload_invalid')
    return bundle
  }
  const delivery = {
    get: vi.fn(get), getForLaunch: vi.fn(getForLaunch),
    prepare: vi.fn<DeliveryService['prepare']>(async (context, input) => {
      await setDelivery(await preflight.validateForPrepare(context, input))
      return get(context, id)
    }),
    exportPackage: vi.fn<DeliveryService['exportPackage']>(async (context, deliveryId) => {
      const saved = await getForLaunch(context, deliveryId)
      return { bytes: await readFile(saved.packagePath), mediaType: 'application/json', filename: 'delivery.json',
        packageSha256: saved.packageSha256 }
    }),
    cancelPreparation: vi.fn(get),
  } satisfies DeliveryService
  const dispatch = vi.fn<() => Promise<'opened' | 'failed' | 'unknown'>>(async () => 'opened')
  const plan = vi.fn(() => ({ dispatch }))
  const newLaunch = (gate = newPreflight()) => new LaunchService({
    delivery, preflight: gate, records: recordsLaunch, requireLocal: authority.requireLocal, plan, signal,
  })
  const launch = newLaunch(preflight)
  const launchInput = { targetId: adapter.id, sessionId, requestId: 'launch-1' }
  const ready = async () => setDelivery((await create()).binding!)
  return {
    root, cwd, source, state, install, adapter, catalog, preflight, newPreflight, create, decide, validate,
    records, launch, newLaunch, recordsLaunch, delivery, dispatch, plan, ready, id, launchInput, packagePath, receiptAttempt,
  }
}

describe('preflight and installation', () => {
  it('distinguishes absent applications and absent integration; declining is repeatable', async () => {
    const f = await fixture()
    const unavailable = new TargetCatalog().list()
    expect(unavailable).toHaveLength(16)
    expect(unavailable.every(entry => entry.qualification === null && entry.availability === 'unavailable')).toBe(true)
    f.state.detection = { state: 'missing' }
    expect(await f.create('absent')).toMatchObject({ state: 'needs_user_action', agent: 'missing', nextAction: 'install_agent' })
    f.state.detection = { state: 'present', version: '1.2', configurationKey: 'same-real-config' }
    f.state.integration = missing
    const pending = await f.create('missing')
    expect(pending).toMatchObject({ state: 'needs_install_consent', integration: 'missing' })
    await expect(f.validate(pending)).rejects.toMatchObject({ code: 'not_ready' })
    const cancelled = await f.decide(pending, 'decline')
    expect(cancelled.state).toBe('cancelled')
    expect(await f.decide(pending, 'decline')).toEqual(cancelled)
    expect(f.install).not.toHaveBeenCalled()
  })

  it('joins one installation for the same actual configuration and verifies each selection', async () => {
    const f = await fixture()
    f.state.integration = missing
    const [a, b] = await Promise.all([f.create('a'), f.create('b')])
    const started = waitFor<void>()
    const done = waitFor<InstallationResult>()
    f.install.mockImplementationOnce(() => { started.resolve(); return done.promise })
    const one = f.decide(a, 'install')
    const duplicate = f.decide(a, 'install')
    const two = f.decide(b, 'install')
    await started.promise
    expect(f.preflight.get(owner, a.preflightId, sessionId).state).toBe('installing')
    f.state.integration = { state: 'ready' }
    done.resolve({ state: 'completed' })
    const [first, again, second] = await Promise.all([one, duplicate, two])
    expect(first).toMatchObject({ state: 'ready', integration: 'ready' })
    expect(again).toEqual(first)
    expect(second).toMatchObject({ state: 'ready' })
    expect(f.install).toHaveBeenCalledTimes(1)
    expect(await f.validate(first)).toEqual(first.binding)
    expect(f.newPreflight().get(owner, first.preflightId, sessionId)).toEqual(first)
  })

  it('does not accept an intermediate ready probe while a shared install can still fail', async () => {
    const f = await fixture()
    f.state.integration = missing
    const [a, b] = await Promise.all([f.create('a'), f.create('b')])
    const started = waitFor<void>()
    const done = waitFor<InstallationResult>()
    f.install.mockImplementationOnce(() => {
      f.state.integration = { state: 'ready' }
      started.resolve()
      return done.promise
    })
    const first = f.decide(a, 'install')
    await started.promise
    const second = f.decide(b, 'install')
    await new Promise<void>(resolve => setImmediate(resolve))
    const during = f.preflight.get(owner, b.preflightId, sessionId)
    done.resolve({ state: 'failed' })

    expect(during.state).toBe('installing')
    for (const result of await Promise.all([first, second])) {
      expect(result).toMatchObject({ state: 'failed', installation: 'failed' })
      await expect(f.validate(result)).rejects.toMatchObject({ code: 'not_ready' })
    }
  })

  it('does not publish fresh readiness or permit an older binding while an installer is unsettled', async () => {
    const f = await fixture()
    const ready = await f.create('previous-ready')
    await f.ready()
    f.state.integration = missing
    const consent = await f.create('consent')
    const started = waitFor<void>()
    const done = waitFor<InstallationResult>()
    f.install.mockImplementationOnce(() => {
      f.state.integration = { state: 'ready' }
      started.resolve()
      return done.promise
    })
    const installing = f.decide(consent, 'install')
    await started.promise
    const inspecting = f.create('fresh')
    const completedEarly = vi.fn()
    void inspecting.then(completedEarly)
    try {
      await expect(f.validate(ready)).rejects.toMatchObject({ code: 'not_ready' })
      await expect(f.launch.launch(owner, f.id, f.launchInput)).rejects.toMatchObject({ code: 'not_ready' })
      expect(f.dispatch).not.toHaveBeenCalled()
      expect(completedEarly).not.toHaveBeenCalled()
    } finally {
      done.resolve({ state: 'failed' })
    }
    expect(await installing).toMatchObject({ state: 'failed', installation: 'failed' })
    expect(await inspecting).toMatchObject({ state: 'failed', installation: 'failed' })
    await expect(f.validate(ready)).rejects.toMatchObject({ code: 'not_ready' })
    expect((await f.create('freshly-reverified')).state).toBe('ready')
  })

  it('reclaims only unreferenced terminal preflights under pressure, including after restart', async () => {
    const referenced = new Set<string>()
    let now = Date.parse(time)
    const f = await fixture({ maxPreflights: 5, referenced, now: () => now })
    const ready = await f.create('saved-delivery')
    referenced.add(ready.preflightId)
    const cancelled = await f.preflight.cancel(owner, ready.preflightId, sessionId)
    const unused = await f.create('unused-ready')
    f.state.detection = { state: 'unknown' }
    const unknown = await f.create('unresolved')
    f.state.detection = { state: 'present', version: '1.2', configurationKey: 'same-real-config' }
    f.state.integration = missing
    const active = await f.create('waiting-for-consent')
    const declined = await f.decide(await f.create('declined'), 'decline')
    const restarted = f.newPreflight()
    const make = (requestId: string) => restarted.create(owner, { requestId, source: f.source, targetId: f.adapter.id })
    const recreated = await make('declined')
    expect(recreated.state).toBe('cancelled') // An in-retention replay keeps its original result.
    const afterPressure = await make('new-request')
    expect(afterPressure.state).toBe('needs_install_consent')
    expect(() => restarted.get(owner, declined.preflightId, sessionId)).toThrow(HandoffError)
    expect(restarted.get(owner, active.preflightId, sessionId).state).toBe('needs_install_consent')
    expect(restarted.get(owner, unknown.preflightId, sessionId).state).toBe('unknown')
    expect(restarted.get(owner, cancelled.preflightId, sessionId).binding).toEqual(ready.binding)
    await expect(make('still-full')).rejects.toMatchObject({ code: 'capacity_exceeded' })
    now += 31 * 86400_000
    const retry = await make('declined')
    expect(retry.preflightId).not.toBe(declined.preflightId)
    expect(() => restarted.get(owner, unused.preflightId, sessionId)).toThrow(HandoffError)
    expect(restarted.get(owner, cancelled.preflightId, sessionId).binding).toEqual(ready.binding)
  })

  it('handles install failure, a restart requirement and post-install verification', async () => {
    const f = await fixture()
    f.state.integration = missing
    f.install.mockResolvedValueOnce({ state: 'failed' })
    const failure = await f.decide(await f.create('fail'), 'install')
    expect(failure.state).toBe('failed')
    f.install.mockImplementationOnce(async () => {
      f.state.integration = { state: 'needs_user_action', action: 'restart_target' }
      return { state: 'completed' }
    })
    const restart = await f.decide(await f.create('restart'), 'install')
    expect(restart).toMatchObject({ state: 'needs_user_action', nextAction: 'restart_target' })
    f.state.integration = { state: 'ready' }
    expect(await f.decide(restart, 'verify')).toMatchObject({ state: 'ready' })
    expect(f.install).toHaveBeenCalledTimes(2)
  })

  it('never silently selects a newer response when installation completes', async () => {
    const f = await fixture()
    f.state.integration = missing
    f.install.mockImplementationOnce(async () => {
      f.state.latest = 'different-reply'
      f.state.integration = { state: 'ready' }
      return { state: 'completed' }
    })
    const changed = await f.decide(await f.create(), 'install')
    expect(changed).toMatchObject({ state: 'needs_user_action', nextAction: 'reselect_source', source: f.source })
    expect((await f.decide(changed, 'verify')).state).toBe('needs_user_action')
    const afterRead = f.preflight.get(owner, changed.preflightId, sessionId)
    const original = await f.decide(afterRead, 'verify', true)
    expect(original).toMatchObject({ state: 'ready', source: f.source })
    expect((await f.validate(original)).source).toEqual(f.source)
  })

  it('cancels continuation without undoing a completed shared installation', async () => {
    const f = await fixture()
    f.state.integration = missing
    const pending = await f.create()
    const started = waitFor<void>()
    const done = waitFor<InstallationResult>()
    f.install.mockImplementationOnce(() => { started.resolve(); return done.promise })
    const installing = f.decide(pending, 'install')
    await started.promise
    expect(f.preflight.cancel(owner, pending.preflightId, sessionId).state).toBe('cancelled')
    f.state.integration = { state: 'ready' }
    done.resolve({ state: 'completed' })
    expect(await installing).toMatchObject({ state: 'cancelled', installation: 'completed' })
    expect(f.newPreflight().get(owner, pending.preflightId, sessionId))
      .toMatchObject({ state: 'cancelled', installation: 'completed' })
    expect((await f.create('after-cancel')).state).toBe('ready')
    expect(f.install).toHaveBeenCalledTimes(1)
  })

  it('uses trusted directory, Server, Scope and configuration at creation and before prepare', async () => {
    const f = await fixture()
    f.state.targetScope = 'wrong-scope'
    expect(await f.create('wrong-scope')).toMatchObject({ state: 'failed', code: 'binding_mismatch' })
    f.state.targetScope = f.state.scope
    const ready = await f.create()
    expect(ready).toMatchObject({ state: 'ready', binding: { scopeId: 'scope-a', serverIdentity: 'server-a' } })
    expect((await f.create()).revision).toBe(ready.revision)
    await expect(f.create()).resolves.toEqual(ready)
    await expect(f.validate(ready, outsider)).rejects.toMatchObject({ code: 'not_found' })
    await expect(f.validate(ready, owner, { ...f.source, messageId: 'different' })).rejects.toMatchObject({ code: 'stale_preflight' })
    await expect(f.preflight.create(owner, { requestId: 'preflight-request', source: { ...f.source, messageId: 'different' },
      targetId: f.adapter.id })).rejects.toMatchObject({ code: 'idempotency_conflict' })
    f.state.generation += 1
    await expect(f.validate(ready)).rejects.toMatchObject({ code: 'binding_mismatch' })
    f.state.generation -= 1
    f.state.targetGeneration = 'route-b'
    await expect(f.validate(ready)).rejects.toMatchObject({ code: 'binding_mismatch' })
    f.state.targetGeneration = 'route-a'
    await rename(f.cwd, join(f.root, 'old-directory'))
    await mkdir(f.cwd)
    await expect(f.validate(ready)).rejects.toMatchObject({ code: 'binding_mismatch' })
    f.state.local = false
    await expect(f.create('remote')).rejects.toMatchObject({ code: 'deployment_unsupported' })
  })
})

describe('launch and recovery', () => {
  it('requires the saved delivery, checks it again after target checks, and preserves its binding', async () => {
    const f = await fixture()
    const run = () => f.launch.launch(owner, f.id, f.launchInput)
    await expect(run()).rejects.toMatchObject({ code: 'not_ready' })
    const saved = await f.ready()
    await expect(f.launch.launch(outsider, f.id, f.launchInput)).rejects.toMatchObject({ code: 'not_found' })
    await expect(f.launch.launch(owner, f.id, { ...f.launchInput, sessionId: 'session-b' }))
      .rejects.toMatchObject({ code: 'not_found' })
    f.state.targetServer = 'different-server'
    await expect(run()).rejects.toMatchObject({ code: 'binding_mismatch' })
    f.state.targetServer = f.state.server
    vi.mocked(f.adapter.canReadPackage).mockImplementationOnce(async () => {
      await writeFile(f.packagePath, 'corrupted during the check')
      return true
    })
    await expect(run()).rejects.toMatchObject({ code: 'payload_invalid' })
    expect(f.dispatch).not.toHaveBeenCalled()
    expect(f.launch.list(owner, f.id, sessionId)).toEqual([])
    expect(saved).toMatchObject({ source: f.source, binding: { serverIdentity: 'server-a', scopeId: 'scope-a' } })
  })

  it('durably records requested before dispatch; overlapping calls and retries cannot repeat it', async () => {
    const f = await fixture()
    await f.ready()
    f.state.latest = 'later-response-does-not-replace-the-delivery'
    const issued = waitFor<void>()
    const done = waitFor<'opened'>()
    f.dispatch.mockImplementationOnce(() => { issued.resolve(); return done.promise })
    const first = f.launch.launch(owner, f.id, f.launchInput)
    const duplicate = f.launch.launch(owner, f.id, f.launchInput)
    await expect(f.launch.launch(owner, f.id, { ...f.launchInput, requestId: 'another-click' }))
      .rejects.toMatchObject({ code: 'idempotency_conflict' })
    await issued.promise
    const [requested] = f.launch.list(owner, f.id, sessionId)
    expect(requested.state).toBe('requested')
    expect(f.newLaunch().list(owner, f.id, sessionId)).toEqual([expect.objectContaining({
      state: 'unknown', launchAttemptId: requested.launchAttemptId,
    })])
    expect((await f.launch.cancelPending(owner, f.id, sessionId))[0].state).toBe('requested')
    done.resolve('opened')
    const opened = await first
    expect(await duplicate).toEqual(opened)
    expect(opened).toMatchObject({ state: 'opened', launchAttemptId: requested.launchAttemptId })
    expect(await f.newLaunch().launch(owner, f.id, f.launchInput)).toEqual(opened)
    expect(f.dispatch).toHaveBeenCalledTimes(1)
  })

  it('restarts with the same unknown attempt, and an explicit retry reuses the original package and receipt', async () => {
    const f = await fixture()
    const bundle = await f.ready()
    f.dispatch.mockResolvedValueOnce('unknown')
    const unknown = await f.launch.launch(owner, f.id, f.launchInput)
    const restarted = f.newLaunch()
    expect(unknown).toMatchObject({ state: 'unknown', code: 'outcome_unknown' })
    expect(await restarted.launch(owner, f.id, f.launchInput)).toEqual(unknown)
    await expect(restarted.launch(owner, f.id, { ...f.launchInput, requestId: 'new-request' }))
      .rejects.toMatchObject({ code: 'outcome_unknown' })
    const retried = await restarted.launch(owner, f.id, {
      ...f.launchInput, requestId: 'explicit-retry', retryOf: unknown.launchAttemptId,
    })
    expect(retried.state).toBe('opened')
    expect(retried.launchAttemptId).not.toBe(unknown.launchAttemptId)
    const expectedDraft = expect.objectContaining({ draft: expect.stringContaining(bundle.receiptAttempt.sourceId) })
    expect(f.plan).toHaveBeenNthCalledWith(1, f.adapter.id, bundle.binding.effectiveCwd, expectedDraft)
    expect(f.plan).toHaveBeenNthCalledWith(2, f.adapter.id, bundle.binding.effectiveCwd, expectedDraft)
    expect(f.delivery.prepare).not.toHaveBeenCalled()
    expect(f.dispatch).toHaveBeenCalledTimes(2)
  })

  it('cancels only undispatched work and keeps the cancelled attempt after restart', async () => {
    const f = await fixture()
    await f.ready()
    const checking = waitFor<void>()
    const proceed = waitFor<boolean>()
    vi.mocked(f.adapter.canReadPackage).mockImplementationOnce(() => { checking.resolve(); return proceed.promise })
    const opening = f.launch.launch(owner, f.id, f.launchInput)
    await checking.promise
    const cancellation = f.launch.cancelPending(owner, f.id, sessionId)
    proceed.resolve(true)
    const attempt = await opening
    expect(attempt.state).toBe('cancelled')
    expect(await cancellation).toEqual([attempt])
    expect(f.newLaunch().list(owner, f.id, sessionId)).toEqual([attempt])
    expect(f.dispatch).not.toHaveBeenCalled()
  })

  it('encodes only the fixed new-session destination fields without truncating', () => {
    const cwd = '/tmp/a b & /字'
    const draft = 'Review & continue\n请检查'
    for (const [id, protocol, host, path, cwdKey, draftKey] of [
      ['codex-desktop', 'codex:', 'threads', '/new', 'path', 'prompt'],
      ['claude-desktop', 'claude:', 'code', '/new', 'folder', 'q'],
      ['claude-code-cli', 'claude-cli:', 'open', '', 'cwd', 'q'],
    ] as const) {
      const url = new URL(nativeLaunchUrl(id, cwd, { draft }))
      expect([url.protocol, url.host, url.pathname]).toEqual([protocol, host, path])
      expect(Object.fromEntries(url.searchParams)).toEqual({ [cwdKey]: cwd, [draftKey]: draft })
    }
    expect(() => nativeLaunchUrl('codex-desktop', cwd, { draft: '字'.repeat(1000) })).toThrow(HandoffError)
  })

  it('allows ordinary credential terminology in a draft', () => {
    const draft = 'Do not put api_key, sk- or BEGIN PRIVATE in a URL.'
    const url = nativeLaunchUrl('codex-desktop', '/tmp/workspace', { draft })
    expect(new URL(url).searchParams.get('prompt')).toBe(draft)
  })

  it.each([
    ['Authorization: Bearer fixture.value.signature', undefined],
    ['ghp_' + 'fixture12345', undefined],
    ['xoxb-' + 'fixture12345', undefined],
    [['-----BEGIN', 'RSA', 'PRIVATE KEY-----'].join(' '), undefined],
    ['The Server secret is a-fixture-only-value', 'Bearer a-fixture-only-value'],
  ])('rejects likely secrets from native launch URLs (%#)', (draft, authorization) => {
    expect(() => nativeLaunchUrl('codex-desktop', '/tmp/workspace', { draft }, authorization))
      .toThrow(expect.objectContaining({ code: 'secret_rejected', status: 400 }))
  })
})

describe('Host routing and authorization', () => {
  async function http(f: Awaited<ReturnType<typeof fixture>>, { unavailable = false, remote = false } = {}) {
    const services = vi.fn(() => unavailable ? {} : { delivery: f.delivery, preflight: f.preflight, launch: f.launch })
    const handler = createHandoffHandler({
      facts: remote ? { ...localFacts, ssh: true } : localFacts,
      requireLocal() {},
      requestRejection: req => req.headers['x-session-auth'] ? undefined : 401,
      authenticatedOwner: req => req.headers['x-session-auth'] === 'valid' ? owner.ownerId : outsider.ownerId,
      catalog: unavailable ? new TargetCatalog() : f.catalog, signal, services,
    })
    const server = createServer(handler)
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
    cleanups.push(async () => { server.closeAllConnections(); await new Promise<void>(resolve => server.close(() => resolve())) })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Missing HTTP listener')
    const origin = `http://127.0.0.1:${address.port}`
    const request = (path: string, value?: unknown, headers: Record<string, string> = {}) =>
      fetch(origin + '/powercontext' + path, {
        ...(value === undefined ? {} : { method: 'POST', body: JSON.stringify(value) }),
        headers: { 'content-type': 'application/json', 'x-session-auth': 'valid', ...headers },
      })
    return { request, services }
  }

  it('gates Host authority before service access and cannot advertise or fake an unavailable integration', async () => {
    const f = await fixture()
    const { request, services } = await http(f, { unavailable: true })
    const prepare = { source: f.source, requestId: 'r', preflightId: 'preflight-id', expectedRevision: 1 }
    expect((await request('/targets', undefined, { 'x-session-auth': '' })).status).toBe(401)
    expect((await request('/deliveries', prepare, { origin: 'https://other.example' })).status).toBe(403)
    expect((await request('/targets', undefined, { forwarded: 'for=192.0.2.1' })).status).toBe(403)
    const remote = await http(f, { remote: true })
    expect((await remote.request('/targets')).status).toBe(403)
    expect(services).not.toHaveBeenCalled()
    const targets = await (await request('/targets')).json()
    expect(targets.targets.every((target: { availability: string }) => target.availability === 'unavailable')).toBe(true)
    expect(await (await request('/deliveries', prepare)).json()).toMatchObject({ code: 'integration_unavailable' })
    expect(f.delivery.prepare).not.toHaveBeenCalled()
    expect(f.dispatch).not.toHaveBeenCalled()
  })

  it('translates explicit operations, checks session selectors, and reads separate states with no GET side effects', async () => {
    const f = await fixture()
    const { request } = await http(f)
    const ready = await (await request('/preflights', { requestId: '1', source: f.source, targetId: f.adapter.id })).json()
    expect(ready.state).toBe('ready')
    const body = { requestId: '2', preflightId: ready.preflightId, expectedRevision: ready.revision,
      source: f.source, workInput: { objective: 'forwarded without rewriting' } }
    expect((await request('/deliveries', { ...body, ownerId: 'other' })).status).toBe(400)
    expect((await request('/deliveries', { ...body, workInput: 'a'.repeat(65 * 1024) })).status).toBe(400)
    const prepared = await (await request('/deliveries', body)).json()
    expect(prepared).toMatchObject({ preflight: { state: 'ready' }, delivery: { preparationState: 'prepared' }, launchAttempts: [] })
    expect(f.delivery.prepare.mock.calls[0]?.[1].workInput).toEqual(body.workInput)
    expect((await request(`/deliveries/${f.id}?sessionId=other`)).status).toBe(404)
    expect((await request(`/deliveries/${f.id}/export?sessionId=${sessionId}`, undefined, { 'x-session-auth': 'other' })).status).toBe(404)
    const exported = await request(`/deliveries/${f.id}/export?sessionId=${sessionId}`)
    expect(exported.headers.get('content-disposition')).toMatch(/^attachment/)
    expect(await exported.text()).toEqual(await readFile(f.packagePath, 'utf8'))
    expect((await request(`/deliveries/${f.id}/launch?sessionId=${sessionId}`)).status).toBe(405)
    expect((await request(`/deliveries/${f.id}/launch`, { ...f.launchInput, uri: 'file:///tmp' })).status).toBe(400)
    expect(f.dispatch).not.toHaveBeenCalled()
    expect(await (await request(`/deliveries/${f.id}/launch`, f.launchInput)).json()).toMatchObject({ state: 'opened' })
    const cancelled = await (await request(`/deliveries/${f.id}/cancel`, { sessionId })).json()
    expect(cancelled).toMatchObject({ delivery: { preparationState: 'prepared' }, launchAttempts: [{ state: 'opened' }] })
    expect(f.delivery.cancelPreparation).toHaveBeenCalledOnce()
  })

  it('uses the effective Host directory and exact completed response, even if the initial cwd is different', async () => {
    const f = await fixture()
    const events = [
      { type: 'assistant/message', time: Date.parse(time), seq: 8,
        data: { turn: 1, message: { id: f.source.messageId, content: [{ type: 'text', text: 'finished' }] } } },
      { type: 'step/end', time: Date.parse(time), seq: 9, data: { turn: 1 } },
      { type: 'turn/end', time: Date.parse(time), seq: 10, data: { turn: 1, reason: { kind: 'completed' } } },
    ]
    const session = { header: { id: sessionId, cwd: '/different' }, seq: 11, snapshotEvents: () => events }
    const host = { get: ((key: string) => key === 'sessions' ? { get: (id: string) => id === sessionId ? session : undefined }
      : key === 'workingDirectory' ? { get: () => f.cwd } : undefined) } as Parameters<typeof verifyHostSource>[0]
    expect(() => verifyHostSource(host, owner, f.source, true)).not.toThrow()
    expect(() => verifyHostSource(host, owner, { ...f.source, cwd: '/different' }, true)).toThrow(HandoffError)
    expect(() => verifyHostSource(host, owner, { ...f.source, workspaceObservedAt: '2026-11-10' }, true)).toThrow(HandoffError)
    expect(() => verifyHostSource(host, owner, { ...f.source, messageId: 'fake-reply' }, true)).toThrow(HandoffError)
    events[2]!.data.reason!.kind = 'aborted'
    expect(() => verifyHostSource(host, owner, f.source, true)).toThrow(HandoffError)
  })
})
