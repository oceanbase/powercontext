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
import { afterEach, describe, expect, it } from 'vitest'
import { PowerContextClient as PiClient } from '../src/client.ts'
import { PowerContextClient as DshClient } from '../../../../dsh/plugins/powercontext/src/client.ts'
import { PowerContextClient as OpenCodeClient } from '../../../../opencode/plugins/powercontext/src/client.ts'
import { PowerContextClient as OpenClawClient } from '../../../../openclaw/plugins/memory-powercontext/src/client.ts'

const cleanup: Array<() => Promise<void> | void> = []
afterEach(async () => { for (const close of cleanup.splice(0)) await close() })

describe.each([
  ['pi', PiClient], ['dsh', DshClient], ['opencode', OpenCodeClient], ['openclaw', OpenClawClient],
] as const)('%s installed-client contract', (_host, Client) => {
  it('shares read validation, scoped paths, HTTP metadata and unknown write outcomes', async () => {
    const writes: string[] = []
    const server = createServer((request, response) => {
      let raw = ''
      request.on('data', chunk => { raw += chunk })
      request.on('end', () => {
        response.setHeader('X-PowerContext-Request-ID', 'shared-request')
        response.setHeader('ETag', '"revision-1"')
        const path = request.url!
        let value: unknown
        if (path === '/health/live') value = { status: 'ok' }
        else if (path === '/health/ready') value = { status: 'ready', checks: { runtime: 'ready', database: 'ready' } }
        else if (path === '/v1/capabilities') value = { source_types: ['content'], artifact_families: ['memory'],
          memory_extraction: false, handoff_generation: false, search_modes: ['auto'], context_versions: ['powercontext.prepared-context.v1'] }
        else if (path === '/v1/scopes/scope%3Aone') value = {
          scope_id: 'scope:one', title: 'One', summary: 'Shared context',
          context_references: [], external_references: [], version: 1,
        }
        else if (path === '/v1/context/prepare') value = {
          schema: 'powercontext.prepared-context.v1', status: 'ready', content: 'broken', content_bytes: 0,
        }
        else {
          writes.push(JSON.parse(raw).scope_id)
          response.statusCode = 500
          value = { error: { code: 'internal_error', message: 'Unconfirmed', details: null } }
        }
        response.end(JSON.stringify(value))
      })
    })
    await new Promise<void>(resolve => server.listen(0, '127.0.0.1', resolve))
    cleanup.push(() => new Promise<void>(resolve => { server.closeAllConnections(); server.close(() => resolve()) }))
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('Missing fixture address')
    const client = new Client({ baseUrl: `http://127.0.0.1:${address.port}`, requestTimeoutMs: 5000 })
    cleanup.unshift(() => client.close())
    await expect(client.doctor()).resolves.toMatchObject({ ok: true, checks: {
      liveness: { status: 'ok' }, readiness: { status: 'ok' }, capabilities: { status: 'ok' },
    } })
    await expect(client.request('get_liveness')).resolves.toMatchObject({ value: { status: 'ok' } })
    await expect(client.request('get_scope', { scope_id: 'scope:one' })).resolves.toMatchObject({
      value: { scope_id: 'scope:one' }, status: 200, requestId: 'shared-request', etag: '"revision-1"',
    })
    await expect(client.request('prepare_context', { scope_id: 'scope:one', query: 'context' }))
      .rejects.toMatchObject({ name: 'InvalidResponseError', statusCode: 200, requestId: 'shared-request' })
    await expect(client.request('flush_memory', { scope_id: 'scope:one' }))
      .rejects.toMatchObject({ outcome: 'unknown', statusCode: 500 })
    await client.request('get_liveness')
    expect(writes).toEqual(['scope:one'])
  }, 15_000)
})
