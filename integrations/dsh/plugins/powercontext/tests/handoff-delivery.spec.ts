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

import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, symlinkSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import type { CallContext, PrepareDeliveryInput, SourceBoundary, VerifiedPreflight } from '../src/handoff/contracts.ts'
import { LocalDeliveryService } from '../src/handoff/delivery.ts'
import type { DeliveryOptions } from '../src/handoff/delivery.ts'
import type { BoundaryReader, WorkEvent } from '../src/handoff/materials.ts'
import { preparedDigest, sha256, wire } from '../src/handoff/wire.ts'
import type { Wire } from '../src/handoff/wire.ts'

const work: Wire<'CurrentWorkHandoff'> = {
  schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input',
  objective: '修复回归 😀',
  state: [{ text: 'The check failed on the selected revision.', basis: 'declared', evidence: [] }],
  disposition: 'continuable',
  next_action: { text: 'Inspect the failing assertion.', basis: 'declared', evidence: [] },
  omissions: ['Exit status unavailable'],
}

const source: SourceBoundary = {
  sessionId: 'session-a', messageId: 'reply-1', throughSeq: 1000, cwd: '/source/worktree',
  workspaceObservedAt: '2026-11-12T03:24:00.000Z',
}

function bindingFor(selected: SourceBoundary, preflightId = 'preflight-a', revision = 1): VerifiedPreflight {
  return { preflightId, revision, source: selected, targetId: 'codex', targetVersion: '1',
    adapterVersion: '1', effectiveCwd: selected.cwd, serverIdentity: 'local-server', connectionRef: 'profile-a',
    connectionGeneration: 2, scopeId: 'scope-a' }
}

function input(overrides: Partial<PrepareDeliveryInput> = {}): PrepareDeliveryInput {
  return { source, preflightId: 'preflight-a', expectedRevision: 1, requestId: 'req-1', workInput: work, ...overrides }
}

const caller = (ownerId = 'owner-a', signal = new AbortController().signal): CallContext => ({ ownerId, signal })

function response(value: unknown, status = 200) {
  return Response.json(value, { status })
}

function statement(text: string, ref: Wire<'SourceReference'>) {
  return { text, citations: [{ kind: 'source', source_ref: ref }] }
}

function responseForCurrent(body: { source_id: string; scope_id: string; handoff: typeof work }) {
  const source = { name: 'content', source_id: body.source_id, locator: { kind: 'sequence', sequence: 2 } }
  return {
    boundary: { kind: 'handoff-boundary', source, position: 2, content_digest: 'sha256:' + 'a'.repeat(64) },
    handoff: {
      schema: 'powercontext.prepared-handoff.v1', scope_id: body.scope_id, base: null, generation: null,
      content: {
        generation: null, schema: 'powercontext.handoff.v1', objective: body.handoff.objective,
        state: body.handoff.state.map(claim => statement(claim.text, wire('SourceReference', source))),
        disposition: body.handoff.disposition,
        next_action: null, omissions: body.handoff.omissions.map(text => ({ text, citation: null })),
      },
    },
  }
}

function deferred<T>() {
  let resolve!: (value: T) => void
  const promise = new Promise<T>(done => { resolve = done })
  return { promise, resolve }
}

describe('local DSH handoff deliveries', () => {
  let parent: string
  let directory: string
  let service: LocalDeliveryService
  const gate = { validateForPrepare: vi.fn(async (
    _ctx: CallContext, { source, preflightId, expectedRevision }: Pick<PrepareDeliveryInput, 'source' | 'preflightId' | 'expectedRevision'>,
  ) => bindingFor(source, preflightId, expectedRevision)) }
  const fetch = vi.fn(async (url: string, request: RequestInit) => {
    expect(url.endsWith('/v1/work/handoffs/prepare-current')).toBe(true)
    return response(responseForCurrent(JSON.parse(request.body as string)))
  })
  const client = new PowerContextClient({ baseUrl: 'http://127.0.0.1:9000', requestTimeoutMs: 5000, fetch })
  const resolveConnection = vi.fn(async () => ({ client }))

  function open(overrides: Partial<DeliveryOptions> = {}) {
    service = new LocalDeliveryService({ directory, preflightGate: gate, resolveConnection, ...overrides })
    return service
  }

  beforeEach(() => {
    parent = mkdtempSync(join(tmpdir(), 'dsh-delivery-'))
    directory = join(parent, 'deliveries')
    gate.validateForPrepare.mockClear()
    fetch.mockClear()
    resolveConnection.mockClear()
  })

  afterEach(async () => {
    try {
      await service?.close()
    } finally {
      rmSync(parent, { recursive: true, force: true })
    }
  })

  it.each(['not_ready', 'stale_preflight', 'binding_mismatch', 'not_found'] as const)(
    'does no capture or generation when the gate rejects with %s', async code => {
      open()
      gate.validateForPrepare.mockRejectedValueOnce(Object.assign(new Error('private remote message'), { code }))
      await expect(service.prepare(caller(), input())).rejects.toMatchObject({ code })
      expect(resolveConnection).not.toHaveBeenCalled()
      expect(fetch).not.toHaveBeenCalled()
      expect(readdirSync(directory).filter(name => !name.startsWith('.'))).toEqual([])
    },
  )

  it('fails closed without a gate and rejects a substituted reply or revision', async () => {
    open({ preflightGate: undefined })
    await expect(service.prepare(caller(), input())).rejects.toMatchObject({ code: 'integration_unavailable' })
    await service.close()
    open()
    gate.validateForPrepare.mockResolvedValueOnce(bindingFor({ ...source, messageId: 'latest' }))
    await expect(service.prepare(caller(), input())).rejects.toMatchObject({ code: 'binding_mismatch' })
    gate.validateForPrepare.mockResolvedValueOnce(bindingFor(source, 'preflight-a', 2))
    await expect(service.prepare(caller(), input())).rejects.toMatchObject({ code: 'stale_preflight' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('rejects input identity overrides, false evidence and over-budget work before dispatch', async () => {
    open()
    for (const workInput of [
      { ...work, scope_id: 'other' },
      { ...work, state: [{ text: 'Unsupported claim', basis: 'verified', evidence: [] }] },
      { ...work, omissions: Array.from({ length: 64 }, (_, i) => '😀'.repeat(200) + i) },
    ]) {
      await expect(service.prepare(caller(), input({ workInput }))).rejects.toMatchObject({ code: 'payload_invalid' })
    }
    expect(fetch).not.toHaveBeenCalled()
  })

  it('persists the entire result, reopens without remote work, and keeps the ACK identity', async () => {
    open()
    expect(() => open()).toThrowError(expect.objectContaining({ code: 'integration_unavailable' }))
    const owner = caller()
    const prepared = await service.prepare(owner, input())
    expect(prepared).toMatchObject({ preparationState: 'prepared', source,
      preview: { objective: work.objective, omissionCount: 1 }, receiptAttempt: { sourceType: 'content' } })
    const first = await service.getForLaunch(owner, prepared.deliveryId)
    const exported = await service.exportPackage(owner, prepared.deliveryId)
    const contents = JSON.parse(Buffer.from(exported.bytes).toString())
    expect(contents).toMatchObject({
      source, binding: bindingFor(source), carrier: { kind: 'prepared', sourceReceipts: [{
        kind: 'handoff-boundary', source: { name: 'content', source_id: 'dsh-delivery:' + prepared.deliveryId }, position: 2,
      }], prepared: { base: null, generation: null, content: { next_action: null, generation: null } } },
    })
    expect(first.packageSha256).toBe(sha256(exported.bytes))
    expect(first.handoffSelection).toEqual({ kind: 'prepared', digest: contents.carrier.digest })
    expect(contents.carrier.digest).not.toBe('sha256:' + first.packageSha256)
    expect(statSync(first.packagePath).mode & 0o777).toBe(0o600)
    expect(statSync(directory).mode & 0o777).toBe(0o700)
    await service.close()
    open()
    expect(await service.getForLaunch(owner, prepared.deliveryId)).toEqual(first)
    expect(await service.prepare(owner, input())).toMatchObject({ deliveryId: prepared.deliveryId })
    expect(await service.cancelPreparation(owner, prepared.deliveryId)).toMatchObject({ preparationState: 'prepared' })
    expect(await service.exportPackage(owner, prepared.deliveryId)).toEqual(exported)
    expect(fetch).toHaveBeenCalledTimes(1)
    await expect(service.getForLaunch(caller('other'), prepared.deliveryId)).rejects.toMatchObject({ code: 'not_found' })
    await expect(service.cancelPreparation(caller('other'), prepared.deliveryId)).rejects.toMatchObject({ code: 'not_found' })
    await expect(service.exportPackage(caller('other'), prepared.deliveryId)).rejects.toMatchObject({ code: 'not_found' })
  })

  it('recovers the verified original package if the Host stopped before publishing prepared', async () => {
    open()
    const owner = caller()
    const ready = await service.prepare(owner, input())
    const first = await service.getForLaunch(owner, ready.deliveryId)
    await service.close()

    // The durable file and its checksum exist, but the last metadata transition did not happen.
    const path = join(directory, ready.deliveryId + '.record.json')
    const staged = JSON.parse(readFileSync(path, 'utf8'))
    writeFileSync(path, JSON.stringify({ ...staged, preparationState: 'preparing' }))
    open()
    expect(await service.getForLaunch(owner, ready.deliveryId)).toEqual(first)
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('isolates sessions and reply boundaries in the same Scope', async () => {
    open()
    const owner = caller()
    const selections = [source, { ...source, sessionId: 'session-b' }, { ...source, messageId: 'reply-2', throughSeq: 1020 }]
    const views = await Promise.all(selections.map((source, i) =>
      service.prepare(owner, input({ source, requestId: 'request-' + i }))))
    expect(new Set(views.map(view => view.deliveryId)).size).toBe(3)
    expect(new Set(views.map(view => view.receiptAttempt.sourceId)).size).toBe(3)
    for (const [i, view] of views.entries()) {
      expect(await service.getForLaunch(owner, view.deliveryId)).toMatchObject({
        source: selections[i], binding: { scopeId: 'scope-a' },
      })
    }
  })

  it('deduplicates concurrent requests and persists an alias for each request key', async () => {
    open()
    const entered = deferred<void>()
    const release = deferred<void>()
    fetch.mockImplementationOnce(async (_url, init) => {
      entered.resolve()
      await release.promise
      return response(responseForCurrent(JSON.parse(init.body as string)))
    })
    const owner = caller()
    const first = service.prepare(owner, input())
    await entered.promise
    try {
      const same = await service.prepare(owner, input())
      const alias = await service.prepare(owner, input({ requestId: 'alias' }))
      expect(same).toMatchObject({ deliveryId: alias.deliveryId, preparationState: 'preparing' })
      await expect(service.getForLaunch(owner, same.deliveryId)).rejects.toMatchObject({ code: 'not_ready' })
      await expect(service.prepare(owner, input({ requestId: 'alias', workInput: undefined })))
        .rejects.toMatchObject({ code: 'idempotency_conflict' })
    } finally {
      release.resolve()
    }
    const view = await first
    await service.close()
    open()
    expect(await service.prepare(owner, input({ requestId: 'alias' }))).toMatchObject({ deliveryId: view.deliveryId })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('preserves an unknown result and only reruns for an explicit new version', async () => {
    open()
    fetch.mockRejectedValueOnce(new Error('connection lost after dispatch'))
    const owner = caller()
    const unknown = await service.prepare(owner, input())
    expect(unknown).toMatchObject({ preparationState: 'unknown', error: { code: 'outcome_unknown' } })
    await service.close()
    open()
    expect(await service.prepare(owner, input({ requestId: 'retry' }))).toMatchObject({
      deliveryId: unknown.deliveryId, preparationState: 'unknown', receiptAttempt: unknown.receiptAttempt,
    })
    await expect(service.prepare(owner, input({ requestId: 'different-preflight', preflightId: 'preflight-b' })))
      .rejects.toMatchObject({ code: 'outcome_unknown' })
    expect(fetch).toHaveBeenCalledTimes(1)
    const replacement = await service.prepare(owner, input({ requestId: 'new', supersedesDeliveryId: unknown.deliveryId }))
    expect(replacement).toMatchObject({ preparationState: 'prepared' })
    expect(replacement.receiptAttempt.sourceId).not.toBe(unknown.receiptAttempt.sourceId)
    expect(await service.get(owner, unknown.deliveryId)).toMatchObject({ preparationState: 'unknown' })
  })

  it.skipIf(Number(process.versions.node.split('.')[0]) < 22).each([
    ['the new Host', process.pid], ['an unrelated live process', process.ppid],
  ])('recovers after a crash when a legacy marker names %s', async (_process, reusedPid) => {
    const url = new URL('../src/handoff/delivery.ts', import.meta.url).href
    const script = `
      import { LocalDeliveryService } from ${JSON.stringify(url)}
      const binding = ${JSON.stringify(bindingFor(source))}
      const service = new LocalDeliveryService({
        directory: process.argv[1],
        preflightGate: { validateForPrepare: async () => binding },
        resolveConnection: async () => ({ client: {
          request() {
            process.stdout.write('dispatched')
            return new Promise(() => setInterval(() => {}, 1000))
          },
        } }),
      })
      await service.prepare(
        { ownerId: 'owner-a', signal: new AbortController().signal },
        ${JSON.stringify(input())},
      )
    `
    const child = spawn(process.execPath, ['--disable-warning=ExperimentalWarning', '--experimental-strip-types', '--input-type=module', '-e', script, directory],
      { stdio: ['ignore', 'pipe', 'pipe'] })
    const exited = once(child, 'exit')
    try {
      const event = await Promise.race([
        once(child.stdout, 'data').then(([data]) => String(data)),
        once(child.stderr, 'data').then(([data]) => String(data)),
        exited.then(([code]) => 'exited: ' + code),
      ])
      expect(event).toBe('dispatched')
      expect(() => open()).toThrowError(expect.objectContaining({ code: 'integration_unavailable' }))
    } finally {
      child.kill('SIGKILL')
      await exited
    }

    // Simulate PID reuse without relying on the kernel to recycle the child PID.
    writeFileSync(join(directory, '.host-' + reusedPid), '', { mode: 0o600 })
    open()
    const owner = caller()
    const [view] = await service.list(owner)
    expect(view).toMatchObject({ preparationState: 'unknown', error: { code: 'outcome_unknown' } })
    expect(await service.prepare(owner, input())).toMatchObject({
      deliveryId: view.deliveryId, receiptAttempt: view.receiptAttempt, preparationState: 'unknown',
    })
    await expect(service.getForLaunch(owner, view.deliveryId)).rejects.toMatchObject({ code: 'outcome_unknown' })
    expect(fetch).not.toHaveBeenCalled()
  })

  it('distinguishes missing input and a definitive rejection from an unknown result', async () => {
    open()
    const owner = caller()
    const needsInput = await service.prepare(owner, input({ workInput: undefined }))
    expect(needsInput).toMatchObject({ preparationState: 'needs_input', recoveryActions: ['supply_work_input', 'create_new_version'] })
    expect(fetch).not.toHaveBeenCalled()
    fetch.mockResolvedValueOnce(response({ error: { message: 'raw Server text' } }, 422))
    const failed = await service.prepare(owner, input({ requestId: 'fixed', supersedesDeliveryId: needsInput.deliveryId }))
    expect(failed).toMatchObject({ preparationState: 'failed', error: { code: 'payload_invalid' } })
    expect(failed.error?.message).not.toContain('raw Server text')
  })

  it('cancels before dispatch without a write and treats an interrupted dispatch as unknown', async () => {
    const entered = deferred<void>()
    const release = deferred<void>()
    open({ resolveConnection: async () => {
      entered.resolve()
      await release.promise
      return { client }
    } })
    const owner = caller()
    const first = service.prepare(owner, input())
    await entered.promise
    try {
      const [preparing] = await service.list(owner)
      expect(await service.cancelPreparation(owner, preparing.deliveryId)).toMatchObject({ preparationState: 'cancelled' })
    } finally {
      release.resolve()
    }
    expect(await first).toMatchObject({ preparationState: 'cancelled' })
    expect(fetch).not.toHaveBeenCalled()
    const dispatched = deferred<void>()
    fetch.mockImplementationOnce((_url, init) => new Promise((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => reject(init.signal!.reason), { once: true })
      dispatched.resolve()
    }))
    const second = service.prepare(owner, input({ requestId: 'new' }))
    await dispatched.promise
    const pending = await service.prepare(owner, input({ requestId: 'new' }))
    expect(await service.cancelPreparation(owner, pending.deliveryId)).toMatchObject({ preparationState: 'unknown' })
    expect(await second).toMatchObject({ preparationState: 'unknown' })
  })

  it.each(['bytes', 'missing', 'symlink'])('refuses launch and export if the saved file is %s', async damage => {
    open()
    const owner = caller()
    const ready = await service.prepare(owner, input())
    const bundle = await service.getForLaunch(owner, ready.deliveryId)
    const bytes = readFileSync(bundle.packagePath)
    rmSync(bundle.packagePath)
    if (damage === 'bytes') writeFileSync(bundle.packagePath, Buffer.concat([bytes, Buffer.from(' ')]), { mode: 0o600 })
    if (damage === 'symlink') {
      const outside = join(parent, 'outside')
      writeFileSync(outside, bytes, { mode: 0o600 })
      symlinkSync(outside, bundle.packagePath)
    }
    await expect(service.getForLaunch(owner, ready.deliveryId)).rejects.toMatchObject({ code: 'outcome_unknown' })
    await expect(service.exportPackage(owner, ready.deliveryId)).rejects.toMatchObject({ code: 'outcome_unknown' })
    expect(await service.get(owner, ready.deliveryId)).toMatchObject({ preparationState: 'unknown' })
  })

  it('checks an explicitly selected exact revision and stores only its reference', async () => {
    open()
    const revision = { family: 'handoff', artifact_id: 'current', revision: 3 }
    fetch.mockImplementationOnce(async (url, init) => {
      expect(url).toContain('/v1/scopes/scope-a/artifacts/handoff/current/revisions/3')
      expect(init.method).toBe('GET')
      return response({ ...revision, scope_id: 'scope-a', content: { schema: 'powercontext.handoff.v1' } })
    })
    const owner = caller()
    const view = await service.prepare(owner, input({ workInput: undefined, revision }))
    const bundle = await service.getForLaunch(owner, view.deliveryId)
    expect(bundle.handoffSelection).toEqual({ kind: 'revision', revision: { ...revision, scope_id: 'scope-a' } })
    const exported = await service.exportPackage(owner, view.deliveryId)
    expect(JSON.parse(Buffer.from(exported.bytes).toString()).carrier).toEqual({ kind: 'revision', revision })
    expect(fetch).toHaveBeenCalledTimes(1)
  })

  it('captures bounded evidence and stores the actual generated receipt and final payload', async () => {
    const calls: string[] = []
    const checks: WorkEvent[] = ['passed', 'failed', 'interrupted', 'not_run', 'unknown'].map((status, seq) => ({
      sessionId: source.sessionId, seq, kind: 'check', command: 'check ' + seq,
      status: status as Extract<WorkEvent, { kind: 'check' }>['status'],
      observedAt: source.workspaceObservedAt, ...(seq === 1 ? { revision: 'old', exitCode: 1 } : {}),
    }))
    let reads = 0
    const readBoundary: BoundaryReader = async (_context, selected, budget) => {
      expect(selected).toEqual(source)
      expect(budget).toEqual({ maxEvents: 511, maxBytes: 6000, maxWorkspaceBytes: 6000 })
      return { source: selected, objective: work.objective,
        selectedReply: { messageId: source.messageId, seq: 999, text: 'The tool results are available.' },
        workspace: { observedAt: source.workspaceObservedAt, revision: 'new', uncommittedChanges: ' M file.ts' },
        events: (function* () {
          for (const check of checks) { reads++; yield check }
          for (let seq = 5; seq < 900; seq++) {
            reads++
            yield { sessionId: source.sessionId, seq, kind: 'completed', text: 'x'.repeat(900) }
          }
        })(),
      }
    }
    let captured: Wire<'CaptureContentSourceResponse'>
    fetch.mockImplementationOnce(async (url, init) => {
      calls.push(url)
      const body = JSON.parse(init.body as string)
      const material = JSON.parse(body.content)
      expect(body.scope_id).toBe('scope-a')
      expect(material.events.slice(0, 5)).toEqual(checks)
      expect(material.workspace).toEqual({ revision: 'new', observedAt: source.workspaceObservedAt, uncommittedChanges: ' M file.ts' })
      expect(material.omissions).toEqual(expect.arrayContaining([expect.stringContaining('event limit'), expect.stringContaining('different revision')]))
      expect(Buffer.byteLength(body.content)).toBeLessThanOrEqual(6000)
      captured = { status: 'accepted', source: { name: 'content', source_id: body.source_id, locator: null }, position: 8 }
      return response(captured, 202)
    }).mockImplementationOnce(async (url, init) => {
      calls.push(url)
      const body = JSON.parse(init.body as string)
      expect(body.evidence).toEqual([{ kind: 'source', source_ref: captured.source }])
      return response({ objective: work.objective, state: [statement('Known failure', captured.source)],
        disposition: 'continuable', next_action: null, omissions: [], generation: { receipt: 'generation-origin' } })
    }).mockImplementationOnce(async (url, init) => {
      calls.push(url)
      const { draft } = JSON.parse(init.body as string)
      return response({ schema: 'powercontext.prepared-handoff.v1', scope_id: 'scope-a', base: null,
        generation: draft.generation, content: { schema: 'powercontext.handoff.v1', objective: draft.objective,
          state: draft.state, disposition: draft.disposition, next_action: draft.next_action, omissions: [] } })
    })
    open({ readBoundary, resolveConnection: async () => ({ client, generation: { maxInputBytes: 6000 } }) })
    const owner = caller()
    const view = await service.prepare(owner, input({ workInput: undefined }))
    expect(view.preparationState).toBe('prepared')
    expect(reads).toBe(511)
    expect(calls.map(url => new URL(url).pathname)).toEqual(['/v1/sources/content', '/v1/handoff/prepare', '/v1/handoff/finalize'])
    const result = JSON.parse(Buffer.from((await service.exportPackage(owner, view.deliveryId)).bytes).toString())
    expect(result.carrier.sourceReceipts).toEqual([captured!])
    expect(result.carrier.prepared.generation.receipt).toBe('generation-origin')
  })

  it.each([16, 17])('discloses truncation of %i Host omissions in the captured source', async count => {
    const omissions = Array.from({ length: count }, (_, i) => 'Host omission ' + (i + 1))
    const readBoundary: BoundaryReader = async (_context, selected) => ({
      source: selected, objective: work.objective,
      selectedReply: { messageId: selected.messageId, seq: selected.throughSeq, text: 'Selected reply' },
      events: [], workspace: { observedAt: selected.workspaceObservedAt, revision: 'abc', uncommittedChanges: '' },
      omissions,
    })
    let captured: unknown
    fetch.mockImplementationOnce(async (_url, init) => {
      captured = JSON.parse(JSON.parse(init.body as string).content)
      return response({}, 422)
    })
    open({ readBoundary, resolveConnection: async () => ({ client, generation: { maxInputBytes: 6000 } }) })
    await service.prepare(caller(), input({ workInput: undefined }))
    expect(captured).toMatchObject({ omissions: [
      'No check evidence was available; whether checks ran is unknown.',
      ...omissions.slice(0, 16),
      ...(count > 16 ? ['Additional omissions reported by the Host could not be included.'] : []),
    ] })
  })

  it('records unavailable evidence without inventing an unrun check or substituting another reply', async () => {
    const reader: BoundaryReader = async (_context, selected) => ({
      source: selected, objective: work.objective,
      selectedReply: { messageId: selected.messageId, seq: selected.throughSeq, text: 'Selected reply' },
      events: [], workspace: null,
    })
    const readBoundary = vi.fn(reader)
    open({ readBoundary, resolveConnection: async () => ({ client, generation: { maxInputBytes: 6000 } }) })
    const owner = caller()
    readBoundary.mockResolvedValueOnce({ ...(await reader(owner, source, {
      maxBytes: 6000, maxWorkspaceBytes: 6000, maxEvents: 511,
    })), selectedReply: { messageId: 'some-other-reply', seq: 999, text: 'Different reply' } })
    expect(await service.prepare(owner, input({ workInput: undefined }))).toMatchObject({
      preparationState: 'failed', error: { code: 'binding_mismatch' },
    })
    expect(fetch).not.toHaveBeenCalled()
    let observed: unknown
    fetch.mockImplementationOnce(async (_url, init) => {
      observed = JSON.parse(JSON.parse(init.body as string).content)
      return response({}, 422)
    })
    await service.prepare(owner, input({ requestId: 'correct-boundary', workInput: undefined }))
    expect(observed).toMatchObject({ events: [], workspace: null, omissions: expect.arrayContaining([
      expect.stringContaining('whether checks ran is unknown'),
      expect.stringContaining('workspace observation'),
    ]) })
  })

  it.each(['check --api_key fixture-value', 'check ' + 'x'.repeat(8192)])(
    'discloses a failed check when its command cannot be captured (%#)', async command => {
      const readBoundary: BoundaryReader = async (_context, selected) => ({
        source: selected, objective: work.objective,
        selectedReply: { messageId: selected.messageId, seq: selected.throughSeq, text: 'Selected reply' },
        events: [{ kind: 'check', sessionId: selected.sessionId, seq: 1, command, status: 'failed', exitCode: 1 }],
        workspace: null,
      })
      open({ readBoundary, resolveConnection: async () => ({ client, generation: { maxInputBytes: 12000 } }) })
      let captured = ''
      fetch.mockImplementationOnce(async (_url, init) => {
        captured = JSON.parse(init.body as string).content
        return response({}, 422)
      })
      await service.prepare(caller(), input({ workInput: undefined }))
      expect(captured).not.toContain(command)
      expect(JSON.parse(captured)).toMatchObject({ omissions: expect.arrayContaining([
        expect.stringContaining('observed check failed'),
      ]) })
    },
  )

  it('protects unconfirmed and pinned packages, and serializes deletion with a path consumer', async () => {
    let now = Date.parse(source.workspaceObservedAt)
    open({ now: () => now, limits: { maxDeliveries: 1 } })
    const owner = caller()
    const first = await service.prepare(owner, input())
    const second = input({ requestId: 'second', source: { ...source, sessionId: 'other-session' } })
    const confirmedAt = now + 1000
    now += 10 * 86_400_000
    await service.prune(owner) // No proof of receipt.
    await service.pin(owner, first.deliveryId, true)
    await service.prune(owner, async () => confirmedAt)
    await expect(service.prepare(owner, second)).rejects.toMatchObject({ code: 'capacity_exceeded' })
    const exported = await service.exportPackage(owner, first.deliveryId)
    const reading = service.getForLaunch(owner, first.deliveryId)
    await expect(service.delete(owner, first.deliveryId)).rejects.toMatchObject({ code: 'not_ready' })
    expect(sha256(readFileSync((await reading).packagePath))).toBe(exported.packageSha256)
    await service.withLaunchBundle(owner, first.deliveryId, async bundle => {
      await expect(service.delete(owner, first.deliveryId)).rejects.toMatchObject({ code: 'not_ready' })
      expect(sha256(readFileSync(bundle.packagePath))).toBe(exported.packageSha256)
    })
    await service.pin(owner, first.deliveryId, false)
    await service.prune(owner, async () => confirmedAt)
    expect(await service.get(owner, first.deliveryId)).toMatchObject({ expired: true })
    await expect(service.getForLaunch(owner, first.deliveryId)).rejects.toMatchObject({ code: 'not_found' })
    expect(await service.prepare(owner, second)).toMatchObject({ preparationState: 'prepared' })
    now += 30 * 86_400_000
    await service.prune(owner)
    await expect(service.get(owner, first.deliveryId)).rejects.toMatchObject({ code: 'not_found' })
  })

  it('rejects a symlinked store and an unsupported envelope without rewriting it', async () => {
    const outside = join(parent, 'outside')
    mkdirSync(outside, { mode: 0o700 })
    symlinkSync(outside, directory)
    expect(() => open()).toThrowError(expect.objectContaining({ code: 'payload_invalid' }))
    rmSync(directory)
    open()
    const result = await service.prepare(caller(), input())
    await service.close()
    const record = join(directory, result.deliveryId + '.record.json')
    const original = readFileSync(record)
    const future = JSON.stringify({ schema: 'powercontext.dsh-delivery.v2' })
    writeFileSync(record, future)
    expect(() => open()).toThrowError(expect.objectContaining({ code: 'payload_invalid' }))
    expect(readFileSync(record, 'utf8')).toBe(future)
    writeFileSync(record, original)
    expect(await open().get(caller(), result.deliveryId)).toMatchObject({ preparationState: 'prepared' })
  })
})

it('matches Python Core digest vectors across wire names, Unicode, ordering and nulls', () => {
  const fixtures = JSON.parse(readFileSync(new URL('./fixtures/handoff-digests.json', import.meta.url), 'utf8'))
  for (const fixture of fixtures) {
    const value = wire('PreparedHandoff', fixture.wire)
    expect(preparedDigest(value)).toBe(fixture.digest)
    const shuffled = Object.fromEntries(Object.entries(fixture.wire).reverse())
    expect(preparedDigest(wire('PreparedHandoff', shuffled))).toBe(fixture.digest)
  }
})
