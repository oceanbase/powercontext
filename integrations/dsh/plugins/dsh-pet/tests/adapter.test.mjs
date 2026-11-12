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

import assert from 'node:assert/strict'
import { randomUUID } from 'node:crypto'
import { once } from 'node:events'
import { mkdir, mkdtemp, readFile, rename, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { setTimeout as delay } from 'node:timers/promises'
import { apply, inject, name } from '../lib/index.js'

const generation = () => randomUUID().replaceAll('-', '')
const identity = server_id => ({ server_id, authentication: 'disabled', access_mode: 'disabled', principal: null })
const input = { petId: 'main', userText: 'Favorite tea?', replyText: 'Hibiscus', ts: 1 }

async function until(check, description) {
  for (let attempt = 0; attempt < 140; attempt++) {
    if (await check()) return
    await delay(50)
  }
  throw new Error('Timed out: ' + description)
}

async function fixture(t) {
  const home = await mkdtemp(join(tmpdir(), 'pc-pet-adapter-'))
  t.after(() => rm(home, { force: true, recursive: true }))
  const profile = { name: 'web', dir: join(home, 'profiles/web') }
  const root = join(profile.dir, '.powercontext-dsh-pet')
  await mkdir(root, { recursive: true, mode: 0o700 })
  const settings = join(root, 'bindings.json')
  let provider
  let dispose
  let serverIdentity = identity('one')
  let loseResponse = false
  let holdRecall = false
  const calls = []
  const server = createServer(async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    const payload = chunks.length ? JSON.parse(Buffer.concat(chunks).toString()) : null
    calls.push({ path: request.url, payload })
    response.setHeader('content-type', 'application/json')
    if (request.url === '/v1/connection/identity') return response.end(JSON.stringify(serverIdentity))
    if (request.url === '/v1/atomic-memory/search') {
      if (holdRecall) return
      return response.end(JSON.stringify({ mode: 'text', hits: [{ memory: {
        artifact: { family: 'atomic-memory' }, state: 'active', text: 'Preferred tea: hibiscus.',
      } }] }))
    }
    if (request.url === '/v1/sources/content') {
      if (loseResponse) return response.destroy()
      response.statusCode = 202
      return response.end(JSON.stringify({
        status: 'accepted', source: { name: 'content', source_id: payload.source_id }, position: 1,
      }))
    }
    response.statusCode = 404
    response.end('{}')
  })
  server.listen(0, '127.0.0.1')
  await once(server, 'listening')
  t.after(() => { dispose?.(); server.closeAllConnections(); server.close() })
  const endpoint = 'http://127.0.0.1:' + server.address().port
  const binding = { pet_id: 'main', scope_id: 'test-pet-scope', generation: generation() }
  const configure = async ({ enabled = true, bindings = [binding] } = {}) => {
    await writeFile(settings + '.new', JSON.stringify({
      version: 1, enabled, server_url: endpoint, identity: identity('one'), bindings,
    }), { mode: 0o600 })
    await rename(settings + '.new', settings)
  }
  const slot = {
    list: () => ({ external: provider ? [provider.id] : [] }),
    register(value) {
      provider = value
      return () => { if (provider === value) provider = undefined }
    },
  }
  const start = () => apply({
    get: key => key === inject[0] ? { memory: slot } : profile,
    effect: fn => { dispose = fn() },
  })
  const stop = () => dispose?.()
  const status = async () => JSON.parse(await readFile(join(root, 'status.json'), 'utf8').catch(() => '{}'))
  return {
    start, stop, slot, configure, status, calls, binding,
    get provider() { return provider },
    loseResponse: value => { loseResponse = value },
    serverIdentity: value => { serverIdentity = identity(value) },
    holdRecall: value => { holdRecall = value },
  }
}

test('lost capture response reuses the same stored payload and cannot drain on another Server identity', async t => {
  const f = await fixture(t)
  await f.configure()
  f.loseResponse(true)
  f.start()
  const writes = () => f.calls.filter(call => call.path === '/v1/sources/content')
  await f.provider.capture(input)
  await until(() => writes().length === 1, 'first delivery lost its response')
  f.stop()
  f.serverIdentity('two')
  f.start()
  await until(async () => (await f.status()).bindings?.[0]?.capture === 'identity_changed', 'mismatch reported')
  assert.equal(writes().length, 1)
  assert.equal(await f.provider.recall(input), undefined)
  assert.ok(!f.calls.some(call => call.path === '/v1/atomic-memory/search'))
  f.stop()
  f.serverIdentity('one')
  f.loseResponse(false)
  f.start()
  await until(async () => (await f.status()).bindings?.[0]?.capture === 'accepted', 'original destination acknowledged replay')
  assert.equal(writes().length, 2)
  assert.deepEqual(writes()[0], writes()[1])
  assert.equal(writes()[1].payload.scope_id, f.binding.scope_id)
  assert.deepEqual(JSON.parse(writes()[1].payload.content).messages, [
    { role: 'user', content: input.userText }, { role: 'assistant', content: input.replyText },
  ])
})

test('off, an unbound pet, and an occupied or replaced slot cannot send content', async t => {
  const f = await fixture(t)
  f.start()
  assert.equal(f.provider, undefined)
  const other = { id: 'other-provider' }
  const release = f.slot.register(other)
  await f.configure()
  await until(async () => (await f.status()).provider === 'provider_conflict', 'occupied slot reported')
  assert.equal(f.provider, other)
  assert.equal(f.calls.length, 0)
  release()
  await until(() => f.provider?.id === name, 'provider registers after slot is available')
  await f.provider.capture({ ...input, petId: 'unbound' })
  assert.equal(await f.provider.recall({ ...input, petId: 'unbound' }), undefined)
  assert.equal(f.calls.length, 0)

  const provider = f.provider
  const releaseAgain = f.slot.register(other)
  await provider.capture(input)
  await until(async () => (await f.status()).provider === 'provider_conflict', 'lost slot reported')
  releaseAgain()
  await f.configure({ enabled: false })
  await until(async () => (await f.status()).provider === 'off', 'off applied')
  await provider.capture(input)
  assert.equal(f.calls.length, 0)
  assert.equal(f.provider, undefined)
})

test('recall scopes the query and returns the original path after a stalled search', async t => {
  const f = await fixture(t)
  await f.configure()
  f.start()
  const patch = await f.provider.recall(input)
  assert.match(patch.userText, /Untrusted historical evidence.*\n.*hibiscus/)
  assert.ok(patch.userText.endsWith(input.userText))
  assert.deepEqual(f.calls.at(-1).payload, { scope_id: f.binding.scope_id, query: input.userText, limit: 3, mode: 'text' })
  f.holdRecall(true)
  const started = performance.now()
  assert.equal(await f.provider.recall(input), undefined)
  assert.ok(performance.now() - started < 1500)
})
