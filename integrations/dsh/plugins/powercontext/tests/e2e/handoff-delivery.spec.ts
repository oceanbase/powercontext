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

import { mkdtempSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { expect, it } from 'vitest'
import { PowerContextClient } from '../../src/client.ts'
import { LocalDeliveryService } from '../../src/handoff/delivery.ts'
import type { SourceBoundary } from '../../src/handoff/contracts.ts'
import { wire } from '../../src/handoff/wire.ts'
import { SERVER_E2E_TIMEOUT_MS, startPowerContextServer } from '../../scripts/e2e-server.mjs'

it('round trips a complete current-work Handoff and a selected exact Revision through the real Server', async () => {
  const server = await startPowerContextServer({ env: { POWERCONTEXT_SERVER_ACCESS_MODE: 'disabled' } })
  const parent = mkdtempSync(join(tmpdir(), 'dsh-handoff-http-'))
  let service: LocalDeliveryService | undefined
  try {
    const client = new PowerContextClient({ baseUrl: server.baseUrl, requestTimeoutMs: 5000 })
    const defaultScope = await client.request('get_default_scope')
    expect(defaultScope.kind).toBe('json')
    if (defaultScope.kind !== 'json') throw new Error('missing default scope')
    const { scope_id: scopeId } = defaultScope.value as { scope_id: string }
    const source: SourceBoundary = {
      sessionId: 'http-fixture', messageId: 'reply-3', throughSeq: 12, cwd: parent,
      workspaceObservedAt: new Date().toISOString(),
    }
    const context = { ownerId: 'http-owner', signal: new AbortController().signal }
    const input = { requestId: 'http-prepare', source, preflightId: 'http-preflight', expectedRevision: 1 }
    service = new LocalDeliveryService({
      directory: join(parent, 'deliveries'),
      preflightGate: { validateForPrepare: async (_ctx, selected) => ({
        source: selected.source, preflightId: selected.preflightId, revision: selected.expectedRevision,
        targetId: 'fixture', targetVersion: 'fixture', adapterVersion: 'fixture',
        effectiveCwd: parent, scopeId, serverIdentity: 'test-server', connectionRef: 'fixture', connectionGeneration: 1,
      }) },
      resolveConnection: async () => ({ client }),
    })

    const ready = await service.prepare(context, { ...input, workInput: {
      schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input',
      objective: '交接真实 HTTP 回包 😀',
      state: [{ basis: 'declared', text: 'The fixture checked the public Work endpoint.', evidence: [] }],
      disposition: 'blocked', next_action: null, omissions: ['Host launch was not exercised.'],
    } })
    expect(ready).toMatchObject({ preparationState: 'prepared', preview: { objective: '交接真实 HTTP 回包 😀' } })
    const first = await service.getForLaunch(context, ready.deliveryId)
    expect(first.handoffSelection).toMatchObject({ kind: 'prepared', digest: expect.stringMatching(/^sha256:/) })
    const { bytes } = await service.exportPackage(context, ready.deliveryId)
    const { carrier } = JSON.parse(Buffer.from(bytes).toString())
    expect(carrier.sourceReceipts).toEqual([expect.objectContaining({ kind: 'handoff-boundary',
      source: expect.objectContaining({ name: 'content', source_id: 'dsh-delivery:' + ready.deliveryId }),
    })])
    const result = await client.request('continue_handoff', { scope_id: scopeId, selection: 'prepared', prepared: carrier.prepared })
    expect(result).toMatchObject({ kind: 'json', value: { status: 'resolved', selection: 'prepared' } })

    // Set up a milestone explicitly as test data. The delivery service never commits one.
    const commit = await client.request('commit_handoff', { scope_id: scopeId, handoff: carrier.prepared })
    if (commit.kind !== 'json') throw new Error('expected committed Handoff')
    const revision = wire('ArtifactReference', (commit.value as { reference: unknown }).reference)
    const selected = await service.prepare(context, { ...input, requestId: 'http-exact', revision,
      supersedesDeliveryId: ready.deliveryId })
    expect(selected.preparationState).toBe('prepared')
    expect((await service.getForLaunch(context, selected.deliveryId)).handoffSelection)
      .toEqual({ kind: 'revision', revision: { scope_id: scopeId, ...revision } })
  } finally {
    try {
      await service?.close()
      await server.stop()
    } finally {
      rmSync(parent, { recursive: true, force: true })
      rmSync(server.home, { recursive: true, force: true })
    }
  }
}, SERVER_E2E_TIMEOUT_MS)
