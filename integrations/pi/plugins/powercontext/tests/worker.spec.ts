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

import { createServer, type Server } from 'node:http'
import { resolve } from 'node:path'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { InvalidResponseError, RequestTimeoutError, UnknownOutcomeError } from '../src/errors.ts'

const repository = resolve(import.meta.dirname, '../../../../..')
const clients: PowerContextClient[] = []
const servers: Server[] = []

afterEach(async () => {
  vi.unstubAllEnvs()
  clients.splice(0).forEach((client) => client.close())
  await Promise.all(servers.splice(0).map((server) => new Promise<void>((done) => {
    server.closeAllConnections()
    server.close(() => done())
  })))
})

async function fixture() {
  const received: Record<string, unknown>[] = []
  const server = createServer((request, response) => {
    let body = ''
    request.on('data', (chunk) => { body += chunk })
    request.on('end', () => {
      const parsed = JSON.parse(body) as Record<string, unknown>
      received.push(parsed)
      if (parsed.content === 'delay') return
      const content = String(parsed.scope_id)
      response.setHeader('Content-Type', 'application/json')
      response.end(JSON.stringify({
        schema: 'powercontext.prepared-context.v1', status: 'ready', content,
        content_bytes: parsed.query === 'invalid' ? 0 : Buffer.byteLength(content),
      }))
    })
  })
  servers.push(server)
  await new Promise<void>((done) => server.listen(0, '127.0.0.1', done))
  const address = server.address()
  if (!address || typeof address === 'string') throw new Error('No fixture address')
  vi.stubEnv('PATH', resolve(repository, '.venv/bin'))
  const client = new PowerContextClient({ baseUrl: `http://127.0.0.1:${address.port}`, requestTimeoutMs: 5000 })
  clients.push(client)
  return { client, received }
}

describe('the Python hook boundary', () => {
  it('correlates concurrent scopes through the real serial worker', async () => {
    const { client, received } = await fixture()
    const responses = await Promise.all(['one', 'two'].map((scope_id) => (
      client.request('prepare_context', { scope_id, query: 'question' })
    )))
    expect(responses.map((response) => (response.value as { content: string }).content)).toEqual(['one', 'two'])
    expect(received.map((body) => body.scope_id)).toEqual(['one', 'two'])
  })

  it('rejects inconsistent context bytes in the shared Core', async () => {
    const { client } = await fixture()
    await expect(client.request('prepare_context', { scope_id: 'one', query: 'invalid' }))
      .rejects.toBeInstanceOf(InvalidResponseError)
  })

  it('does not replay an accepted write after its deadline', async () => {
    const { client, received } = await fixture()
    await client.request('prepare_context', { scope_id: 'one', query: 'warm' })
    await expect(client.request('capture_content_source', {
      scope_id: 'one', source_id: 'stable', content: 'delay',
    }, undefined, 150)).rejects.toBeInstanceOf(UnknownOutcomeError)
    await new Promise((done) => setTimeout(done, 200))
    expect(received.filter((body) => body.source_id === 'stable')).toHaveLength(1)
    await expect(client.request('prepare_context', { scope_id: 'two', query: 'after restart' }))
      .resolves.toMatchObject({ value: { content: 'two' } })
  }, 15_000)

  it('honors cancellation before startup without sending content', async () => {
    const { client, received } = await fixture()
    await expect(client.request('prepare_context', { scope_id: 'one', query: 'private' }, AbortSignal.abort()))
      .rejects.toBeInstanceOf(RequestTimeoutError)
    expect(received).toEqual([])
  })
})
