/*
 * Copyright (c) 2026 OceanBase.
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
 * Unless required by applicable law or agreed to in writing, software distributed under the License
 * is distributed on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and limitations under the License.
 */

import assert from 'node:assert/strict'
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { cp, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { queryObservations, stageResult, startObservation } from '../shared/observations.mjs'
import { bindingKeys, sha256 } from '../shared/scope.mjs'
import { request } from '../shared/transport.mjs'

const plugin = fileURLToPath(new URL('..', import.meta.url))

test('runtime observations preserve opaque Scope IDs exactly', async () => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-opaque-runtime-'))
  try {
    const settings = { serverUrl: 'http://127.0.0.1:8000', serverUrlSource: 'installed', capturePrompts: true }
    for (const [index, scopeId] of ['project.alpha', ' 项目 / alpha 🚀 ', '🚀'.repeat(256), '\ufeff'].entries()) {
      const input = { cwd: root, sessionId: `opaque-${index}`, hookEventName: 'UserPromptSubmit' }
      const observation = await startObservation(input, settings, bindingKeys(input, settings), root)
      await observation.stage('scope', stageResult('resolved'), scopeId)
      await observation.finish()
      const status = await queryObservations(input, settings, root)
      assert.equal(status.status, 'observed')
      assert.equal(status.observation.scope_id, scopeId)
      assert.deepEqual(status.issues, [])
    }
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('Hook observations preserve latest-attempt order, unknown writes and content-free status', async () => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-runtime-'))
  let slowStarted
  const slowReady = new Promise(resolve => { slowStarted = resolve })
  let captures = 0, protectedStatus = 200, captureStatus = 202, receiptValid = true, empty = false
  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = raw ? JSON.parse(raw) : {}
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/health/ready') res.end('{}')
    else if (req.url === '/v1/capabilities') { res.statusCode = protectedStatus; res.end('{}') }
    else if (req.url === '/v1/scope-bindings/resolve') res.end('{"scope_id":"scope-runtime"}')
    else if (req.url === '/v1/context/prepare') {
      if (body.query === 'older private prompt') {
        slowStarted()
        await new Promise(resolve => setTimeout(resolve, 700))
      }
      const content = 'Private returned history marker.'
      res.end(JSON.stringify({ schema: 'powercontext.prepared-context.v1', status: empty ? 'empty' : 'ready',
        content: empty ? null : content, content_bytes: empty ? 0 : Buffer.byteLength(content) }))
    } else if (req.url === '/v1/sources/content') {
      const position = ++captures
      if (body.content === 'unknown capture private prompt') await new Promise(resolve => setTimeout(resolve, 1300))
      res.statusCode = captureStatus
      res.end(JSON.stringify({ status: 'accepted', source: { name: 'content', source_id: receiptValid ? body.source_id : 'incorrect' }, position }))
    } else { res.statusCode = 404; res.end('{}') }
  })
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const installed = join(root, 'plugin'), cwd = join(root, 'workspace'), data = join(root, 'data')
    await cp(plugin, installed, { recursive: true, filter: path => !path.includes('node_modules') })
    await mkdir(cwd)
    const settingsFile = join(installed, 'powercontext.json')
    await writeFile(settingsFile, JSON.stringify({ server_url: `http://127.0.0.1:${server.address().port}` }))
    function run(name, args = [], input, extra = {}) {
      return new Promise((resolve, reject) => {
        const env = { ...process.env }
        for (const key of Object.keys(env)) if (key.startsWith('POWERCONTEXT_ZCODE_') || key === 'ZCODE_SESSION_ID') delete env[key]
        const child = spawn(process.execPath, [join(installed, name), ...args], {
          env: { ...env, ZCODE_PLUGIN_DATA: data, POWERCONTEXT_ZCODE_AUTHORIZATION: 'Bearer synthetic-test-only', ...extra },
          stdio: ['pipe', 'pipe', 'pipe'],
        })
        let stdout = '', stderr = ''
        child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk })
        child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
        child.on('error', reject)
        child.on('close', code => resolve({ code, stdout, stderr, result: JSON.parse(stdout) }))
        child.stdin.end(input ? JSON.stringify(input) : '')
      })
    }
    const hook = (prompt, extra = {}) => run('hooks/user_prompt_submit.mjs', [], {
      hookEventName: 'UserPromptSubmit', cwd, sessionId: 'session-runtime', prompt,
    }, extra)
    const status = () => run('scripts/status.mjs', ['--cwd', cwd, '--session-id', 'session-runtime'])
    const older = hook('older private prompt')
    await slowReady
    const newer = await hook('newer private prompt')
    assert.equal(newer.code, 0)
    await older
    const latest = await status()
    assert.equal(latest.result.status, 'observed')
    assert.equal(latest.result.observation.stages.capture.source_position, 1)
    assert.equal(latest.result.observation.stages.context_output.state, 'emitted')
    assert.equal(latest.result.observation.scope_id, 'scope-runtime')
    assert.doesNotMatch(latest.stdout, /private prompt|Private returned|synthetic-test-only|session-runtime/)

    await hook('unknown capture private prompt')
    const unknown = await status()
    assert.equal(unknown.result.observation.stages.capture.state, 'unknown')
    assert.equal(unknown.result.observation.stages.capture.code, 'timeout')
    assert.equal(unknown.result.observation.stages.prepare.state, 'ready')
    assert.equal(captures, 3) // The mock accepted it before its reply timed out.

    for (const [statusCode, expected] of [[401, 'rejected'], [403, 'rejected'], [503, 'unknown']]) {
      captureStatus = statusCode
      await hook('ordinary prompt for failure probe')
      assert.equal((await status()).result.observation.stages.capture.state, expected)
    }
    captureStatus = 202
    receiptValid = false
    await hook('ordinary prompt for invalid receipt')
    assert.equal((await status()).result.observation.stages.capture.state, 'unknown')
    receiptValid = true
    empty = true
    await hook('ordinary prompt with capture disabled', { POWERCONTEXT_ZCODE_CAPTURE_PROMPTS: 'false' })
    const disabled = await status()
    assert.equal(disabled.result.observation.stages.prepare.state, 'empty')
    assert.equal(disabled.result.observation.stages.capture.reason, 'capture_disabled')
    assert.equal(disabled.result.status, 'configuration_mismatch') // The query process has capture enabled.
    await hook('api_key=synthetic-secret-value')
    assert.equal((await status()).result.observation.stages.capture.reason, 'sensitive_content')
    await hook('a'.repeat(200_001))
    assert.equal((await status()).result.observation.stages.capture.reason, 'source_too_long')
    empty = false

    const badData = join(root, 'not-a-directory')
    await writeFile(badData, 'blocked')
    const degraded = await hook('ordinary task with unavailable state', { ZCODE_PLUGIN_DATA: badData })
    assert.equal(degraded.code, 0)
    assert.match(degraded.result.hookSpecificOutput.additionalContext, /Private returned/)
    assert.match(degraded.stderr, /runtime_state_unavailable/)

    // Stored fields outside the schema must not escape through status output.
    const files = await readdir(join(data, 'runtime'))
    const target = join(data, 'runtime', files[0])
    const stored = JSON.parse(await readFile(target, 'utf8'))
    stored.private_body = 'injected private body'
    await writeFile(target, JSON.stringify(stored))
    assert.doesNotMatch((await status()).stdout, /injected private body/)

    const broken = join(data, 'runtime', 'attempt-00000000-0000-0000-0000-000000000000.json')
    await writeFile(broken, '{"schema":"future-private-version","private":"do not echo"}')
    const corrupt = await status()
    assert.equal(corrupt.code, 1)
    assert.deepEqual(corrupt.result.issues, ['unsupported_state'])
    assert.doesNotMatch(corrupt.stdout, /do not echo|future-private-version/)
    await rm(broken)

    protectedStatus = 401
    const doctor = await run('scripts/doctor.mjs')
    assert.equal(doctor.result.checks.server.status, 'ok')
    assert.equal(doctor.result.checks.protected_api.code, 'unauthorized')
    assert.equal(doctor.result.checks.mcp_session.status, 'skipped')
    assert.equal(doctor.code, 1)
    await writeFile(settingsFile, JSON.stringify({ server_url: 'http://127.0.0.1:1' }))
    assert.equal((await status()).result.status, 'configuration_mismatch')
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})

test('status isolates profiles and sessions, retains incomplete attempts and bounds completed history', async t => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-retention-'))
  const data = join(root, 'data')
  const settings = { serverUrl: 'http://127.0.0.1:1', serverUrlSource: 'installed', capturePrompts: true }
  const input = { hookEventName: 'UserPromptSubmit', cwd: root, sessionId: 'retention-session' }
  try {
    const keys = bindingKeys(input, settings)
    for (let i = 0; i < 70; i++) {
      const observation = await startObservation(input, settings, keys, data)
      await observation.stage('scope', stageResult('resolved'), 'scope-retention')
      await observation.finish()
    }
    const unfinished = await startObservation(input, settings, keys, data)
    await unfinished.stage('prepare', stageResult('running'))
    const before = performance.now()
    const report = await queryObservations(input, settings, data)
    t.diagnostic(`query and retention of 71 records: ${(performance.now() - before).toFixed(1)} ms`)
    assert.equal(report.incomplete, true)
    assert.equal(report.observation.stages.prepare.state, 'running')
    assert.equal((await readdir(join(data, 'runtime'))).length, 65)
    assert.equal((await queryObservations({ ...input, sessionId: 'another-session' }, settings, data)).status, 'not_observed')
    assert.equal((await queryObservations(input, settings, join(root, 'other-profile'))).status, 'not_observed')
    assert.equal((await queryObservations({ ...input, sessionId: 'another-session' }, settings, data, true)).selection, 'workspace_latest')
    const latest = report.observation
    latest.started_at = Date.now() - 300_001
    const path = join(data, 'runtime', `attempt-${latest.attempt_id}.json`)
    await writeFile(path, JSON.stringify(latest))
    // An isolated session prevents newer records from obscuring a stale interrupted attempt.
    const oldData = join(root, 'old-data')
    await mkdir(join(oldData, 'runtime'), { recursive: true })
    latest.identity.profile = sha256(oldData)
    await writeFile(join(oldData, 'runtime', `attempt-${latest.attempt_id}.json`), JSON.stringify(latest))
    const stale = await queryObservations(input, settings, oldData)
    assert.equal(stale.stale, true)
    assert.equal(stale.incomplete, true)
    assert.equal(stale.observation.stages.capture.state, 'not_observed')
    for (let i = 0; i < 513; i++) await writeFile(join(oldData, 'runtime', `unowned-${i}`), '')
    await assert.rejects(queryObservations(input, settings, oldData), { message: 'runtime_capacity_exceeded' })
    assert.equal((await readdir(join(oldData, 'runtime'))).length, 514)
  } finally { await rm(root, { recursive: true, force: true }) }
})

test('an expired budget proves the write was never attempted', async () => {
  try {
    await request({ serverUrl: 'http://127.0.0.1:1' }, 'POST', '/v1/sources/content', {}, AbortSignal.abort())
    assert.fail('Expired budget must reject')
  } catch (error) { assert.equal(error.requestSent, undefined) }
})
