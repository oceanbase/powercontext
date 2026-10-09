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
import { createHash } from 'node:crypto'
import { createServer } from 'node:http'
import { readdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join, resolve } from 'node:path'
import { setTimeout } from 'node:timers/promises'
import { test } from 'node:test'
import { environment, injected, CANARY, NATIVE_ERROR_CANARY } from './fixture.mjs'
import { installIntoHome } from './setup-fixture.mjs'
import { registerSkill } from '../../src/skill.ts'
import { MCP_OPERATIONS } from '../../src/mcp-operations.generated.ts'

test('documented setup installs the matched plugin, diagnoses the running host and recalls processed Source', { timeout: 240000 }, async () => {
  const env = await environment({ fullCatalog: true })
  try {
    const installation = await installIntoHome(env.home)
    assert.ok(installation.setup.includes('powercontext-dsh'))
    assert.equal(installation.doctor.checks.plugin.checks.registration, 'present')
    assert.equal(installation.doctor.checks.plugin.checks.running_host_configuration, 'not_observed')
    const { instance, dshHome, doctor, status, workspace } = env.harness({ baseUrl: 'http://127.0.0.1:1' }, {
      plugin: installation.plugin, commands: true,
      env: { POWERCONTEXT_DSH_BASE_URL: env.baseUrl },
    })
    const first = await instance.run('For this project: ' + CANARY + ' Reply with an acknowledgement.')
    assert.ok(first.finalResponse)
    const request = env.modelRequests.find(r => r.stream)
    const catalog = new Set(request.tools.map(tool => tool.function.name))
    const wireTools = env.mcpCalls.filter(call => call.body?.method === 'tools/list')
      .flatMap(call => call.result?.tools ?? [])
    assert.ok(wireTools.length, 'catalog must be observed on the actual Server MCP connection')
    assert.deepEqual(new Set([...catalog].filter(name => name.startsWith('mcp__powercontext__'))),
      new Set(wireTools.map(tool => `mcp__powercontext__${tool.name}`)))
    assert.deepEqual(new Set(wireTools.map(tool => tool.name)), new Set(Object.keys(MCP_OPERATIONS)))
    for (const tool of wireTools) {
      assert.ok(Object.hasOwn(MCP_OPERATIONS, tool.name), `plugin catalog is missing ${tool.name}`)
      assert.equal(MCP_OPERATIONS[tool.name].readOnly, tool.annotations?.readOnlyHint === true)
    }
    assert.ok(catalog.has('mcp__powercontext__prepare_handoff_hint'))
    const system = request.messages.filter(message => message.role === 'system')
    const routing = request.messages.find(message => JSON.stringify(message).includes('PowerContext host Scope routing'))
    assert.ok(routing, 'host-resolved Scope routing must reach the model')
    assert.ok(JSON.stringify(routing).includes(env.scopeId))
    const bindingId = createHash('sha256').update(resolve(workspace)).digest('hex')
    assert.ok(JSON.stringify(routing).includes(bindingId), 'host workspace binding key must reach the model')
    const references = JSON.stringify(system).match(/\bmcp__powercontext__[a-z0-9_]+\b/g) ?? []
    assert.ok(references.length > 0, 'PowerContext guidance must reach the model before any Skill load')
    for (const name of references) assert.ok(catalog.has(name), `guidance refers to unavailable DSH tool: ${name}`)
    if (process.env.POWERCONTEXT_GUIDANCE_EXPORT) {
      const skills = []
      registerSkill({ get: () => ({ register(value) { skills.push(value) } }) })
      writeFileSync(join(process.env.POWERCONTEXT_GUIDANCE_EXPORT, 'dsh.json'), JSON.stringify({
        host: 'dsh', catalog_source: 'real SDK model request',
        guidance: system.map(message => typeof message.content === 'string' ? message.content : JSON.stringify(message.content)).join('\n'),
        skill: skills.find(skill => skill.name === 'powercontext-project-context'), skills,
        host_skill_tools: request.tools.map(tool => tool.function).filter(tool => tool.name.includes('skill')),
        tools: request.tools.map(tool => tool.function).filter(tool => tool.name.startsWith('mcp__powercontext__')),
      }, null, 2))
    }
    assert.equal(injected(first).length, 0)
    assert.ok(env.calls.some(call => call.path === '/v1/sources/content' && call.status === 202))
    assert.ok(env.calls.some(call => call.path === '/v1/memory/flush' && call.status === 200))
    const memory = await env.api('/v1/memory/entries/list', { scope_id: env.scopeId })
    assert.ok(JSON.stringify(memory).includes(CANARY))
    const diagnosedAt = env.calls.length
    const firstStatus = await status(first.sessionId)
    assert.equal(firstStatus.freshness, 'current')
    assert.equal(firstStatus.stages.prepare.state, 'empty')
    assert.equal(firstStatus.stages.capture.state, 'accepted')
    assert.equal(firstStatus.stages.flush.state, 'completed')
    assert.equal(firstStatus.stages.injection.state, 'skipped')
    assert.ok(!JSON.stringify(firstStatus).includes(CANARY))
    const report = await doctor(first.sessionId)
    assert.equal(report.ok, true, JSON.stringify(report))
    assert.equal(report.kind, 'success')
    assert.equal(report.configuration.endpoint.origin, env.baseUrl)
    assert.equal(report.configuration.endpoint.source, 'environment')
    assert.equal(report.configuration.authorization.source, 'default')
    assert.equal(report.configuration.scope.source, 'default')
    assert.equal(report.checks.capabilities.code, 'extraction_enabled')
    assert.equal(report.checks.routes.code, 'routes_declared')
    assert.equal(report.checks.mcp_catalog.code, 'native_mcp_tools_visible')
    assert.deepEqual(new Set(report.checks.mcp_catalog.tools),
      new Set(wireTools.map(tool => `mcp__powercontext__${tool.name}`)))
    const afterDoctor = await status(first.sessionId)
    assert.equal(afterDoctor.attempt, firstStatus.attempt)
    assert.equal(afterDoctor.stages.prepare.state, 'empty')
    assert.equal(afterDoctor.stages.prepare.observed_at, firstStatus.stages.prepare.observed_at)
    assert.ok(env.calls.slice(diagnosedAt).every(call => !['/v1/sources/content', '/v1/memory/flush'].includes(call.path)))
    env.setFault({ path: '/v1/scope-bindings/resolve', status: 401 })
    const denied = await doctor(first.sessionId)
    assert.equal(denied.kind, 'error')
    assert.equal(denied.checks.liveness.state, 'ok')
    assert.equal(denied.checks.scope.code, 'authentication_failed')
    assert.equal(denied.checks.scope.operation, 'resolve_scope_binding')
    assert.equal(denied.checks.prepare.state, 'skipped')
    const unverified = await status(first.sessionId)
    assert.equal(unverified.kind, 'error')
    assert.equal(unverified.stale_reason, 'scope_unverified')
    assert.equal(unverified.stages.capture.state, 'accepted')
    env.setFault(undefined)
    const capture = env.calls.find(call => call.path === '/v1/sources/content')
    const replay = await env.api('/v1/sources/content', capture.body)
    assert.equal(replay.position, capture.result.position)
    const second = await instance.run('What is the aurora deployment color?')
    const messages = injected(second)
    assert.equal(messages.length, 1)
    const secondStatus = await status(second.sessionId)
    assert.equal(secondStatus.stages.prepare.state, 'ready')
    assert.equal(secondStatus.stages.injection.state, 'appended')
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

test('real DSH rejects secret native writes including nested Handoff content before approval or MCP dispatch', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    const { instance } = env.harness({ capturePrompts: false })
    for (const operation of ['remember_memory', 'revise_memory_entry', 'capture_content_source',
      'handoff_current_work', 'activate_handoff', 'finalize_handoff', 'commit_handoff']) {
      assert.ok((await instance.run(`RUN_PC_SECRET:${operation}`)).finalResponse)
      const input = env.modelRequests.filter(request => request.stream).at(-1)
      assert.match(input.messages.find(message => message.role === 'tool').content, /secret_rejected/)
      assert.ok(!env.mcpCalls.some(call => call.body?.method === 'tools/call' && call.body.params?.name === operation))
    }
    assert.ok(!env.calls.some(call => call.path === '/v1/sources/content'))
    const memory = await env.api('/v1/memory/entries/list', { scope_id: env.scopeId })
    assert.ok(!JSON.stringify(memory).includes('FAKE_REVIEW_MARKER'))
  } finally { await env.close() }
})

test('real DSH receives controlled MCP HTTP failures without upstream diagnostics and can recover', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    await env.api('/v1/memory/remember', { scope_id: env.scopeId, kind: 'decision', text: CANARY })
    const { instance, diagnostics } = env.harness({ capturePrompts: false })
    for (const [status, code] of [[503, 'unavailable'], [422, 'invalid_request']]) {
      env.setFault({ path: '/mcp', status })
      assert.ok((await instance.run('RUN_PC_SEARCH')).finalResponse)
      assert.ok(env.mcpCalls.some(call => call.status === status && call.body?.params?.name === 'search_memory'))
      const input = env.modelRequests.filter(request => request.stream).at(-1)
      const result = input.messages.find(message => message.role === 'tool')
      assert.match(result.content, new RegExp(code))
      assert.match(result.content, new RegExp(`"status":${status}`))
      assert.ok(!JSON.stringify(input.messages).includes(NATIVE_ERROR_CANARY))
      assert.doesNotMatch(JSON.stringify(input.messages), /private-response-marker|FAKE_NATIVE_ERROR_CANARY/)
      assert.doesNotMatch(JSON.stringify(diagnostics()), /private-response-marker|FAKE_NATIVE_ERROR_CANARY/)
    }
    env.setFault(undefined)
    assert.ok((await instance.run('RUN_PC_SEARCH')).finalResponse)
    const input = env.modelRequests.filter(request => request.stream).at(-1)
    assert.ok(input.messages.find(message => message.role === 'tool').content.includes(CANARY))
  } finally { await env.close() }
})

test('real native DSH MCP refuses redirected search results without leaking the project query', { timeout: 120000 }, async () => {
  const received = []
  const target = createServer((req, res) => { received.push(req.url); res.end('unexpected redirect destination') })
  await new Promise(resolve => target.listen(0, '127.0.0.1', resolve))
  const env = await environment()
  try {
    env.setFault({ path: '/mcp', redirectTo: `http://127.0.0.1:${target.address().port}/mcp/` })
    assert.ok((await env.harness().instance.run('RUN_PC_SEARCH')).finalResponse)
    assert.ok(env.mcpCalls.some(call => call.body?.method === 'tools/call' && call.body.params?.name === 'search_memory'))
    const result = env.modelRequests.filter(request => request.stream).at(-1).messages.find(message => message.role === 'tool')
    assert.match(result.content, /redirect_rejected/)
    assert.deepEqual(received, [])
  } finally {
    await env.close()
    target.closeAllConnections()
    await new Promise(resolve => target.close(resolve))
  }
})

test('Scope resolution failures deny cross-Scope MCP calls while real DSH conversations continue', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    await env.api('/v1/memory/remember', { scope_id: env.scopeId, kind: 'decision', text: CANARY })
    const { instance, diagnostics } = env.harness({ scopeId: 'scp_missing_runtime_fixture' })
    const run = await instance.run('RUN_PC_SEARCH')
    assert.ok(run.finalResponse)
    assert.equal(injected(run).length, 0)
    assert.ok(env.calls.length >= 1)
    assert.ok(env.calls.every(call => call.path === '/v1/scope-bindings/resolve' && call.status === 404))
    assert.ok(!env.mcpCalls.some(call => call.body?.method === 'tools/call' && call.body.params?.name === 'search_memory'))
    const tool = env.modelRequests.filter(r => r.stream).at(-1).messages.find(message => message.role === 'tool')
    assert.match(tool.content, /host Scope could not be resolved/)
    assert.ok(!JSON.stringify(env.modelRequests.filter(r => r.stream).at(-1)).includes(CANARY))
    assert.ok(diagnostics().some(event => event.event === 'scope_resolve' && event.error_code === 'scope_not_found'))
    for (const status of [404, 401, 503]) {
      env.setFault({ path: '/v1/scope-bindings/resolve', status })
      const start = env.calls.length
      const next = await instance.run('RUN_PC_SEARCH')
      assert.ok(next.finalResponse)
      assert.equal(injected(next).length, 0)
      assert.ok(env.calls.slice(start).every(call => call.path === '/v1/scope-bindings/resolve'))
      const input = env.modelRequests.filter(r => r.stream).at(-1)
      assert.match(input.messages.find(message => message.role === 'tool').content, /host Scope could not be resolved/)
      assert.ok(!JSON.stringify(input).includes(CANARY))
      assert.ok(!JSON.stringify(env.modelRequests.filter(r => r.stream).at(-1)).includes('private-response-marker'))
    }
    for (const outcome of ['version_mismatch', 'authentication_failed', 'server_unavailable']) {
      assert.ok(diagnostics().some(event => event.event === 'scope_resolve' && event.outcome === outcome))
    }
    assert.ok(!JSON.stringify(diagnostics()).includes('private-response-marker'))
    assert.ok(!JSON.stringify(diagnostics()).includes('/v1/'))
    const configured = await env.api('/v1/scopes', {
      title: 'Configured MCP Scope', summary: 'Resolver failure fixture', idempotency_key: 'runtime-mcp-resolver',
    })
    env.setFault({ path: '/v1/scope-bindings/resolve', status: 503 })
    const unavailable = env.harness({ scopeId: configured.scope_id }).instance
    assert.ok((await unavailable.run('RUN_PC_SEARCH')).finalResponse)
    const denied = env.modelRequests.filter(r => r.stream).at(-1)
    assert.match(denied.messages.find(message => message.role === 'tool').content, /host Scope could not be resolved/)
    assert.ok(!JSON.stringify(denied).includes(CANARY))
    assert.ok(!env.mcpCalls.some(call => call.body?.method === 'tools/call' && call.body.params?.name === 'search_memory'))

    env.setFault(undefined)
    const recovered = await env.harness().instance.run('RUN_PC_SEARCH')
    assert.ok(recovered.finalResponse)
    assert.ok(env.mcpCalls.some(call => call.body?.method === 'tools/call' && call.body.params?.name === 'search_memory'))
    const result = env.modelRequests.filter(r => r.stream).at(-1).messages.find(message => message.role === 'tool')
    assert.ok(result.content.includes(CANARY))
  } finally { await env.close() }
})

test('a stalled native MCP handshake does not block real DSH startup and can finish later', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    env.setFault({ path: '/mcp', hold: true })
    const { instance, diagnostics } = env.harness()
    const run = await instance.run('Reply with a short acknowledgement.')
    assert.ok(run.finalResponse, 'ordinary conversation must reach the model before MCP initialization completes')
    assert.ok(env.mcpCalls.some(call => call.body?.method === 'initialize'))
    const initial = env.modelRequests.find(request => request.stream)
    assert.ok(!initial.tools.some(tool => tool.function.name.startsWith('mcp__powercontext__')))
    assert.ok(diagnostics().some(event => event.event === 'mcp_connect' && event.outcome === 'pending'))

    env.setFault(undefined)
    const deadline = Date.now() + 10000
    let catalog = []
    do {
      assert.ok((await instance.run('Reply with a short acknowledgement.')).finalResponse)
      catalog = env.modelRequests.filter(request => request.stream).at(-1).tools
      if (catalog.some(tool => tool.function.name === 'mcp__powercontext__get_scope')) break
      await setTimeout(100)
    } while (Date.now() < deadline)
    assert.ok(catalog.some(tool => tool.function.name === 'mcp__powercontext__get_scope'), 'the same native client must publish tools after late initialization')
    assert.ok((await instance.run('RUN_PC_GET_SCOPE')).finalResponse)
    assert.ok(env.mcpCalls.some(call => call.body?.method === 'tools/call' && call.body.params?.name === 'get_scope'))
    await instance.close()

    env.setFault({ path: '/mcp', hold: true })
    const pending = env.harness().instance
    assert.ok((await pending.run('Reply with a short acknowledgement.')).finalResponse)
    await pending.close()
    assert.ok(env.mcpCalls.findLast(call => call.body?.method === 'initialize').closed, 'closing the host must close its stalled MCP connection')
  } finally { await env.close() }
})

test('prepare, capture and flush fail independently and recover across real host restarts', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    await env.api('/v1/memory/remember', { scope_id: env.scopeId, kind: 'decision', text: CANARY })
    const { instance, status } = env.harness({}, { commands: true })
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
      const observation = await status(run.sessionId)
      const stage = path === '/v1/context/prepare' ? 'prepare' : path === '/v1/sources/content' ? 'capture' : 'flush'
      assert.equal(observation.stages[stage].state, 'unavailable')
      assert.equal(observation.stages[stage].http_status, 503)
      if (stage !== 'capture') assert.equal(observation.stages.capture.state, 'accepted')
      assert.equal(observation.stages.injection.state, stage === 'prepare' ? 'skipped' : 'appended')
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

test('real DSH distinguishes rejected writes from incomplete HTTP responses', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    await env.api('/v1/memory/remember', { scope_id: env.scopeId, kind: 'decision', text: CANARY })
    const { instance, status } = env.harness({ requestTimeoutMs: 1000, timeoutMs: 10000 }, { commands: true })
    for (const path of ['/v1/sources/content', '/v1/memory/flush']) {
      for (const holdBody of [false, true]) {
        env.setFault({ path, status: 401, holdBody })
        const run = await instance.run('What is the aurora deployment color?')
        assert.ok(run.finalResponse)
        const observation = await status(run.sessionId)
        const stage = path === '/v1/sources/content' ? 'capture' : 'flush'
        assert.equal(observation.stages[stage].code, 'authentication_failed')
        assert.equal(observation.stages[stage].http_status, 401)
        assert.equal(observation.stages[stage].confirmation, 'rejected')
        if (holdBody) {
          assert.equal(observation.stages[stage].request_id, 'req-runtime-body')
          assert.equal(observation.stages[stage].response_body_error, 'request_timeout')
          assert.equal(observation.stages[stage].failure_phase, 'response_body')
        }
        if (stage === 'flush') assert.equal(observation.stages.capture.state, 'accepted')
        else assert.equal(observation.stages.flush.code, 'capture_rejected')
        assert.equal(observation.stages.injection.state, 'appended')
        assert.ok(!JSON.stringify(observation).includes('private-response-marker'))
      }
    }
    env.setFault({ path: '/v1/sources/content', status: 202, holdBody: true })
    const incomplete = await instance.run('What is the aurora deployment color?')
    const observation = await status(incomplete.sessionId)
    assert.equal(observation.stages.capture.http_status, 202)
    assert.equal(observation.stages.capture.code, 'request_timeout')
    assert.equal(observation.stages.capture.confirmation, 'unconfirmed')
    assert.equal(observation.stages.flush.code, 'capture_not_confirmed')
    env.setFault(undefined)
    const recovered = await instance.run('What is the aurora deployment color?')
    assert.equal((await status(recovered.sessionId)).stages.capture.state, 'accepted')
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

test('real DSH enforces the host Scope for native MCP path arguments before dispatch', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    const other = await env.api('/v1/scopes', {
      title: 'Isolated MCP Scope', summary: 'Path Scope isolation fixture', idempotency_key: 'runtime-mcp-isolated',
    })
    const isolated = env.harness({ scopeId: other.scope_id }).instance
    const denied = await isolated.run('RUN_PC_GET_SCOPE')
    assert.ok(denied.finalResponse)
    assert.ok(!env.mcpCalls.some(call => call.body?.method === 'tools/call' && call.body.params?.name === 'get_scope'))
    const denial = env.modelRequests.filter(request => request.stream).at(-1).messages.find(message => message.role === 'tool')
    assert.ok(denial, 'the native DSH host must return the denied tool result to the model')
    assert.ok(denial.content.includes(other.scope_id))
    assert.match(denial.content, /did not use the host Scope/)

    const matched = env.harness().instance
    const allowed = await matched.run('RUN_PC_GET_SCOPE')
    assert.ok(allowed.finalResponse)
    const call = env.mcpCalls.find(call => call.body?.method === 'tools/call' && call.body.params?.name === 'get_scope')
    assert.equal(call?.body.params.arguments.scope_id, env.scopeId)
    const result = env.modelRequests.filter(request => request.stream).at(-1).messages.find(message => message.role === 'tool')
    assert.ok(result?.content.includes(env.scopeId))
  } finally { await env.close() }
})


test('real DSH discovers bilingual domains and loads only the requested Skill', { timeout: 120000 }, async () => {
  const env = await environment()
  try {
    const { instance } = env.harness()
    const registered = []
    registerSkill({ get: () => ({ register(value) { registered.push(value) } }) })
    for (const domain of registered.filter(skill => skill.name !== 'powercontext-project-context')) {
      await instance.run(`LOAD_PC_SKILL:${domain.name}`)
      const requests = env.modelRequests.filter(request => request.stream)
      const finalMessages = requests.at(-1).messages
      assert.ok(JSON.stringify(finalMessages).includes(domain.description), `${domain.name} metadata is not model-visible`)
      const result = finalMessages.find(message => message.role === 'tool')
      assert.ok(result, `${domain.name} was not loaded through the host Skill tool`)
      assert.ok(JSON.stringify(result).includes(domain.content.trim().split('\n')[0]))
      for (const other of registered.filter(skill => skill.name !== domain.name)) {
        assert.ok(!JSON.stringify(result).includes(other.content.trim().split('\n')[0]), `loading ${domain.name} also loaded ${other.name}`)
      }
    }
    await instance.close()
  } finally { await env.close() }
})
