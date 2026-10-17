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
import { execFile, spawn } from 'node:child_process'
import { cp, mkdir, mkdtemp, readdir, readFile, rm, writeFile } from 'node:fs/promises'
import http from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const plugin = fileURLToPath(new URL('..', import.meta.url))
const prompt = 'The deployment color is teal-731.'
async function fixture(t, overrides = {}, settings = {}, listenHost = '127.0.0.1') {
  const root = await mkdtemp(join(tmpdir(), 'pc-kimi-'))
  const installed = join(root, 'plugin')
  const home = join(root, 'home')
  const workspace = join(root, 'workspace')
  await cp(plugin, installed, { recursive: true, filter: path => !path.includes('tests') })
  const captures = []
  const requests = []
  const server = http.createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : undefined
    requests.push({ path: req.url, body, authorization: req.headers.authorization })
    if (overrides[req.url]) return overrides[req.url](req, res, body)
    const answer = req.url === '/v1/scope-bindings/resolve' ? { scope_id: 'scope-test' } :
      req.url === '/v1/context/prepare' ? { schema: 'powercontext.prepared-context.v1', status: 'ready',
        content: 'Historical context: use uv.', content_bytes: Buffer.byteLength('Historical context: use uv.') } :
      req.url === '/health/ready' ? { status: 'ready' } : undefined
    if (answer) { res.writeHead(200); res.end(JSON.stringify(answer)); return }
    if (req.url === '/v1/sources/content') {
      captures.push(body)
      res.writeHead(202)
      res.end(JSON.stringify({ status: 'accepted', source: { name: 'content', source_id: body.source_id }, position: captures.length }))
      return
    }
    res.writeHead(404); res.end('{}')
  })
  await new Promise(resolve => server.listen(0, listenHost, resolve))
  await writeFile(join(installed, 'powercontext.json'), JSON.stringify({
    server_url: 'http://' + listenHost + ':' + server.address().port, scope_id: 'scope-test', capture_prompts: true, use_token: false, ...settings,
  }))
  t.after(async () => { server.closeAllConnections(); await new Promise(resolve => server.close(resolve)); await rm(root, { recursive: true, force: true }) })
  async function invoke(input = { hook_event_name: 'UserPromptSubmit', session_id: 'session-test', cwd: workspace, prompt },
    environment = {}) {
    const env = { ...process.env, KIMI_CODE_HOME: home, ...environment }
    if (!Object.hasOwn(environment, 'POWERCONTEXT_KIMI_TOKEN')) delete env.POWERCONTEXT_KIMI_TOKEN
    const child = spawn(process.execPath, [join(installed, 'hooks/user_prompt_submit.mjs')], { cwd: installed,
      env, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    child.stdout.on('data', chunk => stdout += chunk)
    child.stderr.on('data', chunk => stderr += chunk)
    child.stdin.end(JSON.stringify(input))
    const code = await new Promise(resolve => child.on('exit', resolve))
    assert.equal(code, 0, stderr)
    return { output: JSON.parse(stdout), stderr }
  }
  async function status(session = 'session-test', cwd = workspace) {
    const args = [join(installed, 'scripts/status.mjs'), '--cwd', cwd]
    if (session !== undefined) args.push('--session-id', session)
    const result = await promisify(execFile)(process.execPath, args,
      { env: { ...process.env, KIMI_CODE_HOME: home }, windowsHide: true }).catch(error => error)
    return { exit_code: result.code ?? 0, ...JSON.parse(result.stdout) }
  }
  async function scope(action, scopeId, cwd = workspace) {
    const args = [join(installed, 'scripts/scope.mjs'), action, '--cwd', cwd]
    if (scopeId !== undefined) args.push('--scope-id', scopeId)
    const result = await promisify(execFile)(process.execPath, args,
      { env: { ...process.env, KIMI_CODE_HOME: home }, windowsHide: true }).catch(error => error)
    return { exit_code: result.code ?? 0, ...JSON.parse(result.stdout) }
  }
  async function doctor(cwd = workspace) {
    const result = await promisify(execFile)(process.execPath, [join(installed, 'scripts/doctor.mjs'), '--cwd', cwd],
      { env: { ...process.env, KIMI_CODE_HOME: home }, windowsHide: true })
    return JSON.parse(result.stdout)
  }
  return { captures, requests, invoke, installed, home, workspace, status, scope, doctor }
}

test('context and capture use the explicit Scope and shared runtime token', async t => {
  const f = await fixture(t, {}, { use_token: true })
  const { output, stderr } = await f.invoke(undefined, { POWERCONTEXT_KIMI_TOKEN: 'synthetic-token' })
  assert.match(output.message, /scope-test/)
  assert.match(output.message, /Historical context: use uv/)
  assert.equal(stderr, '')
  assert.equal(f.captures[0].content, prompt)
  assert.equal(f.captures[0].scope_id, 'scope-test')
  assert.equal(f.captures[0].metadata.session_id, 'session-test')
  assert.ok(f.requests.every(r => r.authorization === 'Bearer synthetic-token'))
  assert.equal(f.requests[0].body.explicit_scope_id, 'scope-test')
  assert.equal(f.requests[0].body.binding_keys[0].integration, 'kimi-code')
  assert.equal(f.requests[0].body.binding_keys[0].kind, 'workspace')
  assert.match(f.requests[0].body.binding_keys[0].external_id, /^[a-f0-9]{64}$/u)
  await f.invoke(undefined, { POWERCONTEXT_KIMI_TOKEN: 'synthetic-token' })
  assert.notEqual(f.captures[0].source_id, f.captures[1].source_id)
})

test('disabled capture still supplies context', async t => {
  const f = await fixture(t, {}, { capture_prompts: false })
  assert.match((await f.invoke()).output.message, /Historical context/)
  assert.deepEqual(f.captures, [])
})

test('sensitive text and the configured token are never captured', async t => {
  const f = await fixture(t, {}, { use_token: true })
  await f.invoke({ hook_event_name: 'UserPromptSubmit', prompt: 'api_key=private-value' }, { POWERCONTEXT_KIMI_TOKEN: 'synthetic-token' })
  await f.invoke({ hook_event_name: 'UserPromptSubmit', prompt: 'my opaque synthetic-token value' },
    { POWERCONTEXT_KIMI_TOKEN: 'synthetic-token' })
  assert.deepEqual(f.captures, [])
})

test('multimodal input captures text only and pure media is ignored', async t => {
  const f = await fixture(t)
  await f.invoke({ hook_event_name: 'UserPromptSubmit', prompt: [{ type: 'image', data: 'private-image' }, { type: 'text', text: prompt }] })
  assert.equal(f.captures[0].content, prompt)
  assert.deepEqual((await f.invoke({ hook_event_name: 'UserPromptSubmit', prompt: [{ type: 'image', data: 'private-image' }] })).output, {})
  assert.equal(f.captures.length, 1)
})

test('scope rejection never redirects writes and reports no secrets', async t => {
  const f = await fixture(t, { '/v1/scope-bindings/resolve': (_req, res) => { res.writeHead(401); res.end('private response') } })
  const { output, stderr } = await f.invoke()
  assert.deepEqual(output, {})
  assert.deepEqual(f.captures, [])
  assert.match(stderr, /authentication_failed/)
  assert.doesNotMatch(stderr, /private response|teal-731|127\.0\.0\.1/)
})

test('invalid prepared context is withheld while capture remains usable', async t => {
  const f = await fixture(t, { '/v1/context/prepare': (_req, res) => {
    res.writeHead(200); res.end(JSON.stringify({ schema: 'powercontext.prepared-context.v1', status: 'ready',
      content: 'bad history', content_bytes: 1 }))
  } })
  const { output, stderr } = await f.invoke()
  assert.doesNotMatch(output.message, /bad history/)
  assert.match(stderr, /invalid_response/)
  assert.equal(f.captures.length, 1)
})

test('an accepted write with a lost reply stays unknown without automatic retry', async t => {
  const writes = []
  const f = await fixture(t, { '/v1/sources/content': (req, _res, body) => { writes.push(body); req.socket.destroy() } })
  const { output, stderr } = await f.invoke()
  assert.match(output.message, /Historical context/)
  assert.equal(writes.length, 1)
  assert.equal(JSON.parse(stderr.trim()).result, 'unknown')
})

test('timeouts fail open within the automatic budget', async t => {
  const f = await fixture(t, { '/v1/scope-bindings/resolve': () => {} })
  const started = Date.now()
  const { output, stderr } = await f.invoke()
  assert.deepEqual(output, {})
  assert.match(stderr, /server_unavailable/)
  assert.ok(Date.now() - started < 5000)
})

test('loopback aliases support automatic context and Source capture', async t => {
  const f = await fixture(t, {}, {}, '127.0.0.2')
  const { output, stderr } = await f.invoke()
  assert.match(output.message, /Historical context: use uv/)
  assert.equal(stderr, '')
  assert.equal(f.captures[0].content, prompt)
  assert.equal(f.captures[0].scope_id, 'scope-test')
})

test('non-loopback plaintext settings are rejected before sending requests', async t => {
  const f = await fixture(t, {}, { server_url: 'http://127.0.0.1.example.test:8000' })
  const { output, stderr } = await f.invoke()
  assert.deepEqual(output, {})
  assert.match(stderr, /invalid_settings/)
  assert.deepEqual(f.requests, [])
})

test('runtime history records stages, stays private and queries without credentials or network', async t => {
  const f = await fixture(t, {}, { use_token: true })
  assert.equal((await f.status()).status, 'not_observed')
  await f.invoke(undefined, { POWERCONTEXT_KIMI_TOKEN: 'synthetic-token' })
  const count = f.requests.length
  const result = await f.status()
  assert.equal(result.status, 'observed')
  assert.equal(result.selection, 'session')
  assert.equal(result.freshness, 'recent')
  assert.equal(result.stages.scope.state, 'resolved')
  assert.equal(result.stages.prepare.state, 'ready')
  assert.equal(result.stages.capture.state, 'accepted')
  assert.equal(result.stages.injection.state, 'emitted')
  assert.equal(f.requests.length, count)
  assert.equal((await f.status('another-session')).status, 'not_observed')
  assert.equal((await f.status('session-test', '/another-workspace')).status, 'not_observed')
  const directory = join(f.home, 'powercontext/runtime')
  const files = await readdir(directory)
  const stored = await readFile(join(directory, files[0]), 'utf8')
  assert.doesNotMatch(stored, /teal-731|synthetic-token|session-test|\/workspace|Historical context|127\.0\.0\.1/)
  await writeFile(join(f.installed, 'powercontext.json'), JSON.stringify({
    server_url: 'http://127.0.0.1:1', scope_id: 'scope-test', capture_prompts: true, use_token: true,
  }))
  assert.equal((await f.status()).status, 'configuration_mismatch')
})

test('independent stages preserve preparation failure, capture unknown and disabled capture', async t => {
  const f = await fixture(t, {
    '/v1/context/prepare': (_req, res) => { res.writeHead(503); res.end('{}') },
    '/v1/sources/content': (req, _res) => req.socket.destroy(),
  })
  await f.invoke()
  const first = await f.status()
  assert.equal(first.stages.prepare.state, 'failed')
  assert.equal(first.stages.prepare.code, 'server_unavailable')
  assert.equal(first.stages.capture.state, 'unknown')
  assert.equal(first.stages.injection.state, 'skipped')
  const disabled = await fixture(t, {}, { capture_prompts: false })
  await disabled.invoke()
  assert.equal((await disabled.status()).stages.capture.reason, 'disabled')
})

test('runtime storage failure leaves context and capture usable', async t => {
  const f = await fixture(t)
  await mkdir(f.home)
  await writeFile(join(f.home, 'powercontext'), 'Not a directory')
  const result = await f.invoke()
  assert.match(result.output.message, /Historical context/)
  assert.equal(f.captures.length, 1)
  assert.match(result.stderr, /runtime_unavailable/)
  assert.equal((await f.status()).code, 'runtime_unavailable')
})

test('stale and incomplete history remain historical; corrupt records fail without leaking fields', async t => {
  const f = await fixture(t)
  await f.invoke()
  const directory = join(f.home, 'powercontext/runtime')
  const [name] = await readdir(directory)
  const path = join(directory, name)
  const value = JSON.parse(await readFile(path, 'utf8'))
  value.started_at -= 300001
  await writeFile(path, JSON.stringify(value))
  assert.equal((await f.status()).freshness, 'stale')
  value.completed_at = null
  value.private = 'private-answer'
  value.stages.prepare.private = 'private-answer'
  await writeFile(path, JSON.stringify(value))
  const incomplete = await f.status()
  assert.equal(incomplete.freshness, 'incomplete')
  assert.doesNotMatch(JSON.stringify(incomplete), /private-answer/)
  value.stages.prepare.state = 'private-body'
  await writeFile(path, JSON.stringify(value))
  const invalid = await f.status()
  assert.equal(invalid.status, 'failed')
  assert.equal(invalid.code, 'invalid_state')
  assert.doesNotMatch(JSON.stringify(invalid), /private-body/)
})

test('a later attempt stays latest when an earlier capture completes afterward', async t => {
  let release
  let signalArrival
  const arrived = new Promise(resolve => { signalArrival = resolve })
  let count = 0
  const f = await fixture(t, { '/v1/sources/content': (_req, res, body) => {
    const position = ++count
    const complete = () => { res.writeHead(202); res.end(JSON.stringify({
      status: 'accepted', source: { name: 'content', source_id: body.source_id }, position,
    })) }
    if (position === 1) { release = complete; signalArrival() } else complete()
  } })
  const first = f.invoke()
  await arrived
  try { await f.invoke() } finally { release() }
  await first
  assert.equal((await f.status()).stages.capture.source_position, 2)
})

test('overflowed history stays readable and concurrent Hooks recover without deleting unfinished or unowned files', async t => {
  const f = await fixture(t)
  await f.invoke()
  const directory = join(f.home, 'powercontext/runtime')
  const [name] = await readdir(directory)
  const value = JSON.parse(await readFile(join(directory, name), 'utf8'))
  let incomplete, oldest
  // Persist the 65-record state left by concurrent admissions below the retention threshold.
  for (let index = 0; index < 64; index++) {
    const copy = { ...value, attempt_id: randomUUID(), started_at: value.started_at - index - 1,
      completed_at: index === 63 ? null : value.completed_at }
    const path = join(directory, `attempt-${copy.attempt_id}.json`)
    await writeFile(path, JSON.stringify(copy))
    if (index === 63) incomplete = path
    if (index === 62) oldest = path
  }
  const unfinished = await readFile(incomplete, 'utf8')
  await writeFile(join(directory, 'unrelated.txt'), 'Keep me.')
  const before = (await readdir(directory)).sort()
  assert.equal((await f.status()).status, 'observed')
  assert.deepEqual((await readdir(directory)).sort(), before)
  const results = await Promise.all([f.invoke(), f.invoke()])
  for (const result of results) {
    assert.equal(result.stderr, '')
    assert.match(result.output.message, /Historical context/)
  }
  assert.equal((await f.status()).status, 'observed')
  assert.equal(await readFile(incomplete, 'utf8'), unfinished)
  assert.equal(await readFile(join(directory, 'unrelated.txt'), 'utf8'), 'Keep me.')
  assert.equal(await readFile(join(directory, name), 'utf8'), JSON.stringify(value))
  await assert.rejects(readFile(oldest), { code: 'ENOENT' })
})

test('retention preserves unowned files and incomplete attempts; capacity failures stay bounded', async t => {
  const f = await fixture(t)
  await f.invoke()
  const directory = join(f.home, 'powercontext/runtime')
  const [name] = await readdir(directory)
  const value = JSON.parse(await readFile(join(directory, name), 'utf8'))
  await writeFile(join(directory, 'unrelated.txt'), 'Keep me.')
  for (let index = 0; index < 63; index++) {
    const copy = { ...value, attempt_id: randomUUID(), started_at: value.started_at - index - 1,
      completed_at: index === 62 ? null : value.completed_at }
    await writeFile(join(directory, `attempt-${copy.attempt_id}.json`), JSON.stringify(copy))
  }
  await f.invoke()
  assert.equal((await f.status()).status, 'observed')
  assert.equal(await readFile(join(directory, 'unrelated.txt'), 'utf8'), 'Keep me.')
  const retained = await Promise.all((await readdir(directory)).filter(name => name.endsWith('.json'))
    .map(async name => JSON.parse(await readFile(join(directory, name), 'utf8'))))
  assert.ok(retained.some(item => item.completed_at === null))
  for (let index = 0; index < 128; index++) await writeFile(join(directory, `attempt-${randomUUID()}.json`), '{}')
  const failure = await f.status()
  assert.equal(failure.code, 'runtime_capacity_exceeded')
  const result = await f.invoke()
  assert.match(result.output.message, /Historical context/)
  assert.match(result.stderr, /runtime_unavailable/)
})

test('workspace bindings route Hooks and doctor while explicit Scope still overrides a saved binding', async t => {
  const bindings = new Map()
  function reply(res, value) { res.writeHead(200); res.end(JSON.stringify(value)) }
  const f = await fixture(t, {
    '/v1/scope-bindings': (_req, res, body) => {
      bindings.set(body.key.external_id, body.scope_id); reply(res, body)
    },
    '/v1/scope-bindings/clear': (_req, res, body) => {
      reply(res, { cleared: bindings.delete(body.key.external_id) })
    },
    '/v1/scope-bindings/resolve': (_req, res, body) => {
      reply(res, { scope_id: body.explicit_scope_id ?? bindings.get(body.binding_keys[0]?.external_id) ?? 'scope-default' })
    },
  }, { scope_id: null })
  assert.equal((await f.scope('resolve')).scope_id, 'scope-default')
  const other = join(f.workspace, '..', 'other-workspace')
  for (const [cwd, scopeId] of [[f.workspace, 'scope-first'], [other, 'scope-second']]) {
    const bound = await f.scope('bind', scopeId, cwd)
    assert.equal(bound.binding.status, 'saved')
    assert.equal(bound.bound_scope_is_current, true)
    const result = await f.invoke({ cwd, session_id: 'session-test', hook_event_name: 'UserPromptSubmit', prompt })
    assert.equal(result.stderr, '')
    assert.match(result.output.message, new RegExp(scopeId))
    assert.equal(f.captures.at(-1).scope_id, scopeId)
    assert.equal((await f.status('session-test', cwd)).scope_id, scopeId)
    assert.equal((await f.doctor(cwd)).checks.scope.checks.scope_id, scopeId)
  }
  assert.ok(f.requests.filter(item => item.body?.key).every(item => !JSON.stringify(item.body.key).includes(f.workspace)))
  const settingsPath = join(f.installed, 'powercontext.json')
  const settings = JSON.parse(await readFile(settingsPath, 'utf8'))
  await writeFile(settingsPath, JSON.stringify({ ...settings, scope_id: 'scope-explicit' }))
  const overridden = await f.scope('bind', 'scope-third')
  assert.equal(overridden.binding.scope_id, 'scope-third')
  assert.equal(overridden.scope_id, 'scope-explicit')
  assert.equal(overridden.bound_scope_is_current, false)
  await writeFile(settingsPath, JSON.stringify(settings))
  assert.equal((await f.scope('resolve')).scope_id, 'scope-third')
  assert.equal((await f.scope('unbind')).binding.status, 'cleared')
  assert.equal((await f.scope('unbind')).binding.status, 'absent')
  assert.equal((await f.scope('resolve')).scope_id, 'scope-default')
  assert.equal((await f.scope('resolve', undefined, other)).scope_id, 'scope-second')
})

test('a confirmed binding survives later resolution failure and failed resolution never captures', async t => {
  const f = await fixture(t, {
    '/v1/scope-bindings': (_req, res, body) => { res.writeHead(200); res.end(JSON.stringify(body)) },
    '/v1/scope-bindings/resolve': (_req, res) => { res.writeHead(409); res.end('{}') },
  }, { scope_id: null })
  const result = await f.scope('bind', 'scope-saved')
  assert.equal(result.exit_code, 1)
  assert.equal(result.binding.status, 'saved')
  assert.equal(result.binding.scope_id, 'scope-saved')
  assert.equal(result.status, 'failed')
  const hook = await f.invoke()
  assert.deepEqual(hook.output, {})
  assert.deepEqual(f.captures, [])
  assert.ok(f.requests.every(item => !['/v1/context/prepare', '/v1/sources/content'].includes(item.path)))
  const history = await f.status()
  assert.equal(history.status, 'observed')
  assert.equal(history.scope_id, null)
  assert.equal(history.stages.scope.state, 'failed')
})

test('an unconfirmed binding write stays unknown without retry and invalid workspace input sends nothing', async t => {
  const f = await fixture(t, { '/v1/scope-bindings': req => req.socket.destroy() }, { scope_id: null })
  assert.equal((await f.scope('bind', 'scope-target', 'relative')).code, 'workspace_required')
  assert.deepEqual(f.requests, [])
  const result = await f.scope('bind', 'scope-target')
  assert.equal(result.binding.status, 'unknown')
  assert.equal(result.status, 'failed')
  assert.equal(f.requests.filter(item => item.path === '/v1/scope-bindings').length, 1)
})
