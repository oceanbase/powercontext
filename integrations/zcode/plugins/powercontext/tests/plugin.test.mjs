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
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { tmpdir } from 'node:os'
import { after, before, beforeEach, test } from 'node:test'

const pluginRoot = dirname(dirname(fileURLToPath(import.meta.url)))
const hookPath = join(pluginRoot, 'hooks', 'user_prompt_submit.mjs')
let server
let serverUrl
const requests = []
let override = {}
let dataDir

beforeEach(() => {
  requests.length = 0
  override = {}
})

before(async () => {
  dataDir = await mkdtemp(join(tmpdir(), 'pc-zcode-observations-'))
  server = createServer(async (request, response) => {
    const chunks = []
    for await (const chunk of request) chunks.push(chunk)
    const body = JSON.parse(Buffer.concat(chunks).toString('utf8'))
    requests.push({ path: request.url, body })
    response.setHeader('Content-Type', 'application/json')
    const replacement = override[request.url]
    if (replacement) {
      if (replacement.delayMs) await new Promise(resolve => setTimeout(resolve, replacement.delayMs))
      response.statusCode = replacement.status
      response.end(JSON.stringify(replacement.body))
      return
    }
    if (request.url === '/v1/scope-bindings/resolve') {
      response.end(JSON.stringify({ scope_id: 'scope-test' }))
    } else if (request.url === '/v1/context/prepare') {
      const content = 'Remember the blue deployment.'
      response.end(JSON.stringify({
        schema: 'powercontext.prepared-context.v1', status: 'ready',
        content, content_bytes: Buffer.byteLength(content),
      }))
    } else if (request.url === '/v1/sources/content') {
      response.statusCode = 202
      response.end(JSON.stringify({
        status: 'accepted', source: { name: 'content', source_id: body.source_id }, position: 1,
      }))
    } else {
      response.statusCode = 404
      response.end('{}')
    }
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  serverUrl = `http://127.0.0.1:${server.address().port}`
})

after(async () => {
  await rm(dataDir, { recursive: true, force: true })
  await new Promise(resolve => server.close(resolve))
})

function invoke(payload, environment = {}, script = hookPath) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [script], {
      env: { ...process.env, POWERCONTEXT_ZCODE_SERVER_URL: serverUrl, ZCODE_PLUGIN_DATA: dataDir, ...environment },
      stdio: ['pipe', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk })
    child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('close', code => resolve({ code, stdout, stderr }))
    child.stdin.end(JSON.stringify(payload))
  })
}

test('plugin declares a ZCode prompt hook and Server MCP endpoint', async () => {
  const manifest = JSON.parse(await readFile(join(pluginRoot, '.zcode-plugin', 'plugin.json'), 'utf8'))
  const hooks = JSON.parse(await readFile(join(pluginRoot, 'hooks', 'hooks.json'), 'utf8'))
  const mcp = JSON.parse(await readFile(join(pluginRoot, '.mcp.json'), 'utf8'))
  assert.equal(manifest.name, 'powercontext')
  assert.equal(hooks.hooks.UserPromptSubmit[0].hooks[0].type, 'process')
  assert.equal(mcp.mcpServers.powercontext.url, 'http://127.0.0.1:8000/mcp')
})

test('hook resolves Scope, injects prepared context and captures one Source', async () => {
  const payload = {
    hookEventName: 'UserPromptSubmit', sessionId: 'session-1', turnId: 'turn-2',
    cwd: pluginRoot, prompt: 'What color is the deployment?',
  }
  const result = await invoke(payload)
  assert.equal(result.code, 0)
  assert.equal(result.stderr, '')
  const output = JSON.parse(result.stdout)
  assert.equal(output.hookSpecificOutput.hookEventName, 'UserPromptSubmit')
  assert.match(output.hookSpecificOutput.additionalContext, /Remember the blue deployment/)
  assert.deepEqual(requests.map(request => request.path), [
    '/v1/scope-bindings/resolve', '/v1/context/prepare', '/v1/sources/content',
  ])
  assert.deepEqual(requests[0].body.binding_keys.map(key => key.kind), ['session', 'workspace'])
  assert.equal(requests[1].body.scope_id, 'scope-test')
  assert.equal(requests[2].body.scope_id, 'scope-test')
  assert.match(requests[2].body.source_id, /^zcode-user-prompt:[0-9a-f]{64}$/)
  const repeated = await invoke(payload)
  assert.equal(JSON.parse(repeated.stdout).hookSpecificOutput.hookEventName, 'UserPromptSubmit')
  assert.equal(requests.at(-1).body.source_id, requests[2].body.source_id)
  await invoke({ ...payload, turnId: 'turn-3' })
  assert.notEqual(requests.at(-1).body.source_id, requests[2].body.source_id)
  await invoke({ ...payload, turnId: undefined })
  const fallbackId = requests.at(-1).body.source_id
  await invoke({ ...payload, turnId: undefined })
  assert.equal(requests.at(-1).body.source_id, fallbackId)
})

test('capture can be disabled without skipping recall', async () => {
  const result = await invoke({
    hook_event_name: 'UserPromptSubmit', session_id: 'session-2', turnId: 'turn-3',
    cwd: pluginRoot, prompt: 'Recall the deployment.',
  }, { POWERCONTEXT_ZCODE_CAPTURE_PROMPTS: 'false' })
  assert.equal(result.code, 0)
  assert.match(JSON.parse(result.stdout).hookSpecificOutput.additionalContext, /blue deployment/)
  assert.deepEqual(requests.map(request => request.path), [
    '/v1/scope-bindings/resolve', '/v1/context/prepare',
  ])
})

test('unavailable Server yields a valid empty result and does not block the turn', async () => {
  const result = await invoke({
    hookEventName: 'UserPromptSubmit', sessionId: 'session-3', turnId: 'turn-4',
    cwd: pluginRoot, prompt: 'Continue working.',
  }, { POWERCONTEXT_ZCODE_SERVER_URL: 'http://127.0.0.1:1' })
  assert.equal(result.code, 0)
  assert.equal(JSON.parse(result.stdout).hookSpecificOutput.additionalContext, '')
  assert.match(result.stderr, /"stage":"scope"/)
  assert.doesNotMatch(result.stderr, /Continue working|scope-test/)
})

test('empty recall is silent, while authentication and Scope failures have distinct diagnostics', async () => {
  const payload = { hookEventName: 'UserPromptSubmit', sessionId: 'session-4', turnId: 'turn-5',
    cwd: pluginRoot, prompt: 'Recall a fact.' }
  override['/v1/context/prepare'] = {
    status: 200, body: { schema: 'powercontext.prepared-context.v1', status: 'empty',
      content: null, content_bytes: 0 },
  }
  const empty = await invoke(payload)
  assert.match(JSON.parse(empty.stdout).hookSpecificOutput.additionalContext, /current-request binding/)
  assert.doesNotMatch(JSON.parse(empty.stdout).hookSpecificOutput.additionalContext, /blue deployment/)
  assert.equal(empty.stderr, '')
  assert.ok(requests.some(request => request.path === '/v1/sources/content'))

  requests.length = 0
  override = { '/v1/scope-bindings/resolve': { status: 401, body: {} } }
  const unauthorized = await invoke(payload)
  assert.equal(JSON.parse(unauthorized.stdout).hookSpecificOutput.additionalContext, '')
  assert.match(unauthorized.stderr, /"stage":"scope","code":"unauthorized"/)
  assert.deepEqual(requests.map(request => request.path), ['/v1/scope-bindings/resolve'])

  override = { '/v1/scope-bindings/resolve': { status: 200, body: { scope_id: null } } }
  const unresolved = await invoke(payload)
  assert.match(unresolved.stderr, /"stage":"scope","code":"scope_unresolved"/)
})

test('prepare and capture failures remain independent and do not expose their bodies', async () => {
  const payload = { hookEventName: 'UserPromptSubmit', sessionId: 'session-5', turnId: 'turn-6',
    cwd: pluginRoot, prompt: 'Inspect the deployment.' }
  override['/v1/context/prepare'] = {
    status: 200, body: { schema: 'wrong', status: 'ready', content: 'private server body', content_bytes: 19 },
  }
  const prepareFailure = await invoke(payload)
  assert.match(JSON.parse(prepareFailure.stdout).hookSpecificOutput.additionalContext, /current-request binding/)
  assert.match(prepareFailure.stderr, /"stage":"prepare","code":"invalid_response"/)
  assert.doesNotMatch(prepareFailure.stderr, /private server body|Inspect the deployment/)
  assert.ok(requests.some(request => request.path === '/v1/sources/content'))

  requests.length = 0
  override = { '/v1/sources/content': { status: 503, body: { detail: 'private server body' } } }
  const captureFailure = await invoke(payload)
  assert.match(JSON.parse(captureFailure.stdout).hookSpecificOutput.additionalContext, /blue deployment/)
  assert.match(captureFailure.stderr, /"stage":"capture","code":"server_unavailable"/)
  assert.doesNotMatch(captureFailure.stderr, /private server body|Inspect the deployment/)
})

test('sensitive prompts are recalled but not automatically captured', async () => {
  for (const prompt of [
    'Check API_KEY=example-not-real-secret.',
    '{"password":"synthetic-password-for-review"}',
    '{"access_token":"synthetic-token-for-review"}',
  ]) {
    requests.length = 0
    const result = await invoke({ hookEventName: 'UserPromptSubmit', sessionId: 'session-6', turnId: 'turn-7',
      cwd: pluginRoot, prompt })
    assert.equal(result.code, 0)
    assert.match(JSON.parse(result.stdout).hookSpecificOutput.additionalContext, /blue deployment/)
    assert.deepEqual(requests.map(request => request.path), [
      '/v1/scope-bindings/resolve', '/v1/context/prepare',
    ])
  }
})

test('large multibyte prompts are captured without changing their content', async () => {
  const prompt = `Deployment note: ${'中'.repeat(100_000)}`
  const payload = { hookEventName: 'UserPromptSubmit', sessionId: 'session-utf8', turnId: 'turn-utf8',
    cwd: pluginRoot, prompt }
  const first = await invoke(payload)
  assert.equal(first.code, 0)
  const captured = requests.find(request => request.path === '/v1/sources/content')?.body
  assert.equal(captured?.content, prompt)
  const firstId = captured.source_id

  requests.length = 0
  const repeated = await invoke(payload)
  assert.equal(repeated.code, 0)
  assert.equal(requests.find(request => request.path === '/v1/sources/content')?.body.source_id, firstId)
})

test('slow prepare times out without losing independent Source capture', async () => {
  override['/v1/context/prepare'] = { status: 200, body: {}, delayMs: 1400 }
  const result = await invoke({ hookEventName: 'UserPromptSubmit', sessionId: 'session-7', turnId: 'turn-8',
    cwd: pluginRoot, prompt: 'Keep working after slow recall.' })
  assert.equal(result.code, 0)
  assert.match(JSON.parse(result.stdout).hookSpecificOutput.additionalContext, /current-request binding/)
  assert.match(result.stderr, /"stage":"prepare","code":"timeout"/)
  assert.ok(requests.some(request => request.path === '/v1/sources/content'))
})

test('installed Server URL keeps Hook aligned with MCP despite a stale environment override', async () => {
  const plugin = await mkdtemp(join(tmpdir(), 'powercontext-zcode-hook-'))
  try {
    await mkdir(join(plugin, 'hooks'))
    const installedHook = join(plugin, 'hooks', 'user_prompt_submit.mjs')
    await cp(join(pluginRoot, 'hooks'), join(plugin, 'hooks'), { recursive: true })
    await cp(join(pluginRoot, 'shared'), join(plugin, 'shared'), { recursive: true })
    await writeFile(join(plugin, 'powercontext.json'), JSON.stringify({ server_url: serverUrl, capture_prompts: false }))
    const result = await invoke({ hookEventName: 'UserPromptSubmit', sessionId: 'session-8', turnId: 'turn-9',
      cwd: pluginRoot, prompt: 'Recall the deployment.' },
    { POWERCONTEXT_ZCODE_SERVER_URL: 'http://127.0.0.1:1' }, installedHook)
    assert.equal(result.stderr, '')
    assert.match(JSON.parse(result.stdout).hookSpecificOutput.additionalContext, /blue deployment/)
    assert.deepEqual(requests.map(request => request.path), [
      '/v1/scope-bindings/resolve', '/v1/context/prepare',
    ])
  } finally {
    await rm(plugin, { recursive: true, force: true })
  }
})

test('remote workspace requires an explicit Scope and never derives a path binding', async () => {
  const payload = { hookEventName: 'UserPromptSubmit', sessionId: 'remote-session', turnId: 'turn-1',
    cwd: '/workspace/project', prompt: 'Recall remote context.' }
  const missing = await invoke(payload, { POWERCONTEXT_ZCODE_REMOTE_WORKSPACE: 'true' })
  assert.match(missing.stderr, /"stage":"scope","code":"scope_unresolved"/)
  assert.deepEqual(requests, [])

  const explicit = await invoke(payload, {
    POWERCONTEXT_ZCODE_REMOTE_WORKSPACE: 'true', POWERCONTEXT_ZCODE_SCOPE_ID: 'scope-test',
  })
  assert.equal(explicit.stderr, '')
  assert.deepEqual(requests[0].body.binding_keys, [])
  assert.equal(requests[0].body.explicit_scope_id, 'scope-test')
})
