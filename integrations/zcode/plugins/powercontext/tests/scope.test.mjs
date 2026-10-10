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
import { cp, mkdtemp, mkdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const plugin = fileURLToPath(new URL('..', import.meta.url))

function run(script, args, env, input) {
  return new Promise((resolve, reject) => {
    const environment = { ...process.env }
    for (const key of Object.keys(environment)) {
      if (key.startsWith('POWERCONTEXT_ZCODE_') || key === 'ZCODE_SESSION_ID') delete environment[key]
    }
    const child = spawn(process.execPath, [script, ...args], { env: { ...environment, ...env }, stdio: ['pipe', 'pipe', 'pipe'] })
    let stdout = '', stderr = ''
    child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk })
    child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('close', code => resolve({ code, stdout, stderr }))
    child.stdin.end(input ? JSON.stringify(input) : '')
  })
}

test('installed Scope resolver agrees with Hook and keeps binding writes distinct from effective Scope', async () => {
  const root = await mkdtemp(join(tmpdir(), 'pc-zcode-scope-'))
  const bindings = new Map()
  const seen = []
  let failResolve = false
  const keyOf = key => `${key.kind}:${key.external_id}`
  const server = createServer(async (req, res) => {
    let raw = ''
    for await (const chunk of req) raw += chunk
    const body = JSON.parse(raw)
    seen.push({ method: req.method, path: req.url, body })
    res.setHeader('Content-Type', 'application/json')
    if (req.url === '/v1/scope-bindings' && req.method === 'PUT') {
      bindings.set(keyOf(body.key), body.scope_id)
      // JSON property order is not part of the binding contract.
      res.end(JSON.stringify({ scope_id: body.scope_id, key: { external_id: body.key.external_id, kind: body.key.kind, integration: 'zcode' } }))
    } else if (req.url === '/v1/scope-bindings/clear' && req.method === 'POST') {
      res.end(JSON.stringify({ cleared: bindings.delete(keyOf(body.key)) }))
    } else if (req.url === '/v1/scope-bindings/resolve') {
      if (failResolve) { res.statusCode = 503; res.end('{}'); return }
      const scopeId = body.explicit_scope_id ?? body.binding_keys.map(key => bindings.get(keyOf(key))).find(Boolean) ?? 'default'
      res.end(JSON.stringify({ scope_id: scopeId }))
    } else if (req.url === '/v1/context/prepare') {
      res.end(JSON.stringify({ schema: 'powercontext.prepared-context.v1', status: 'empty', content: null, content_bytes: 0 }))
    } else { res.statusCode = 404; res.end('{}') }
  })
  try {
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    const installed = join(root, 'plugin')
    await cp(plugin, installed, { recursive: true, filter: path => !path.includes('node_modules') })
    await writeFile(join(installed, 'powercontext.json'), JSON.stringify({ server_url: `http://127.0.0.1:${server.address().port}`, capture_prompts: false }))
    const cwd = join(root, '项目 with spaces')
    await mkdir(cwd)
    const script = join(installed, 'scripts', 'scope.mjs')
    const args = ['--cwd', cwd, '--session-id', 'session-one']
    const scope = async (action, env = {}, extra = []) => {
      const result = await run(script, [action, ...args, ...extra], env)
      assert.equal(result.stderr, '')
      return { ...result, body: JSON.parse(result.stdout) }
    }
    bindings.set('session:session-one', 'session-scope')
    const bound = await scope('bind', {}, ['--scope-id', 'workspace-scope'])
    assert.equal(bound.code, 0)
    assert.equal(bound.body.binding.status, 'saved')
    assert.equal(bound.body.scope_id, 'session-scope')
    assert.equal(bound.body.bound_scope_is_current, false)
    const resolvedRequest = seen.at(-1).body
    const hooked = await run(join(installed, 'hooks', 'user_prompt_submit.mjs'), [], { ZCODE_PLUGIN_DATA: join(root, 'data') }, {
      hookEventName: 'UserPromptSubmit', cwd, sessionId: 'session-one', prompt: 'Look up the project.' })
    const context = JSON.parse(hooked.stdout).hookSpecificOutput.additionalContext
    const metadata = JSON.parse(context.split('\n')[1])
    assert.equal(metadata.scope_id, bound.body.scope_id)
    assert.equal(metadata.session_id, 'session-one')
    assert.equal(metadata.scope_script, script)
    assert.deepEqual(seen.at(-2).body, resolvedRequest)
    assert.equal((await scope('unbind')).body.scope_id, 'session-scope')
    assert.equal((await scope('unbind')).body.binding.status, 'absent')
    const explicit = await scope('bind', { POWERCONTEXT_ZCODE_SCOPE_ID: 'explicit-scope' }, ['--scope-id', 'workspace-scope'])
    assert.equal(explicit.body.resolution_note, 'explicit_scope_override')
    const opaqueScope = ' 项目.alpha / 🚀 '
    const opaque = await scope('resolve', { POWERCONTEXT_ZCODE_SCOPE_ID: opaqueScope })
    assert.equal(opaque.code, 0)
    assert.equal(opaque.body.scope_id, opaqueScope)
    assert.equal(seen.at(-1).body.explicit_scope_id, opaqueScope)
    const opaqueBinding = await scope('bind', {}, ['--scope-id', opaqueScope])
    assert.equal(opaqueBinding.code, 0)
    assert.equal(opaqueBinding.body.binding.scope_id, opaqueScope)
    const before = seen.length
    const conflict = await scope('resolve', { ZCODE_SESSION_ID: 'different' })
    assert.equal(conflict.code, 1)
    assert.equal(conflict.body.code, 'session_conflict')
    assert.equal(seen.length, before)
    const remote = await scope('bind', { POWERCONTEXT_ZCODE_REMOTE_WORKSPACE: 'true' }, ['--scope-id', 'workspace-scope'])
    assert.equal(remote.body.code, 'remote_binding_disabled')
    assert.equal(seen.length, before)
    failResolve = true
    const partial = await scope('bind', {}, ['--scope-id', 'workspace-scope'])
    assert.equal(partial.code, 1)
    assert.equal(partial.body.binding.status, 'saved')
    assert.equal(partial.body.code, 'server_unavailable')
  } finally {
    await new Promise(resolve => server.close(resolve))
    await rm(root, { recursive: true, force: true })
  }
})
