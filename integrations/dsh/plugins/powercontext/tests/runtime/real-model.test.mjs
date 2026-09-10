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
import { test } from 'node:test'
import { environment, injected, CANARY } from './fixture.mjs'

test('live model searches and reports an unavailable write approval channel', { timeout: 240000 }, async () => {
  assert.ok(process.env.DEEPSEEK_API_KEY, 'DEEPSEEK_API_KEY is required for real-model acceptance')
  const realModel = {
    apiKey: process.env.DEEPSEEK_API_KEY,
    baseUrl: process.env.DEEPSEEK_BASE_URL ?? 'https://api.deepseek.com',
    model: process.env.DSH_REAL_MODEL ?? 'deepseek-v4-pro',
  }
  const env = await environment({ realModel })
  try {
    const { instance } = env.harness({ capturePrompts: false, requestTimeoutMs: 15000 }, {
      initializeTimeoutMs: 60000, maxTokens: 3000,
    })
    const save = await instance.run('Remember for future Aurora work: the deployment color is violet-cedar-1520. Tell me whether it was saved.')
    assert.ok(env.modelRequests.some(request => request.messages.some(message =>
      message.tool_calls?.some(call => call.function.name === 'pc_remember'))), save.finalResponse)
    // The pinned SDK has no interactive approval channel. The real host must deny the write.
    assert.ok(!env.calls.some(call => call.path === '/v1/memory/remember'))
    assert.ok(!JSON.stringify(await env.api('/v1/memory/entries/list', { scope_id: env.scopeId })).includes('violet-cedar-1520'))
    assert.match(save.finalResponse, /approval|approve|permission|确认|授权|批准/i)
    assert.match(save.finalResponse, /failed|unavailable|could not|couldn't|not saved|did not|未保存|失败|不可用/i)
    await env.api('/v1/memory/remember', { scope_id: env.scopeId, kind: 'decision', text: 'Aurora deployment color is violet-cedar-1520.' })
    const search = await instance.run('Search my saved memories for the Aurora deployment color and report the result.')
    assert.ok(env.calls.some(call => call.path === '/v1/memory/search' && call.status === 200), search.finalResponse)
    assert.ok(search.finalResponse.includes('violet-cedar-1520'))
    console.log('Live routing:', realModel.model, JSON.stringify({ denied: save.finalResponse, search: search.finalResponse }))
  } finally { await env.close() }
})

test('live model processes Source and uses recalled Memory in a new DSH session', { timeout: 240000 }, async () => {
  assert.ok(process.env.DEEPSEEK_API_KEY, 'DEEPSEEK_API_KEY is required for real-model acceptance')
  const realModel = {
    apiKey: process.env.DEEPSEEK_API_KEY,
    baseUrl: process.env.DEEPSEEK_BASE_URL ?? 'https://api.deepseek.com',
    model: process.env.DSH_REAL_MODEL ?? 'deepseek-v4-pro',
  }
  const env = await environment({ realModel })
  try {
    const { instance } = env.harness({ requestTimeoutMs: 90000, timeoutMs: 90000 })
    const first = await instance.run('This is a stable convention for the aurora project: ' + CANARY + ' Acknowledge briefly.')
    assert.ok(first.finalResponse)
    assert.ok(env.calls.some(call => call.path === '/v1/sources/content' && call.status === 202))
    assert.ok(env.calls.some(call => call.path === '/v1/memory/flush' && call.status === 200))
    const memory = await env.api('/v1/memory/entries/list', { scope_id: env.scopeId })
    assert.ok(JSON.stringify(memory).includes('violet-cedar-1457'), 'accepted Source must actually become Memory')
    const second = await instance.run('What is the aurora deployment color? Use the available context and answer briefly.')
    assert.equal(injected(second).length, 1)
    assert.ok(injected(second)[0].content[0].text.includes('violet-cedar-1457'))
    assert.ok(second.finalResponse.includes('violet-cedar-1457'))
    assert.ok(env.modelRequests.filter(request => request.stream).at(-1).messages.some(message =>
      JSON.stringify(message.content).includes('PowerContext context prepared')))
    console.log('Live model:', realModel.model, '; Source → Memory → fresh-session snapshot → model answer passed')
  } finally { await env.close() }
})
