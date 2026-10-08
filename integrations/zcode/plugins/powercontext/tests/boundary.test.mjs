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
import { spawn } from 'node:child_process'
import { createServer } from 'node:http'
import { cp, mkdir, mkdtemp, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'
import test from 'node:test'
import { authorizeUnknownFlushRetry, beginCaptureTracking, flushBoundary, queryPending } from '../shared/pending.mjs'
import { bindingKeys, resolveScope } from '../shared/scope.mjs'

const plugin = fileURLToPath(new URL('..', import.meta.url))
async function leaseWindow() {
  const remaining = 5000 - Date.now() % 5000
  if (remaining < 1200) await new Promise(resolve => setTimeout(resolve, remaining + 30))
}

test('opaque Scope IDs survive resolution, receipt readback and boundary processing across sessions', async () => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-opaque-'))
  let scopeId, position, flushes = 0
  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/v1/scope-bindings/resolve') res.end(JSON.stringify({ scope_id: scopeId }))
    else {
      assert.equal(req.url, '/v1/memory/flush')
      assert.deepEqual(JSON.parse(raw), { scope_id: scopeId })
      flushes++
      res.end(JSON.stringify({ status: 'processed', previous_cursor: 0, current_cursor: position,
        high_watermark: position, processed_source_count: position }))
    }
  })
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const settings = { serverUrl: `http://127.0.0.1:${server.address().port}`, boundaryFlush: true }
    const data = join(root, 'data'), owners = []
    const ids = ['project.alpha', ' 项目 / alpha 🚀 ', '🚀'.repeat(256), '\ufeff']
    for (const [index, id] of ids.entries()) {
      scopeId = id; position = index + 1
      const input = { cwd: root, sessionId: `opaque-${index}` }, keys = bindingKeys(input, settings)
      const resolved = await resolveScope(input, settings, AbortSignal.timeout(800), keys)
      assert.equal(resolved.scopeId, id)
      const tracking = await beginCaptureTracking(input, settings, keys, id, data)
      await tracking.accepted(position)
      owners.push({ input, id, position, keys })
      // Read earlier sessions while all receipts share one directory; no valid ID may poison the snapshot.
      for (const owner of owners) {
        const status = await queryPending(owner.input, settings, data)
        assert.equal(status.scopes[0].scope_id, owner.id)
        assert.equal(status.scopes[0].target_position, owner.position)
      }
    }
    for (const owner of owners) {
      scopeId = owner.id; position = owner.position
      await leaseWindow()
      const result = await flushBoundary(owner.input, settings, owner.keys, async () => ({ scopeId }),
        AbortSignal.timeout(800), data)
      assert.equal(result.state, 'cursor_reached')
    }
    assert.equal(flushes, ids.length)
    assert.equal((await queryPending(owners[0].input, settings, data)).scopes.length, 0)
    for (const invalid of ['', ' \u0085\u001c ', '🚀'.repeat(257)]) {
      await assert.rejects(beginCaptureTracking(owners[0].input, settings, owners[0].keys, invalid, data),
        { message: 'invalid_pending' })
    }
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})

test('pause publication survives process interruption and legacy partial pauses require explicit recovery',
  { timeout: 25000 }, async () => {
    const root = await mkdtemp(join(tmpdir(), 'pc-zcode-pause-crash-'))
    let flushes = 0
    const server = createServer(async (req, res) => {
      for await (const chunk of req) { void chunk }
      res.setHeader('Content-Type', 'application/json')
      if (req.url === '/v1/scope-bindings/resolve') { res.end('{"scope_id":"scope-crash"}'); return }
      assert.equal(req.url, '/v1/memory/flush')
      flushes++
      res.end('{"status":"processed","previous_cursor":0,"current_cursor":1,"high_watermark":1,"processed_source_count":1}')
    })
    try {
      await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
      const settings = { serverUrl: `http://127.0.0.1:${server.address().port}`, boundaryFlush: true }
      const loader = join(root, 'interrupt.mjs')
      await writeFile(loader, `
        import fs from 'node:fs/promises'
        import { syncBuiltinESMExports } from 'node:module'
        const nativeOpen = fs.open, nativeWrite = fs.writeFile, nativeLink = fs.link
        async function stop() {
          process.send('interruption-ready')
          await new Promise(() => { setInterval(() => {}, 1000) })
        }
        fs.open = async (...args) => {
          const file = await nativeOpen(...args)
          if (process.env.PC_TEST_PHASE === 'before' && /pause-.*\\.json$/.test(args[0]) && args[1] === 'wx') await stop()
          return file
        }
        fs.writeFile = async (...args) => {
          if (process.env.PC_TEST_PHASE === 'before' && /pause-.*\\.tmp$/.test(args[0])) {
            const file = await nativeOpen(args[0], 'wx', 0o600)
            await file.close()
            await stop()
          }
          return nativeWrite(...args)
        }
        fs.link = async (...args) => {
          await nativeLink(...args)
          if (process.env.PC_TEST_PHASE === 'after' && /pause-.*\\.json$/.test(args[1])) await stop()
        }
        syncBuiltinESMExports()
      `)
      const cases = []
      for (const phase of ['before', 'after', 'after', 'after']) {
        const data = join(root, `data-${cases.length}`)
        const input = { cwd: root, sessionId: `crash-${cases.length}` }, keys = bindingKeys(input, settings)
        const tracking = await beginCaptureTracking(input, settings, keys, 'scope-crash', data)
        await tracking.accepted(1)
        await leaseWindow()
        const script = `
          import { flushBoundary } from ${JSON.stringify(new URL('../shared/pending.mjs', import.meta.url).href)}
          const [input, settings, keys, data] = JSON.parse(process.env.PC_TEST_CASE)
          await flushBoundary(input, settings, keys, async () => ({scopeId: 'scope-crash'}), AbortSignal.timeout(800), data)
        `
        await new Promise((resolve, reject) => {
          const child = spawn(process.execPath, ['--import', pathToFileURL(loader).href, '--input-type=module', '--eval', script], {
            env: { ...process.env, PC_TEST_PHASE: phase, PC_TEST_CASE: JSON.stringify([input, settings, keys, data]) },
            stdio: ['ignore', 'ignore', 'pipe', 'ipc'],
          })
          let ready = false, stderr = ''
          const timer = setTimeout(() => { child.kill('SIGKILL'); reject(new Error('interruption_not_reached')) }, 5000)
          child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
          child.on('error', error => { clearTimeout(timer); reject(error) })
          child.on('message', message => { if (message === 'interruption-ready') { ready = true; child.kill('SIGKILL') } })
          child.on('close', () => { clearTimeout(timer); ready ? resolve() : reject(new Error(stderr)) })
        })
        const status = await queryPending(input, settings, data)
        assert.equal(status.scopes[0].receipt_count, 1)
        assert.equal(status.scopes[0].unknown_flush_paused, phase === 'after')
        const pauseName = (await readdir(join(data, 'pending'))).find(name => /^pause-.*\.json$/.test(name))
        assert.equal(Boolean(pauseName), phase === 'after')
        if (pauseName) assert.equal(JSON.parse(await readFile(join(data, 'pending', pauseName), 'utf8')).scope_id, 'scope-crash')
        cases.push({ input, keys, data, pauseName })
      }
      assert.equal(flushes, 0, 'No request may precede complete pause publication')
      await new Promise(resolve => setTimeout(resolve, 5100))
      const first = cases.shift()
      assert.equal((await flushBoundary(first.input, settings, first.keys, async () => ({ scopeId: 'scope-crash' }),
        AbortSignal.timeout(800), first.data)).state, 'cursor_reached')
      assert.equal(flushes, 1)
      for (const [index, owner] of cases.entries()) {
        const pause = join(owner.data, 'pending', owner.pauseName)
        // Existing installations may already contain an empty or partially written final pause.
        await writeFile(pause, ['', '{"schema":', Buffer.from([0xc3])][index])
        assert.equal((await queryPending(owner.input, settings, owner.data)).scopes[0].unknown_flush_paused, true)
        assert.equal((await flushBoundary(owner.input, settings, owner.keys, async () => ({ scopeId: 'scope-crash' }),
          AbortSignal.timeout(800), owner.data)).reason, 'unknown_flush_paused')
        const other = { ...owner.input, sessionId: 'other-session' }
        assert.equal((await queryPending(other, settings, owner.data)).scopes.length, 0)
        assert.equal(await authorizeUnknownFlushRetry(other, settings, bindingKeys(other, settings), 'scope-crash', owner.data), 'not_paused')
        assert.equal(flushes, 1, 'Interrupted pauses never permit an automatic retry')
        await leaseWindow()
        if (index === 0) {
          const installed = join(root, 'plugin')
          await cp(plugin, installed, { recursive: true, filter: path => !path.includes('node_modules') })
          await writeFile(join(installed, 'powercontext.json'), JSON.stringify({ server_url: settings.serverUrl }))
          const env = { ...process.env, ZCODE_PLUGIN_DATA: owner.data }
          for (const key of Object.keys(env)) if (key.startsWith('POWERCONTEXT_ZCODE_') || key === 'ZCODE_SESSION_ID') delete env[key]
          const control = accept => new Promise((resolve, reject) => {
            const args = [join(installed, 'scripts/pending.mjs'), 'resume-flush', '--cwd', root,
              '--session-id', owner.input.sessionId, '--scope-id', 'scope-crash', ...(accept ? ['--accept-unknown-outcome'] : [])]
            const child = spawn(process.execPath, args, { env, stdio: ['ignore', 'pipe', 'pipe'] })
            let stdout = '', stderr = ''
            child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk })
            child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
            child.on('error', reject)
            child.on('close', code => resolve({ code, stdout, stderr, result: JSON.parse(stdout) }))
          })
          assert.equal((await control(false)).code, 1)
          assert.equal((await queryPending(owner.input, settings, owner.data)).scopes[0].unknown_flush_paused, true)
          await leaseWindow()
          const result = await control(true)
          assert.equal(result.code, 0, result.stderr)
          assert.equal(result.result.status, 'retry_authorized')
        } else assert.equal(await authorizeUnknownFlushRetry(owner.input, settings, owner.keys, 'scope-crash', owner.data), 'retry_authorized')
        const recovered = await queryPending(owner.input, settings, owner.data)
        assert.equal(recovered.scopes[0].unknown_flush_paused, false)
        assert.equal(recovered.scopes[0].receipt_count, 1)
        // Without a matching persisted receipt, a malformed marker does not establish ownership.
        await writeFile(pause, '')
        for (const name of await readdir(join(owner.data, 'pending'))) {
          if (name.startsWith('receipt-')) await rm(join(owner.data, 'pending', name))
        }
        await assert.rejects(authorizeUnknownFlushRetry(owner.input, settings, owner.keys, 'scope-crash', owner.data),
          { message: 'invalid_pending' })
      }
    } finally {
      await new Promise(resolve => server.close(resolve))
      await rm(root, { recursive: true, force: true })
    }
  })

test('pending preserves concurrent receipts, pauses unknown writes and bounds tracking capacity', { timeout: 25000 }, async t => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-boundary-'))
  let flushes = 0, delay = 0, cursor = 1, received
  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    assert.deepEqual(body, { scope_id: 'scope-boundary' })
    flushes++
    received?.()
    if (delay) await new Promise(resolve => setTimeout(resolve, delay))
    res.setHeader('Content-Type', 'application/json')
    res.end(JSON.stringify({ status: cursor ? 'processed' : 'idle', previous_cursor: 0,
      current_cursor: cursor, high_watermark: 10, processed_source_count: cursor }))
  })
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const settings = { serverUrl: `http://127.0.0.1:${server.address().port}`, boundaryFlush: true }
    const input = { sessionId: 'boundary-secret-session', cwd: root }
    const keys = bindingKeys(input, settings)
    const resolveCurrent = async () => ({ scopeId: 'scope-boundary' })
    const data = join(root, 'data')
    const first = await beginCaptureTracking(input, settings, keys, 'scope-boundary', data)
    await first.accepted(1)
    const receiptName = (await readdir(join(data, 'pending'))).find(name => name.startsWith('receipt-'))
    const recoveredGuard = JSON.parse(await readFile(join(data, 'pending', receiptName), 'utf8'))
    recoveredGuard.kind = 'track'
    delete recoveredGuard.position
    await writeFile(join(data, 'pending', receiptName.replace('receipt-', 'track-')), JSON.stringify(recoveredGuard))
    assert.equal((await queryPending(input, settings, data)).scopes[0].tracking_incomplete, false)
    assert.ok(!(await readdir(join(data, 'pending'))).some(name => name.startsWith('track-')))
    delay = 300
    const requestArrived = new Promise(resolve => { received = resolve })
    await leaseWindow()
    const flushing = flushBoundary(input, settings, keys, resolveCurrent, AbortSignal.timeout(800), data)
    await requestArrived
    const later = await beginCaptureTracking(input, settings, keys, 'scope-boundary', data)
    await later.accepted(2)
    const concurrent = await flushBoundary(input, settings, keys, resolveCurrent, AbortSignal.timeout(800), data)
    assert.ok(['unknown', 'skipped'].includes(concurrent.state))
    assert.equal((await flushing).state, 'cursor_reached') // Only the snapshot target was reached.
    assert.equal(flushes, 1)
    const remaining = (await readdir(join(data, 'pending'))).filter(name => name.startsWith('receipt-'))
    assert.equal(remaining.length, 1)
    assert.equal(JSON.parse(await readFile(join(data, 'pending', remaining[0]), 'utf8')).position, 2)
    const otherWorkspace = join(root, 'other-workspace')
    await mkdir(otherWorkspace)
    assert.equal((await queryPending({ ...input, cwd: otherWorkspace }, settings, data)).scopes[0].target_position, 2)

    const unknownData = join(root, 'unknown')
    const unknown = await beginCaptureTracking(input, settings, keys, 'scope-boundary', unknownData)
    await unknown.accepted(3)
    delay = 1200
    await leaseWindow()
    const result = await flushBoundary(input, settings, keys, resolveCurrent, AbortSignal.timeout(800), unknownData)
    assert.equal(result.state, 'unknown')
    const afterUnknown = flushes
    await new Promise(resolve => setTimeout(resolve, 5100))
    const retried = await flushBoundary(input, settings, keys, resolveCurrent, AbortSignal.timeout(800), unknownData)
    assert.equal(retried.reason, 'unknown_flush_paused')
    assert.equal(flushes, afterUnknown)
    assert.ok((await readdir(join(unknownData, 'pending'))).some(name => name.startsWith('pause-')))
    const pauseName = (await readdir(join(unknownData, 'pending'))).find(name => name.startsWith('pause-'))
    const pausePayload = await readFile(join(unknownData, 'pending', pauseName), 'utf8')
    const status = await queryPending(input, settings, unknownData)
    assert.equal(status.scopes[0].unknown_flush_paused, true)
    assert.equal(status.scopes[0].target_position, 3)
    await leaseWindow()
    assert.equal(await authorizeUnknownFlushRetry(input, settings, keys, 'scope-boundary', unknownData), 'retry_authorized')
    assert.equal((await queryPending(input, settings, unknownData)).scopes[0].unknown_flush_paused, false)
    for (const name of await readdir(join(unknownData, 'pending'))) {
      if (name.startsWith('receipt-')) await rm(join(unknownData, 'pending', name))
    }
    await writeFile(join(unknownData, 'pending', pauseName), pausePayload)
    const orphanPause = await queryPending(input, settings, unknownData)
    assert.equal(orphanPause.scopes[0].receipt_count, 0)
    assert.equal(orphanPause.scopes[0].unknown_flush_paused, true)

    const behindData = join(root, 'behind')
    const behind = await beginCaptureTracking(input, settings, keys, 'scope-boundary', behindData)
    await behind.accepted(7)
    cursor = 0; delay = 0
    await leaseWindow()
    const idle = await flushBoundary(input, settings, keys, resolveCurrent, AbortSignal.timeout(800), behindData)
    assert.equal(idle.state, 'pending')
    assert.equal(idle.current_cursor, 0)
    const inventory = await queryPending(input, settings, behindData)
    assert.equal(inventory.scopes[0].target_position, 7)
    assert.equal(inventory.scopes[0].unknown_flush_paused, false)

    assert.equal((await flushBoundary(input, { ...settings, boundaryFlush: false }, keys, resolveCurrent,
      AbortSignal.timeout(800), data)).reason, 'boundary_flush_disabled')
    assert.equal((await flushBoundary(input, settings, keys, async () => ({ scopeId: 'different-scope' }),
      AbortSignal.timeout(800), data)).reason, 'scope_changed')
    assert.equal((await flushBoundary(input, { ...settings, serverUrl: 'http://127.0.0.1:1' }, keys, resolveCurrent,
      AbortSignal.timeout(800), data)).reason, 'no_pending')
    const guard = await beginCaptureTracking(input, settings, keys, 'scope-boundary', data)
    assert.equal((await flushBoundary(input, settings, keys, resolveCurrent, AbortSignal.timeout(800), data)).reason, 'tracking_incomplete')
    await guard.rejected()

    const capacity = join(root, 'capacity')
    const before = performance.now()
    for (let position = 1; position <= 256; position++) {
      const tracking = await beginCaptureTracking(input, settings, keys, 'scope-boundary', capacity)
      await tracking.accepted(position)
    }
    t.diagnostic(`256 receipt writes with bounded preflight: ${(performance.now() - before).toFixed(1)} ms`)
    await assert.rejects(beginCaptureTracking(input, settings, keys, 'scope-boundary', capacity), { message: 'pending_capacity_exceeded' })
    assert.equal((await flushBoundary(input, settings, keys, resolveCurrent, AbortSignal.timeout(800), capacity)).reason, 'tracking_incomplete')
    const serialized = (await Promise.all((await readdir(join(capacity, 'pending')))
      .map(name => readFile(join(capacity, 'pending', name), 'utf8')))).join('')
    assert.doesNotMatch(serialized, /boundary-secret-session|Bearer|private prompt/)
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})

test('real Stop process observes a slow unknown flush and exits without continuing the turn', { timeout: 10000 }, async () => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-stop-'))
  let flushes = 0
  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/v1/scope-bindings/resolve') res.end('{"scope_id":"scope-boundary"}')
    else if (req.url === '/v1/memory/flush') {
      flushes++
      await new Promise(resolve => setTimeout(resolve, 1500))
      res.end('{"status":"processed","previous_cursor":0,"current_cursor":1,"high_watermark":1,"processed_source_count":1}')
    } else { res.statusCode = 404; res.end('{}') }
  })
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const installed = join(root, 'plugin'), data = join(root, 'data')
    await cp(plugin, installed, { recursive: true, filter: path => !path.includes('node_modules') })
    const settings = { serverUrl: `http://127.0.0.1:${server.address().port}`, boundaryFlush: true }
    await writeFile(join(installed, 'powercontext.json'), JSON.stringify({ server_url: settings.serverUrl, boundary_flush: true }))
    const input = { hookEventName: 'Stop', sessionId: 'stop-process-session', cwd: root, stopHookActive: false }
    const keys = bindingKeys(input, settings)
    const tracking = await beginCaptureTracking(input, settings, keys, 'scope-boundary', data)
    await tracking.accepted(1)
    await leaseWindow()
    const before = performance.now()
    const result = await new Promise((resolve, reject) => {
      const env = { ...process.env, ZCODE_PLUGIN_DATA: data }
      for (const key of Object.keys(env)) if (key.startsWith('POWERCONTEXT_ZCODE_') || key === 'ZCODE_SESSION_ID') delete env[key]
      const child = spawn(process.execPath, [join(installed, 'hooks/stop.mjs')], { env, stdio: ['pipe', 'pipe', 'pipe'] })
      let stdout = '', stderr = ''
      child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk })
      child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
      child.on('error', reject)
      child.on('close', code => resolve({ code, stdout, stderr }))
      child.stdin.end(JSON.stringify(input))
    })
    assert.equal(result.code, 0)
    assert.deepEqual(JSON.parse(result.stdout), {})
    assert.ok(performance.now() - before < 1500, 'Stop must fit within its host timeout')
    assert.equal(flushes, 1)
    const observations = await Promise.all((await readdir(join(data, 'runtime')))
      .map(name => readFile(join(data, 'runtime', name), 'utf8').then(JSON.parse)))
    assert.equal(observations[0].stages.flush.state, 'unknown')
    assert.equal(observations[0].config.hook_budget_ms, 1000)
    assert.equal(observations[0].config.request_timeout_ms, 800)
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})
