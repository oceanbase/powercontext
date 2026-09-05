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
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { test } from 'node:test'
import { environment, injected, CANARY } from './fixture.mjs'

test('real DSH loads the built plugin and recalls processed Source in another session', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    const { instance, dshHome } = env.harness()
    const first = await instance.run('For this project: ' + CANARY + ' Reply with an acknowledgement.')
    assert.ok(first.finalResponse)
    assert.equal(injected(first).length, 0)
    assert.ok(env.calls.some(call => call.path === '/v1/sources/content' && call.status === 202))
    assert.ok(env.calls.some(call => call.path === '/v1/memory/flush' && call.status === 200))
    const memory = await env.api('/v1/memory/entries/list', { scope_id: env.scopeId })
    assert.ok(JSON.stringify(memory).includes(CANARY))
    const capture = env.calls.find(call => call.path === '/v1/sources/content')
    const replay = await env.api('/v1/sources/content', capture.body)
    assert.equal(replay.position, capture.result.position)
    const second = await instance.run('What is the aurora deployment color?')
    const messages = injected(second)
    assert.equal(messages.length, 1)
    const message = messages[0]
    assert.equal(message.source.form, 'snapshot')
    assert.equal(message.source.sections[0].text, message.content[0].text)
    assert.ok(message.content[0].text.includes(CANARY))
    const prepare = env.calls.findLast(call => call.path === '/v1/context/prepare')
    assert.ok(message.content[0].text.endsWith(prepare.result.content))
    assert.ok(message.content[0].text.includes('untrusted historical evidence'))
    const modelInput = env.modelRequests.filter(r => r.stream).at(-1).messages
    assert.equal(modelInput.filter(m => JSON.stringify(m.content).includes('PowerContext context prepared')).length, 1)
    await instance.close()
    const sessions = join(dshHome, 'sessions')
    const saved = readdirSync(sessions, { recursive: true }).filter(path => path.endsWith('.jsonl'))
      .flatMap(path => readFileSync(join(sessions, path), 'utf8').trim().split('\n').map(line => JSON.parse(line)))
    const persisted = saved.find(event => event.type === 'user/message' && event.data?.id === message.id)
    assert.deepEqual(persisted?.data, message)
  } finally { await env.close() }
})

test('Scope faults leave real DSH conversations running and preserve direct-tool errors', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    const { instance, diagnostics } = env.harness({ scopeId: 'scp_missing_runtime_fixture' })
    const run = await instance.run('RUN_PC_SEARCH')
    assert.ok(run.finalResponse)
    assert.equal(injected(run).length, 0)
    assert.ok(env.calls.length >= 2)
    assert.ok(env.calls.every(call => call.path === '/v1/scope-bindings/resolve' && call.status === 404))
    const tool = env.modelRequests.filter(r => r.stream).at(-1).messages.find(message => message.role === 'tool')
    assert.ok(tool.content.startsWith('{'), tool.content)
    const result = JSON.parse(tool.content)
    assert.equal(result.ok, false)
    assert.equal(result.code, 'not_found')
    assert.equal(result.error_code, 'scope_not_found')
    assert.ok(diagnostics().some(event => event.event === 'scope_resolve' && event.error_code === 'scope_not_found'))
    for (const status of [404, 401, 503]) {
      env.setFault({ path: '/v1/scope-bindings/resolve', status })
      const start = env.calls.length
      const next = await instance.run('Reply with a short acknowledgement.')
      assert.ok(next.finalResponse)
      assert.equal(injected(next).length, 0)
      assert.ok(env.calls.slice(start).every(call => call.path === '/v1/scope-bindings/resolve'))
      assert.ok(!JSON.stringify(env.modelRequests.filter(r => r.stream).at(-1)).includes('private-response-marker'))
    }
    for (const outcome of ['version_mismatch', 'authentication_failed', 'server_unavailable']) {
      assert.ok(diagnostics().some(event => event.event === 'scope_resolve' && event.outcome === outcome))
    }
    assert.ok(!JSON.stringify(diagnostics()).includes('private-response-marker'))
    assert.ok(!JSON.stringify(diagnostics()).includes('/v1/'))
  } finally { await env.close() }
})

test('prepare, capture and flush fail independently and recover across real host restarts', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    await env.api('/v1/memory/remember', { scope_id: env.scopeId, kind: 'decision', text: CANARY })
    const { instance } = env.harness()
    for (const path of ['/v1/context/prepare', '/v1/sources/content', '/v1/memory/flush']) {
      env.setFault({ path, status: 503 })
      const start = env.calls.length
      const run = await instance.run('What is the aurora deployment color?')
      assert.ok(run.finalResponse)
      assert.equal(injected(run).length, path === '/v1/context/prepare' ? 0 : 1)
      const calls = env.calls.slice(start)
      assert.equal(calls.filter(call => call.path === '/v1/sources/content').length, 1)
      if (path === '/v1/context/prepare') assert.ok(calls.some(call => call.path === '/v1/sources/content' && call.status === 202))
      if (path === '/v1/sources/content') assert.ok(!calls.some(call => call.path === '/v1/memory/flush'))
    }
    env.setFault(undefined)
    const recovered = await instance.run('What is the aurora deployment color?')
    assert.equal(injected(recovered).length, 1)
    await instance.close()
    const start = env.calls.length
    const restarted = env.harness().instance
    assert.equal(injected(await restarted.run('What is the aurora deployment color?')).length, 1)
    assert.equal(env.calls.slice(start).filter(call => call.path === '/v1/context/prepare').length, 1)
    assert.equal(env.calls.slice(start).filter(call => call.path === '/v1/sources/content').length, 1)
  } finally { await env.close() }
})

test('real DSH does not recall or capture into another configured Scope', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    await env.api('/v1/memory/remember', { scope_id: env.scopeId, kind: 'decision', text: CANARY })
    const other = await env.api('/v1/scopes', {
      title: 'Isolated runtime Scope', summary: 'Isolation fixture', idempotency_key: 'runtime-isolated',
    })
    const { instance } = env.harness({ scopeId: other.scope_id })
    const run = await instance.run('What is the aurora deployment color?')
    assert.ok(run.finalResponse)
    assert.equal(injected(run).length, 0)
    assert.ok(!JSON.stringify(env.modelRequests.filter(r => r.stream).at(-1)).includes(CANARY))
    const scoped = env.calls.filter(call => call.path !== '/v1/scope-bindings/resolve')
    assert.ok(scoped.length > 0)
    assert.ok(scoped.every(call => call.body.scope_id === other.scope_id))
  } finally { await env.close() }
})
