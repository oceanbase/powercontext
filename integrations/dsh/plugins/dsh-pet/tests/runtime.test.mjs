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
import { execFile, spawn } from 'node:child_process'
import { once } from 'node:events'
import { access, appendFile, mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { createServer } from 'node:http'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { promisify } from 'node:util'
import { test } from 'node:test'
import { startPowerContextServer, unusedPort } from '../../powercontext/scripts/e2e-server.mjs'
import { startDesktop } from './desktop-host.mjs'

const exec = promisify(execFile)
const repo = resolve(import.meta.dirname, '../../../../..')
const petPackage = resolve(import.meta.dirname, '..')
const fact = 'The favorite tea of the synthetic pet is silver hibiscus.'
const correction = 'Correction: the favorite tea of the synthetic pet is cedar rooibos.'
const question = 'What is the favorite tea of the synthetic pet?'
const outageTurn = 'Unique synthetic exchange while the adapter Server route is down.'
const desktopApp = process.env.DSH_PET_TEST_DESKTOP_APP
const profile = desktopApp ? 'desktop' : 'web'
const dsh = desktopApp ? join(desktopApp, 'Contents/Resources/runtime/cli/bin/dsh') : process.env.DSH_PET_TEST_BIN

async function until(check, description, timeout = 25_000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    if (await check()) return
    await delay(100)
  }
  throw new Error('Timed out: ' + description)
}

async function api(base, path, payload) {
  const response = await fetch(base + path, payload ? {
    method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify(payload),
  } : {})
  assert.ok(response.ok, path + ' HTTP ' + response.status + ': ' + (response.ok ? '' : await response.text()))
  return response.json()
}

async function fixtureModel() {
  const calls = []
  const http = createServer(async (req, res) => {
    const chunks = []
    for await (const chunk of req) chunks.push(chunk)
    const body = JSON.parse(Buffer.concat(chunks).toString())
    calls.push(body)
    if (body.stream) {
      const messages = JSON.stringify(body.messages)
      const current = JSON.stringify(body.messages.findLast(message => message.role === 'user'))
      let text = 'Understood.'
      // Neutral filler must not repeat a fact and accidentally keep it in the
      // native window. Only the fixed probe answers from its available context.
      if (current.includes(question)) {
        text = messages.includes(correction) ? correction : messages.includes(fact) ? fact : 'I do not know.'
      }
      res.setHeader('content-type', 'text/event-stream')
      if (req.url.endsWith('/messages')) {
        const events = [
          { type: 'message_start', message: {
            id: 'pet-fixture', type: 'message', role: 'assistant', model: body.model, content: [],
            stop_reason: null, stop_sequence: null, usage: { input_tokens: 10, output_tokens: 0 },
          } },
          { type: 'content_block_start', index: 0, content_block: { type: 'text', text: '' } },
          { type: 'content_block_delta', index: 0, delta: { type: 'text_delta', text } },
          { type: 'content_block_stop', index: 0 },
          { type: 'message_delta', delta: { stop_reason: 'end_turn', stop_sequence: null }, usage: { output_tokens: 10 } },
          { type: 'message_stop' },
        ]
        return res.end(events.map(event => 'event: ' + event.type + '\ndata: ' + JSON.stringify(event) + '\n\n').join(''))
      }
      const chunk = (delta, finish_reason) => 'data: ' + JSON.stringify({
        id: 'pet-fixture', object: 'chat.completion.chunk', model: body.model,
        choices: [{ index: 0, delta, finish_reason }],
      }) + '\n\n'
      res.end(chunk({ role: 'assistant', content: text }, null) + chunk({}, 'stop') + 'data: [DONE]\n\n')
      return
    }
    const prompt = body.messages.findLast(m => m.role === 'user').content
    const text = typeof prompt === 'string' ? prompt : prompt.map(block => block.text ?? '').join('')
    let content = 'OK'
    if (text.startsWith('{')) {
      const input = JSON.parse(text)
      if (input.proposal) {
        const duplicate = input.related.find(item => item.text === input.proposal.text)
        content = JSON.stringify({
          action: duplicate ? 'noop' : 'create',
          compared_ids: input.related.map(item => item.item_id),
          ...(duplicate ? { target_ids: [duplicate.item_id] } : {
            content: { kind: 'fact', text: input.proposal.text, description: 'Synthetic preference' },
            evidence_ids: input.proposal.evidence_ids,
          }),
          reason: 'Synthetic preference from completed pet dialogue.',
        })
      } else {
        const candidates = [fact, correction].flatMap(text => {
          const evidence = input.evidence?.find(item => JSON.stringify(item).includes(text))
          return evidence ? [{
            kind: 'fact', text, description: 'Synthetic preference', evidence_ids: [evidence.evidence_id],
          }] : []
        })
        content = JSON.stringify({ candidates })
      }
    }
    res.setHeader('content-type', 'application/json')
    res.end(JSON.stringify({
      id: 'pet-fixture', object: 'chat.completion', model: body.model, created: 0,
      choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
      usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 },
    }))
  })
  http.listen(0, '127.0.0.1')
  await once(http, 'listening')
  return {
    calls, url: 'http://127.0.0.1:' + http.address().port,
    close: () => { http.closeAllConnections(); http.close() },
  }
}

test('packaged pet adapter captures, extracts and recalls in real DSH ' + profile, {
  skip: !dsh && 'Set DSH_PET_TEST_BIN or DSH_PET_TEST_DESKTOP_APP for DSH 0.2.2-rc.1',
  timeout: 300_000,
}, async t => {
  const root = await mkdtemp(join(tmpdir(), 'pc-dsh-pet-' + profile + '-'))
  const cleanups = []
  t.after(async () => {
    const failures = []
    for (const cleanup of cleanups.reverse()) {
      try { await cleanup() } catch (error) { failures.push(error) }
    }
    await rm(root, { force: true, recursive: true })
    if (failures.length) throw new AggregateError(failures, 'DSH fixture cleanup failed')
  })
  const model = await fixtureModel()
  cleanups.push(model.close)
  const env = {
    ...Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      !/^(?:POWERCONTEXT_|DEEPSEEK_|OPENAI_|DSH_)/.test(key))),
    HOME: root,
    DSH_HOME: join(root, 'dsh'),
    DSH_AGENTS_HOME: join(root, 'agents'),
    DSH_TELEMETRY_DISABLED: '1',
    DEEPSEEK_API_KEY: 'pet-' + 'test-fixture',
    DEEPSEEK_BASE_URL: model.url + '/v1',
    OPENAI_API_KEY: 'pet-' + 'test-fixture',
    POWERCONTEXT_CLIENT_CONFIG_FILE: join(root, 'clients.json'),
  }
  const run = (file, args) => exec(file, args, { cwd: repo, env, timeout: 120_000 })
  const profileDir = join(env.DSH_HOME, 'profiles', profile)
  if (desktopApp) {
    // Desktop reserves this profile. Let the actual installed application initialize
    // it once; the CLI must refuse to invent it or substitute an npm runtime.
    await mkdir(join(root, 'Documents'))
    const firstLaunch = await startDesktop(desktopApp, root, env)
    try {
      await until(async () => {
        const initialized = await readFile(join(profileDir, 'package.json'), 'utf8').catch(() => '')
        return initialized && (await firstLaunch.appRenderer())
      }, 'Desktop initialized its reserved profile', 80_000)
    } finally {
      await firstLaunch.stop()
    }
  }
  const server = await startPowerContextServer({ env: {
    OPENAI_API_KEY: 'pet-' + 'test-fixture',
    POWERCONTEXT_SERVER_INFERENCE: JSON.stringify({
      generation_model: 'openai-chat:fixture', generation_base_url: model.url + '/v1',
    }),
  } })
  cleanups.push(async () => {
    await server.stop()
    await rm(server.home, { force: true, recursive: true })
  })
  // Switch off the adapter's route while keeping the real Server available for observation.
  let offline = false
  let dropCaptureResponse = false
  let petAuthorizationObserved = false
  const attempts = []
  const proxy = createServer(async (req, res) => {
    try {
      const chunks = []
      for await (const chunk of req) chunks.push(chunk)
      const body = Buffer.concat(chunks).toString()
      if (['/v1/connection/identity', '/v1/atomic-memory/search', '/v1/sources/content'].includes(req.url)) {
        petAuthorizationObserved ||= req.headers.authorization !== undefined
      }
      if (offline) {
        res.writeHead(503, { 'content-type': 'application/json' }).end('{}')
        return
      }
      const upstream = await fetch(server.baseUrl + req.url, {
        method: req.method, redirect: 'error',
        ...(body && { body, headers: { 'content-type': 'application/json' } }),
      })
      const text = await upstream.text()
      if (req.url === '/v1/sources/content' && upstream.status === 202) {
        attempts.push(JSON.parse(body).source_id)
        if (dropCaptureResponse && JSON.parse(body).content.includes(outageTurn)) {
          dropCaptureResponse = false
          res.destroy()
          return
        }
      }
      res.writeHead(upstream.status, { 'content-type': upstream.headers.get('content-type') ?? 'application/json' })
      res.end(text)
    } catch {
      if (!res.headersSent) res.writeHead(502)
      res.end()
    }
  })
  proxy.listen(0, '127.0.0.1')
  await once(proxy, 'listening')
  cleanups.push(() => { proxy.closeAllConnections(); proxy.close() })
  const proxyUrl = 'http://127.0.0.1:' + proxy.address().port

  const createScope = (title, refs) => api(server.baseUrl, '/v1/scopes', { title, summary: title, idempotency_key: title.toLowerCase().replaceAll(' ', '-'), context_references: refs })
  const first = await createScope('Synthetic pet one', [])
  const second = await createScope('Synthetic pet two', [first.scope_id])
  const scope = first.scope_id
  assert.ok(scope && second.scope_id)
  const store = process.env.DSH_PET_TEST_PNPM_STORE
  const cache = process.env.DSH_PET_TEST_PNPM_CACHE
  assert.equal(Boolean(store), Boolean(cache), 'Set both DSH_PET_TEST_PNPM_STORE and DSH_PET_TEST_PNPM_CACHE for offline installs')
  const cachedInstall = store ? ['--offline', '--store-dir', store, '--cache-dir', cache] : []
  await run(dsh, ['plugin', '--profile', profile, 'add', 'dsh-pet@0.4.8', ...cachedInstall])
  if (store) {
    // Subsequent installs, including the CLI under test, must keep using the
    // store that owns this profile's node_modules.
    await appendFile(join(profileDir, 'pnpm-workspace.yaml'),
      '\nstoreDir: ' + JSON.stringify(store) + '\ncacheDir: ' + JSON.stringify(cache) + '\n')
  }
  const pack = await run('npm', ['pack', petPackage, '--pack-destination', root, '--ignore-scripts', '--json'])
  const archive = join(root, JSON.parse(pack.stdout)[0].filename)
  await run(dsh, ['plugin', '--profile', profile, 'add', archive])
  const packagePath = join(profileDir, 'node_modules/powercontext-dsh-pet')
  assert.equal(JSON.parse(await readFile(join(packagePath, 'package.json'), 'utf8')).name, 'powercontext-dsh-pet')
  assert.equal((await readFile(join(packagePath, 'lib/index.js'))).length > 0, true)

  const cli = resolve(repo, '.venv/bin/powercontext')
  const baseSetup = ['setup', 'dsh', '--profile', profile, '--dsh-command', dsh, '--source', repo,
    '--server-url', proxyUrl, '--json']
  // The regular plugin can have a URL-bound token even when this Server has no
  // authentication provider. Pet setup and runtime must use the anonymous identity.
  await mkdir(join(env.DSH_HOME, 'powercontext'), { recursive: true, mode: 0o700 })
  await writeFile(join(env.DSH_HOME, 'powercontext', 'credentials.json'), JSON.stringify({
    version: 1, server_url: proxyUrl, authorization: 'Bearer fixture-token',
  }), { mode: 0o600 })
  const setup = await run(cli, [...baseSetup, '--dsh-pet-memory', 'on',
    '--dsh-pet-bind', 'main=' + scope, '--dsh-pet-bind', 'second=' + second.scope_id])
  assert.equal(JSON.parse(setup.stdout).hosts.dsh.result.dsh_pet_memory.status, 'enabled', setup.stdout)
  const otherProfile = desktopApp ? 'web' : 'desktop'
  await assert.rejects(access(join(env.DSH_HOME, 'profiles', otherProfile, '.powercontext-dsh-pet/bindings.json')),
    { code: 'ENOENT' })
  assert.equal((await run(dsh, ['--version'])).stdout.trim(), '0.2.2-rc.1')
  // Boot the packed adapter, not the local checkout that setup can also install.
  await run(dsh, ['plugin', '--profile', profile, 'add', archive])

  const petHome = join(env.DSH_HOME, 'dsh-pet')
  await mkdir(petHome, { recursive: true })
  const petConfig = {
    chatMemoryRounds: 1,
    chatModel: { provider: 'deepseek-official', model: 'deepseek-v4-flash' },
    pets: [{ id: 'main', name: 'Fixture One', whisperEnabled: false }, { id: 'second', name: 'Fixture Two', whisperEnabled: false }],
  }
  const configurePet = rounds => writeFile(join(petHome, 'main-config.jsonc'),
    JSON.stringify({ ...petConfig, chatMemoryRounds: rounds }))
  await configurePet(1)
  const patch = desktopApp ? join(profileDir, 'cordis.patch.yml') : join(root, 'model-patch.json')
  await writeFile(patch, JSON.stringify([
    { id: 'llm-deepseek', config: { baseURL: model.url + '/v1', apiKeyEnv: 'DEEPSEEK_API_KEY', thinking: 'disabled' } },
  ]))
  const statusFile = join(profileDir, '.powercontext-dsh-pet/status.json')
  const status = async () => JSON.parse(await readFile(statusFile, 'utf8').catch(() => '{}'))

  let child
  let desktop
  async function stopHost() {
    if (desktop) {
      await desktop.stop()
      desktop = undefined
    }
    if (!child || child.exitCode !== null || child.signalCode !== null) return
    const exited = once(child, 'exit')
    child.kill('SIGTERM')
    await exited
  }
  cleanups.push(stopHost)

  let cookie
  let web
  async function boot() {
    await rm(statusFile, { force: true })
    if (desktopApp) {
      desktop = await startDesktop(desktopApp, root, env)
      await until(desktop.appRenderer, 'packaged Desktop renderer')
      await until(async () => (await status()).provider === 'registered', 'Desktop memory provider registration')
      return
    }
    const port = await unusedPort()
    web = 'http://127.0.0.1:' + port
    child = spawn(dsh, ['web', '--patch', patch, '--host', '127.0.0.1', '--port', String(port), '--no-open'], {
      cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'],
    })
    let logs = ''
    child.stdout.on('data', part => { logs += part })
    child.stderr.on('data', part => { logs += part })
    let token
    await until(() => {
      if (child.exitCode !== null) throw new Error('DSH exited: ' + logs.replace(/token=\S+/g, 'token=[redacted]'))
      token = /token=([^\s]+)/.exec(logs)?.[1]
      return token
    }, 'Web launch token')
    const entry = await fetch(web + '/?token=' + encodeURIComponent(token), { redirect: 'manual' })
    cookie = entry.headers.get('set-cookie')?.split(';')[0]
    assert.ok(cookie, 'DSH launch token should create an owner cookie')
    await until(async () => (await status()).provider === 'registered', 'dsh-pet provider registration')
  }

  async function chat(pet, text) {
    const route = '/dsh-pet-7340/chat?surface=' + profile + '&pet=' + encodeURIComponent(pet)
    const request = {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ text }),
    }
    const { status, result } = desktop
      ? await desktop.evaluate(`(async () => {
        const response = await fetch(${JSON.stringify(route)}, ${JSON.stringify(request)})
        return { status: response.status, result: await response.json() }
      })()`)
      : await fetch(web + route, {
        ...request, headers: { ...request.headers, cookie, origin: web }, signal: AbortSignal.timeout(20_000),
      }).then(async response => ({ status: response.status, result: await response.json() }))
    assert.equal(status, 200, JSON.stringify(result))
    assert.equal(result.ok, true, JSON.stringify(result))
    return result
  }

  await boot()
  const doctor = await run(cli, ['doctor', 'dsh', '--profile', profile, '--dsh-command', dsh, '--json'])
  assert.equal(JSON.parse(doctor.stdout).hosts.dsh.checks.dsh_pet_memory.checks.runtime, 'registered')
  await chat('main', fact)
  const sources = () => api(server.baseUrl, '/v1/scopes/' + scope + '/sources')
  await until(async () => (await sources()).items.some(item => item.source_id.startsWith('dsh-pet:')),
    'completed exchange accepted as a Source')
  await api(server.baseUrl, '/v1/memory/flush', { scope_id: scope })
  const memory = await api(server.baseUrl, '/v1/atomic-memory/search', {
    scope_id: scope, query: 'favorite tea', mode: 'text',
  })
  assert.ok(memory.hits.some(hit => hit.memory.text === fact), JSON.stringify(memory))
  await chat('main', 'An unrelated round about bicycles.')
  await stopHost()
  await boot()
  assert.equal((await chat('main', question)).text, fact)
  assert.match(JSON.stringify(model.calls.filter(call => call.stream).at(-1)), /Untrusted historical evidence/)
  assert.equal((await chat('second', question)).text, 'I do not know.')
  assert.doesNotMatch(JSON.stringify(model.calls.filter(call => call.stream).at(-1)), /silver hibiscus/)

  await chat('main', correction)
  const capturedCorrection = async () => (await sources()).items.some(item => JSON.stringify(item.content).includes(correction))
  await until(capturedCorrection, 'corrected preference captured')
  await api(server.baseUrl, '/v1/memory/flush', { scope_id: scope })
  await chat('main', 'Another unrelated bicycle round.')
  const historyPath = join(petHome, 'memory.json')
  const replayHistory = await readFile(historyPath)
  const replay = async (expected, injected = false) => {
    const samples = []
    for (let i = 0; i < 3; i++) {
      await writeFile(historyPath, replayHistory)
      const started = performance.now()
      const answer = await chat('main', question)
      assert.equal(answer.text, expected)
      const input = JSON.stringify(model.calls.filter(call => call.stream).at(-1))
      assert.equal(input.includes('Untrusted historical evidence'), injected)
      samples.push({
        latency_ms: Math.round(performance.now() - started),
        model_request_bytes: Buffer.byteLength(input),
      })
    }
    const latencies = samples.map(sample => sample.latency_ms).sort((a, b) => a - b)
    return { samples, latency_ms_p50: latencies[1], latency_ms_p95: latencies[2] }
  }
  const pc = await replay(correction, true)

  // A completed chat stays available when the adapter route is down. Replay must
  // retain the Source ID even if the real Server stores the turn but loses the reply.
  offline = true
  const count = attempts.length
  await chat('main', outageTurn)
  await until(async () => (await status()).bindings?.find(b => b.scope_id === scope)?.pending > 0, 'turn queued offline')
  assert.equal(attempts.length, count)
  await stopHost()
  await boot()
  dropCaptureResponse = true
  offline = false
  const matchesOutageTurn = item => JSON.stringify(item.content).includes(outageTurn)
  await until(async () => {
    const captured = (await sources()).items.filter(matchesOutageTurn)
    return captured.length === 1 && attempts.filter(id => id === captured[0].source_id).length >= 2
  }, 'real Server receives exact retry after restart and lost response', 45_000)

  await run(cli, ['setup', 'dsh', '--profile', profile, '--dsh-command', dsh, '--dsh-pet-memory', 'off', '--json'])
  await until(async () => (await status()).provider === 'off', 'provider disabled without stopping chat')
  const native = await replay('I do not know.')
  await configurePet(100)
  const full = await replay(correction)
  assert.equal(petAuthorizationObserved, false, 'pet requests must not send the stored token')
  t.diagnostic(JSON.stringify({
    scenario: 'corrected preference past the recent window',
    native_recent: { ...native, target_check: false },
    powercontext_same_window: { ...pc, target_check: true },
    full_history: { ...full, target_check: true },
    model_provider_usage_and_cost: 'unknown (deterministic local fixture; no provider billing)',
  }))
})
