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
import { mkdtempSync, readdirSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import type { CallContext, DeliveryView, LaunchBundle } from '../src/handoff/contracts.ts'
import { LocalReceiptReader } from '../src/handoff/receipt.ts'
import { sha256 } from '../src/handoff/wire.ts'

const source = { sessionId: 's', messageId: 'm', throughSeq: 1, cwd: '/a', workspaceObservedAt: '2026-11-13T00:00:00Z' }
const attempt = '5e4e5cc7-6067-4c76-a168-d823a1692adf'
const bundle: LaunchBundle = { deliveryId: 'delivery', source, packagePath: '/tmp/package.json', packageSha256: 'a'.repeat(64),
  binding: { source, preflightId: 'p', revision: 1, targetId: 't', targetVersion: '1', adapterVersion: '1', effectiveCwd: '/a', serverIdentity: 'server', connectionRef: 'ref', connectionGeneration: 1, scopeId: 'scope' },
  handoffSelection: { kind: 'prepared', digest: 'sha256:' + 'b'.repeat(64) },
  receiptAttempt: { receiptAttemptId: attempt, sourceType: 'content', sourceId: 'dsh-ack:' + attempt } }
const context: CallContext = { ownerId: 'owner', signal: new AbortController().signal }
const view: DeliveryView = { deliveryId: bundle.deliveryId, source, binding: bundle.binding, receiptAttempt: bundle.receiptAttempt,
  preparationState: 'prepared', createdAt: '2026-11-13T00:00:00Z', updatedAt: '2026-11-13T00:00:00Z', pinned: false, expired: true, recoveryActions: [] }
const dirs: string[] = []
afterEach(() => { for (const dir of dirs.splice(0)) rmSync(dir, { recursive: true, force: true }) })
function receipt(overrides: Record<string, unknown> = {}) {
  return { schema: 'powercontext.handoff-receipt.v1', trust: 'untrusted_observation', receiver: 'alice', status: 'accepted',
    selection: 'prepared', selected_revision: null, prepared_digest: 'sha256:' + 'b'.repeat(64),
    receiver_checks: { live_state: 'confirmed', capability: 'confirmed', authorization: 'confirmed' },
    evidence_status: 'available', unavailable_evidence: [], message: null, ...overrides }
}
function sourceRecord(payload = receipt(), overrides: Record<string, unknown> = {}) {
  const content = JSON.stringify(payload)
  return { scope_id: 'scope', source_type: 'content', source_id: bundle.receiptAttempt.sourceId,
    position: 1, handoff_receipt: true, receipt_identity: null, content, content_digest: 'sha256:' + sha256(JSON.stringify(content)), ...overrides }
}
function fixture(mode: 'disabled' | 'enforced' = 'disabled') {
  const directory = join(mkdtempSync(join(tmpdir(), 'receipt-')), 'observations')
  dirs.push(join(directory, '..'))
  let response: Response | Error = Response.json(sourceRecord())
  const client = new PowerContextClient({ baseUrl: 'http://127.0.0.1:9123', requestTimeoutMs: 5000,
    fetch: async (url) => { expect(url).toContain('/v1/scopes/scope/sources/content/dsh-ack%3A'); if (response instanceof Error) throw response; return response.clone() } })
  let expectedPrincipal: { type: string; id: string } | undefined
  let now = Date.parse('2026-11-13T00:00:00Z')
  const options = { directory, resolveConnection: async () => ({ client, accessMode: mode, expectedPrincipal }), now: () => now }
  return { directory, reader: new LocalReceiptReader(options), reopen: () => new LocalReceiptReader(options), set: (value: Response | Error) => { response = value },
    expectPrincipal: (value: { type: string; id: string }) => { expectedPrincipal = value }, advance: (ms: number) => { now += ms } }
}

describe('exact receipt observation', () => {
  it('persists verified attribution and preserves it across read failures and restart', async () => {
    const f = fixture()
    const verified = await f.reader.read(context, bundle)
    expect(verified).toMatchObject({ state: 'accepted', readState: 'verified', attribution: { kind: 'self_reported', receiver: 'alice' } })
    f.set(new Error('offline'))
    const reopened = f.reopen()
    expect(await reopened.read(context, bundle)).toMatchObject({ state: 'accepted', readState: 'unavailable', error: 'read_unavailable', confirmedAt: verified.confirmedAt })
    for (const [status, readState] of [[404, 'not_found'], [403, 'forbidden']] as const) {
      f.set(Response.json({}, { status }))
      expect(await reopened.read(context, bundle)).toMatchObject({ state: 'accepted', readState, confirmedAt: verified.confirmedAt })
    }
    expect(await reopened.confirmedAt(context, bundle)).toBe(Date.parse(verified.confirmedAt!))
    expect(await reopened.confirmedAt({ ...context, ownerId: 'other' }, bundle)).toBeUndefined()
  })
  it('separates absent, forbidden and unavailable reads from receiver decisions', async () => {
    const f = fixture()
    for (const [status, state] of [[404, 'not_found'], [403, 'forbidden'], [503, 'unavailable']] as const) {
      f.set(Response.json({}, { status }))
      expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'awaiting', readState: state })
    }
  })
  it('keeps a parallel confirmation when an older 404 completes later', async () => {
    const parent = mkdtempSync(join(tmpdir(), 'receipt-race-')); dirs.push(parent)
    let release!: (value: Response) => void
    let entered!: () => void
    const started = new Promise<void>(resolve => { entered = resolve })
    const pending = new Promise<Response>(resolve => { release = resolve })
    let first = true
    const client = new PowerContextClient({ baseUrl: 'http://127.0.0.1:9123', requestTimeoutMs: 5000,
      fetch: async () => { if (first) { first = false; entered(); return pending } return Response.json(sourceRecord()) } })
    const reader = new LocalReceiptReader({ directory: join(parent, 'records'), resolveConnection: async () => ({ client, accessMode: 'disabled' }) })
    const oldRead = reader.read(context, bundle)
    await started
    expect(await reader.read(context, bundle)).toMatchObject({ state: 'accepted', readState: 'verified' })
    release(Response.json({}, { status: 404 }))
    expect(await oldRead).toMatchObject({ state: 'accepted', readState: 'not_found' })
  })
  it('rejects ordinary Sources, incorrect targets, digest and unchecked acceptance', async () => {
    for (const bad of [sourceRecord(receipt(), { handoff_receipt: false }), sourceRecord(receipt(), { handoff_receipt: undefined }),
      sourceRecord(receipt({ prepared_digest: 'sha256:' + 'c'.repeat(64) })), sourceRecord(receipt(), { content_digest: 'sha256:' + '0'.repeat(64) }),
      sourceRecord(receipt({ receiver_checks: null })), sourceRecord(receipt(), { source_id: 'other' })]) {
      const f = fixture(); f.set(Response.json(bad))
      expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'awaiting', readState: 'invalid' })
    }
  })
  it('accepts a large valid receipt within the existing HTTP response budget', async () => {
    const f = fixture()
    const evidence = Array.from({ length: 1200 }, (_, i) => ({ kind: 'source', source_ref: {
      source_type: 'content', source_id: 'source-' + i + 'x'.repeat(200) } }))
    const large = sourceRecord(receipt({ status: 'needs_clarification', evidence_status: 'unavailable',
      unavailable_evidence: evidence, receiver_checks: null, message: 'Evidence unavailable.' }))
    expect(large.content.length).toBeGreaterThan(262144)
    f.set(Response.json(large))
    expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'needs_clarification', readState: 'verified' })
  })
  it('counts non-BMP receipt messages as Unicode characters', async () => {
    for (const [length, state, readState] of [[5000, 'needs_clarification', 'verified'],
      [8192, 'needs_clarification', 'verified'], [8193, 'awaiting', 'invalid']] as const) {
      const f = fixture()
      f.set(Response.json(sourceRecord(receipt({ status: 'needs_clarification', receiver_checks: null,
        message: '😀'.repeat(length) }))))
      expect(await f.reader.read(context, bundle)).toMatchObject({ state, readState })
    }
  })
  it('requires matching committed authenticated attribution in enforced mode', async () => {
    const f = fixture('enforced')
    expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'awaiting', readState: 'invalid' })
    f.set(Response.json(sourceRecord(receipt(), { receipt_identity: { principal: { type: 'user', id: 'alice' }, receiver_identity_matches: true } })))
    f.expectPrincipal({ type: 'agent', id: 'alice' })
    expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'awaiting', readState: 'invalid' })
    f.expectPrincipal({ type: 'user', id: 'alice' })
    expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'accepted', attribution: { kind: 'authenticated', principal: { type: 'user', id: 'alice' } } })
    f.expectPrincipal({ type: 'user', id: 'bob' })
    expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'awaiting', readState: 'invalid' })
  })
  it('accepts only the exact revision and preserves a non-acceptance decision', async () => {
    const f = fixture()
    const selected = { ...bundle, handoffSelection: { kind: 'revision' as const,
      revision: { scope_id: 'scope', family: 'handoff', artifact_id: 'handoff-a', revision: 7 } } }
    const decision = receipt({ status: 'needs_clarification', selection: 'exact', prepared_digest: null,
      selected_revision: { family: 'handoff', artifact_id: 'handoff-a', revision: 7 },
      receiver_checks: null, message: 'Need repository access.' })
    f.set(Response.json(sourceRecord({ ...decision, selected_revision: { family: 'handoff', artifact_id: 'handoff-a', revision: 8 } })))
    expect(await f.reader.read(context, selected)).toMatchObject({ state: 'awaiting', readState: 'invalid' })
    f.set(Response.json(sourceRecord(decision)))
    expect(await f.reader.read(context, selected)).toMatchObject({ state: 'needs_clarification', readState: 'verified' })
  })
  it('restores expired deliveries only for the exact owner, binding and receipt attempt', async () => {
    const f = fixture()
    expect(await f.reader.saved(context, view)).toBeUndefined()
    await f.reader.read(context, bundle)
    f.set(new Error('offline'))
    expect(await f.reopen().saved(context, view)).toMatchObject({ state: 'accepted', readState: 'stale' })
    expect(await f.reader.saved({ ...context, ownerId: 'other' }, view)).toBeUndefined()
    await expect(f.reader.saved(context, { ...view, binding: { ...view.binding, scopeId: 'other' } })).rejects.toThrow()
    await expect(f.reader.saved(context, { ...view, receiptAttempt: { ...view.receiptAttempt, sourceId: 'other' } })).rejects.toThrow()
  })
  it('indexes an existing observation before prune discards its package', async () => {
    const f = fixture()
    await f.reader.read(context, bundle)
    rmSync(join(f.directory, 'by-delivery'), { recursive: true, force: true })
    expect(await f.reader.saved(context, view)).toBeUndefined()
    expect(await f.reader.confirmedAt(context, bundle)).toBeDefined()
    expect(await f.reader.saved(context, view)).toMatchObject({ state: 'accepted', readState: 'stale' })
  })
  it('does not confirm an interrupted observation write and can recover it', async () => {
    const f = fixture()
    const initial = await f.reader.read(context, bundle)
    const record = readdirSync(f.directory).find(name => /^[0-9a-f]{64}\.json$/.test(name))!
    rmSync(join(f.directory, record))
    expect(await f.reader.saved(context, view)).toBeUndefined()
    f.advance(1000)
    expect(await f.reader.read(context, bundle)).toMatchObject({ state: 'accepted', confirmedAt: initial.confirmedAt })
    expect(await f.reader.saved(context, view)).toMatchObject({ state: 'accepted', readState: 'stale' })
  })
  it('cleans only orphaned observations after a 30-day grace period', async () => {
    const f = fixture()
    await f.reader.read(context, bundle)
    await f.reader.maintain(context, new Set([bundle.deliveryId]))
    f.advance(100 * 86_400_000)
    await f.reader.maintain(context, new Set([bundle.deliveryId]))
    expect(await f.reader.saved(context, view)).toBeDefined()
    await f.reader.maintain({ ...context, ownerId: 'other' }, new Set())
    await f.reader.maintain(context, new Set())
    f.advance(29 * 86_400_000)
    await f.reader.maintain(context, new Set())
    expect(await f.reader.saved(context, view)).toBeDefined()
    f.advance(86_400_000)
    await f.reader.maintain(context, new Set())
    expect(await f.reader.saved(context, view)).toBeUndefined()
  })
})
