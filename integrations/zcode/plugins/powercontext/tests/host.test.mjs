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
import { createServer } from 'node:http'
import { cp, mkdtemp, mkdir, readFile, readdir, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { promisify } from 'node:util'

const cli = process.env.ZCODE_CLI_BIN
const pluginRoot = dirname(dirname(fileURLToPath(import.meta.url)))

async function listen(server) {
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  return `http://127.0.0.1:${server.address().port}`
}

async function close(server) {
  await new Promise(resolve => server.close(resolve))
}

async function invoke(home, workspace, prompt, args = []) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [cli, '--prompt', prompt, '--output-format', 'json', '--cwd', workspace, ...args], {
      env: {
        ...process.env,
        USERPROFILE: home,
        ZCODE_STORAGE_DIR: join(home, '.zcode'),
        POWERCONTEXT_ZCODE_SERVER_URL: 'http://127.0.0.1:1',
        POWERCONTEXT_ZCODE_AUTHORIZATION: 'Bearer test-only',
      },
      stdio: ['ignore', 'pipe', 'pipe'],
    })
    let stdout = ''
    let stderr = ''
    child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk })
    child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk })
    child.on('error', reject)
    child.on('close', code => resolve({ code, stdout, stderr }))
  })
}

test('open-source ZCode CLI injects context, discovers MCP tools and survives Server loss', {
  skip: !cli ? 'Set ZCODE_CLI_BIN to a built open-source ZCode CLI bundle' : false,
  timeout: 60_000,
}, async t => {
  const requests = []
  const modelRequests = []
  let boundaryRun = false
  const powercontext = createServer(async (request, response) => {
    let raw = ''
    for await (const chunk of request) raw += chunk
    const body = raw ? JSON.parse(raw) : {}
    requests.push({ path: request.url, body, authorization: request.headers.authorization })
    response.setHeader('Content-Type', 'application/json')
    if (request.url === '/v1/scope-bindings/resolve') {
      response.end(JSON.stringify({ scope_id: 'scope-zcode-host-test' }))
    } else if (request.url === '/v1/context/prepare') {
      const content = body.query === 'Current project decisions, constraints, and outstanding work'
        ? 'Resumed project constraint: rime-9281.' : 'PowerContext validation fact: the project color is ultramarine.'
      response.end(JSON.stringify({
        schema: 'powercontext.prepared-context.v1', status: 'ready',
        content, content_bytes: Buffer.byteLength(content),
      }))
    } else if (request.url === '/v1/sources/content') {
      response.statusCode = 202
      response.end(JSON.stringify({ status: 'accepted', source: { name: 'content', source_id: body.source_id }, position: 1 }))
    } else if (request.url === '/v1/memory/flush') {
      response.end(JSON.stringify({ status: 'processed', previous_cursor: 0, current_cursor: 1,
        high_watermark: 1, processed_source_count: 1, memory: null }))
    } else if (request.url === '/mcp') {
      const result = body.method === 'initialize'
        ? { protocolVersion: '2025-03-26', capabilities: { tools: {} }, serverInfo: { name: 'powercontext-test', version: '0.1.0' } }
        : body.method === 'tools/list'
          ? { tools: [{ name: 'search_memory', description: 'Search memory', inputSchema: { type: 'object', properties: { query: { type: 'string' } } } }] }
          : {}
      response.end(JSON.stringify({ jsonrpc: '2.0', id: body.id ?? null, result }))
    } else {
      response.statusCode = 404
      response.end('{}')
    }
  })
  const model = createServer(async (request, response) => {
    let raw = ''
    for await (const chunk of request) raw += chunk
    modelRequests.push(JSON.parse(raw))
    if (boundaryRun && Date.now() % 5000 > 1000) {
      await new Promise(resolve => setTimeout(resolve, 5000 - Date.now() % 5000 + 30))
    }
    response.setHeader('Content-Type', 'text/event-stream')
    response.write(`data: ${JSON.stringify({
      id: 'chatcmpl-test', object: 'chat.completion.chunk', created: 1, model: 'fake-model',
      choices: [{ index: 0, delta: { role: 'assistant', content: 'Validation complete.' }, finish_reason: null }],
    })}\n\n`)
    response.write(`data: ${JSON.stringify({
      id: 'chatcmpl-test', object: 'chat.completion.chunk', created: 1, model: 'fake-model',
      choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
    })}\n\n`)
    response.end('data: [DONE]\n\n')
  })

  const temp = await mkdtemp(join(tmpdir(), 'powercontext-zcode-host-'))
  let powercontextOpen = true
  try {
    const serverUrl = await listen(powercontext)
    const modelUrl = await listen(model)
    const plugin = join(temp, 'plugin')
    const home = join(temp, 'home')
    const workspace = join(temp, 'workspace')
    await cp(pluginRoot, plugin, { recursive: true })
    await mkdir(join(home, '.zcode', 'cli'), { recursive: true })
    await mkdir(workspace)
    const mcp = JSON.parse(await readFile(join(plugin, '.mcp.json'), 'utf8'))
    mcp.mcpServers.powercontext.url = `${serverUrl}/mcp`
    mcp.mcpServers.powercontext.headers = { Authorization: '${POWERCONTEXT_ZCODE_AUTHORIZATION}' }
    await writeFile(join(plugin, '.mcp.json'), JSON.stringify(mcp))
    await writeFile(join(plugin, 'powercontext.json'), JSON.stringify({ server_url: serverUrl }))
    await writeFile(join(home, '.zcode', 'cli', 'config.json'), JSON.stringify({
      plugins: { enabled: true, dirs: [plugin] },
      provider: {
        local: {
          kind: 'openai-compatible',
          options: { apiKey: 'test-only', baseURL: `${modelUrl}/v1` },
          models: { 'fake-model': { id: 'fake-model', contextWindow: 32768, tool_call: true } },
        },
      },
      model: { main: 'local/fake-model' },
    }))

    const first = await invoke(home, workspace, 'What is the project color?')
    assert.equal(first.code, 0, first.stderr)
    assert.equal(JSON.parse(first.stdout).response, 'Validation complete.')
    assert.deepEqual(requests.filter(request => request.path.startsWith('/v1/')).map(request => request.path), [
      '/v1/scope-bindings/resolve', '/v1/scope-bindings/resolve', '/v1/context/prepare', '/v1/sources/content',
    ])
    assert.equal(requests.find(request => request.path === '/v1/context/prepare').body.scope_id, 'scope-zcode-host-test')
    assert.equal(requests.find(request => request.path === '/v1/sources/content').body.scope_id, 'scope-zcode-host-test')
    assert.ok(requests.some(request => request.path === '/mcp' && request.body.method === 'tools/list'))
    assert.ok(requests.filter(request => request.path.startsWith('/v1/') || request.path === '/mcp')
      .every(request => request.authorization === 'Bearer test-only'))
    assert.match(JSON.stringify(modelRequests[0].messages), /ultramarine/)
    assert.match(JSON.stringify(modelRequests[0].tools), /mcp__plugin_powercontext_powercontext__search_memory/)

    function strings(value) {
      return typeof value === 'string' ? [value] : value && typeof value === 'object'
        ? Object.values(value).flatMap(strings) : []
    }
    const delivered = strings(modelRequests[0].messages).find(value => value.includes('PowerContext current-request binding metadata:'))
    const binding = JSON.parse(delivered.split('\n').find(line => line.startsWith('{"schema":"powercontext.zcode.request-binding.v1"')))
    assert.ok(binding.plugin_data_dir)
    assert.ok(binding.session_id)
    const status = await promisify(execFile)(process.execPath, [binding.status_script, '--cwd', workspace,
      '--session-id', binding.session_id, '--data-dir', binding.plugin_data_dir], {
      env: { ...process.env, POWERCONTEXT_ZCODE_AUTHORIZATION: 'Bearer test-only' }, timeout: 5000,
    })
    const observed = JSON.parse(status.stdout)
    assert.equal(observed.status, 'observed')
    assert.equal(observed.incomplete, false)
    assert.equal(observed.observation.event, 'Stop')
    assert.equal(observed.observation.stages.flush.reason, 'boundary_flush_disabled')
    const observedFiles = await readdir(join(binding.plugin_data_dir, 'runtime'))
    const records = await Promise.all(observedFiles.map(name => readFile(join(binding.plugin_data_dir, 'runtime', name), 'utf8').then(JSON.parse)))
    const startup = records.filter(record => record.event === 'SessionStart')
    assert.equal(startup.length, 1)
    assert.equal(startup[0].event_source, 'startup')
    assert.equal(startup[0].stages.prepare.reason, 'prompt_recall_follows')
    assert.equal(startup[0].stages.capture.state, 'skipped')
    const prompt = records.find(record => record.event === 'UserPromptSubmit')
    assert.equal(prompt.stages.capture.state, 'accepted')
    assert.equal(prompt.stages.context_output.state, 'emitted')
    assert.equal(prompt.scope_id, 'scope-zcode-host-test')

    const resumed = await invoke(home, workspace, 'Continue the isolated project validation.', ['--resume', JSON.parse(first.stdout).sessionId])
    assert.equal(resumed.code, 0, resumed.stderr)
    assert.match(JSON.stringify(modelRequests.at(-1).messages), /rime-9281/)
    const resumedRecords = await Promise.all((await readdir(join(binding.plugin_data_dir, 'runtime')))
      .map(name => readFile(join(binding.plugin_data_dir, 'runtime', name), 'utf8').then(JSON.parse)))
    const resume = resumedRecords.filter(record => record.event === 'SessionStart' && record.event_source === 'resume')
    assert.equal(resume.length, 1)
    assert.equal(resume[0].stages.prepare.state, 'ready')
    assert.equal(resume[0].stages.prepare.query_source, 'lifecycle_generic')

    const compacted = await invoke(home, workspace, '/compact', ['--resume', JSON.parse(first.stdout).sessionId])
    assert.equal(compacted.code, 0, compacted.stderr)
    const compactRecords = await Promise.all((await readdir(join(binding.plugin_data_dir, 'runtime')))
      .map(name => readFile(join(binding.plugin_data_dir, 'runtime', name), 'utf8').then(JSON.parse)))
    t.diagnostic(compactRecords.some(record => record.event_source === 'compact')
      ? 'CLI emitted SessionStart compact' : 'CLI did not emit SessionStart compact; resume/prompt recovery remains available')

    await writeFile(join(plugin, 'powercontext.json'), JSON.stringify({ server_url: serverUrl, boundary_flush: true }))
    boundaryRun = true
    const boundary = await invoke(home, workspace, 'Validate one explicit boundary processing turn.')
    boundaryRun = false
    assert.equal(boundary.code, 0, boundary.stderr)
    assert.equal(requests.filter(request => request.path === '/v1/memory/flush').length, 1)
    const boundaryRecords = await Promise.all((await readdir(join(binding.plugin_data_dir, 'runtime')))
      .map(name => readFile(join(binding.plugin_data_dir, 'runtime', name), 'utf8').then(JSON.parse)))
    const stopped = boundaryRecords.find(record => record.event === 'Stop' && record.config.boundary_flush)
    assert.equal(stopped.stages.flush.state, 'cursor_reached')
    assert.equal(stopped.stages.flush.source_position, 1)
    assert.equal(stopped.config.hook_budget_ms, 1000)

    await close(powercontext)
    powercontextOpen = false
    const second = await invoke(home, workspace, 'Continue without PowerContext.')
    assert.equal(second.code, 0, second.stderr)
    assert.equal(JSON.parse(second.stdout).response, 'Validation complete.')
    assert.doesNotMatch(JSON.stringify(modelRequests.at(-1).messages), /ultramarine/)
  } finally {
    if (powercontextOpen) await close(powercontext)
    await close(model)
    await rm(temp, { recursive: true, force: true })
  }
})
