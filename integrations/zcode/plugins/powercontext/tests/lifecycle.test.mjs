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
import { cp, mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { queryObservations } from '../shared/observations.mjs'

const plugin = fileURLToPath(new URL('..', import.meta.url))

test('SessionStart restores only compact/resume and records readonly empty/failure results', async () => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-start-'))
  const calls = []
  let prepared = 'ready'
  const server = createServer(async (req, res) => {
    let text = ''
    for await (const chunk of req) text += chunk
    const body = JSON.parse(text)
    calls.push({ path: req.url, body })
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/v1/scope-bindings/resolve') res.end('{"scope_id":"scope-start"}')
    else if (req.url === '/v1/context/prepare') {
      if (prepared === 'failed') { res.statusCode = 503; res.end('{}'); return }
      const content = 'Historical project decision: validation uses frost-917.'
      res.end(JSON.stringify({ schema: 'powercontext.prepared-context.v1', status: prepared,
        content: prepared === 'ready' ? content : null, content_bytes: prepared === 'ready' ? Buffer.byteLength(content) : 0 }))
    } else { res.statusCode = 404; res.end('{}') }
  })
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const installed = join(root, 'plugin'), cwd = join(root, 'workspace'), data = join(root, 'data')
    await cp(plugin, installed, { recursive: true, filter: path => !path.includes('node_modules') })
    await mkdir(cwd)
    const settings = { serverUrl: `http://127.0.0.1:${server.address().port}`, capturePrompts: true }
    await writeFile(join(installed, 'powercontext.json'), JSON.stringify({ server_url: settings.serverUrl }))
    async function invoke(source) {
      const input = { hookEventName: 'SessionStart', source, sessionId: `session-${source}`, cwd }
      const env = { ...process.env, ZCODE_PLUGIN_DATA: data }
      for (const key of Object.keys(env)) if (key.startsWith('POWERCONTEXT_ZCODE_') || key === 'ZCODE_SESSION_ID') delete env[key]
      const result = await new Promise((resolve, reject) => {
        const child = spawn(process.execPath, [join(installed, 'hooks/session_start.mjs')], { env, stdio: ['pipe', 'pipe', 'pipe'] })
        let stdout = '', stderr = ''
        child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk })
        child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
        child.on('error', reject)
        child.on('close', code => resolve({ code, stdout, stderr }))
        child.stdin.end(JSON.stringify(input))
      })
      assert.equal(result.code, 0)
      const status = await queryObservations(input, settings, data)
      assert.equal(status.observation.event_source, source)
      assert.equal(status.incomplete, false)
      assert.equal(status.observation.stages.capture.state, 'skipped')
      assert.equal(status.observation.stages.flush.state, 'skipped')
      assert.doesNotMatch(JSON.stringify(status), /frost-917|session-startup|session-resume/)
      return { ...result, output: JSON.parse(result.stdout), observation: status.observation }
    }
    for (const source of ['startup', 'clear']) {
      calls.length = 0
      const result = await invoke(source)
      assert.deepEqual(calls.map(call => call.path), ['/v1/scope-bindings/resolve'])
      assert.equal(result.output.hookSpecificOutput.additionalContext, '')
      assert.equal(result.observation.stages.prepare.reason, 'prompt_recall_follows')
    }
    for (const source of ['compact', 'resume']) {
      calls.length = 0
      const result = await invoke(source)
      assert.deepEqual(calls.map(call => call.path), ['/v1/scope-bindings/resolve', '/v1/context/prepare'])
      assert.equal(calls[1].body.query, 'Current project decisions, constraints, and outstanding work')
      assert.match(result.output.hookSpecificOutput.additionalContext, /untrusted historical evidence/)
      assert.match(result.output.hookSpecificOutput.additionalContext, /frost-917/)
      assert.equal(result.observation.stages.prepare.query_source, 'lifecycle_generic')
    }
    prepared = 'empty'
    assert.equal((await invoke('resume')).output.hookSpecificOutput.additionalContext, '')
    prepared = 'failed'
    const failed = await invoke('compact')
    assert.equal(failed.output.hookSpecificOutput.additionalContext, '')
    assert.equal(failed.observation.stages.prepare.code, 'server_unavailable')
    assert.equal(failed.observation.stages.context_output.state, 'empty')
    assert.ok(calls.every(call => !/sources|flush|handoff/.test(call.path)))
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})
