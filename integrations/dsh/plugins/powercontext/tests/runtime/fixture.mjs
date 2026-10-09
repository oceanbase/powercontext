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

import { createServer } from 'node:http'
import { cpSync, existsSync, mkdirSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { pathToFileURL } from 'node:url'
import { createRequire } from 'node:module'
import { startPowerContextServer } from '../../scripts/e2e-server.mjs'

const sdkModule = process.env.DSH_TEST_SDK_ROOT
  ? pathToFileURL(join(process.env.DSH_TEST_SDK_ROOT, 'lib/index.js')).href
  : '@deepseek-ai/dsh-sdk-client'
const { DeepSeekHarness } = await import(sdkModule)
const sdkRequire = createRequire(process.env.DSH_TEST_SDK_ROOT
  ? join(process.env.DSH_TEST_SDK_ROOT, 'package.json')
  : import.meta.resolve('@deepseek-ai/dsh-sdk-client'))
export const dshBin = join(dirname(sdkRequire.resolve('@deepseek-ai/dsh/package.json')), 'lib/bin.js')
export const CANARY = 'The aurora deployment color is violet-cedar-1457.'
export const NATIVE_ERROR_CANARY = 'private-response-marker: api_key=FAKE_NATIVE_ERROR_CANARY'
export const pluginRoot = resolve(import.meta.dirname, '../..')

async function listen(handler) {
  const server = createServer((req, res) => {
    Promise.resolve(handler(req, res)).catch(error => {
      if (res.headersSent || res.writableEnded) {
        res.destroy()
        return
      }
      res.writeHead(500)
      res.end('test fixture failed')
    })
  })
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  return {
    server, url: `http://127.0.0.1:${server.address().port}`,
    async close() {
      server.closeAllConnections()
      await new Promise((resolve, reject) => server.close(error => error ? reject(error) : resolve()))
    },
  }
}

async function bodyOf(req) {
  const buffer = await rawBodyOf(req)
  const text = buffer.toString()
  return text ? JSON.parse(text) : undefined
}

async function rawBodyOf(req) {
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  return Buffer.concat(chunks)
}

function json(res, value, status = 200) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(value))
}

function secretArguments(operation, scopeId) {
  const text = 'api_key=FAKE_REVIEW_MARKER'
  const source = { name: 'content', source_id: 'secret-fixture' }
  const draft = {
    objective: 'Continue the review', disposition: 'blocked', next_action: null, omissions: [],
    state: [{ text, citations: [{ kind: 'source', source_ref: source }] }],
  }
  switch (operation) {
    case 'handoff_current_work': return { scope_id: scopeId, source_id: source.source_id, handoff: {
      schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input', ...draft,
      state: [{ text, basis: 'declared', evidence: [] }],
    } }
    case 'activate_handoff': return { scope_id: scopeId, boundary_source: source, objective: text }
    case 'finalize_handoff': return { scope_id: scopeId, draft }
    case 'commit_handoff': return { scope_id: scopeId, handoff: {
      schema: 'powercontext.prepared-handoff.v1', scope_id: scopeId, base: null,
      content: { schema: 'powercontext.handoff.v1', ...draft },
    } }
    case 'capture_content_source': return { scope_id: scopeId, source_id: source.source_id, content: text }
    case 'revise_memory_entry': return { scope_id: scopeId, memory_id: 'secret-fixture', text, expected_version: 1 }
    default: return { scope_id: scopeId, text, kind: 'decision' }
  }
}

export async function environment({ realModel, fullCatalog = false } = {}) {
  const home = mkdtempSync(join(tmpdir(), 'pc-dsh-runtime-'))
  const modelRequests = []
  let scopeIdForModel = ''
  const model = await listen(async (req, res) => {
    const body = await bodyOf(req)
    modelRequests.push(body)
    if (realModel) {
      const upstream = await fetch(realModel.baseUrl.replace(/\/$/, '') + '/chat/completions', {
        method: 'POST', signal: AbortSignal.timeout(90000),
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${realModel.apiKey}` },
        body: JSON.stringify({ ...body, model: realModel.model, thinking: { type: 'disabled' } }),
      })
      res.writeHead(upstream.status, { 'Content-Type': upstream.headers.get('content-type') ?? 'application/json' })
      for await (const chunk of upstream.body) res.write(chunk)
      res.end()
      return
    }
    if (!body.stream) {
      const prompt = body.messages.findLast(m => m.role === 'user').content
      const text = typeof prompt === 'string' ? prompt : prompt.map(block => block.text ?? '').join('')
      let content = 'OK' // The real Server also probes generation readiness with a plain-text prompt.
      if (text.startsWith('{')) {
        const input = JSON.parse(text)
        const candidates = input.current_entries?.length || !JSON.stringify(input.evidence).includes(CANARY) ? [] : [{
          intent: 'add', kind: 'decision', text: CANARY, evidence_ids: ['source:0'], reason: 'runtime fixture',
        }]
        content = JSON.stringify({ candidates })
      }
      json(res, {
        id: 'inference-fixture', object: 'chat.completion', model: body.model, created: 0,
        choices: [{ index: 0, message: { role: 'assistant', content }, finish_reason: 'stop' }],
        usage: { prompt_tokens: 10, completion_tokens: 10, total_tokens: 20 },
      })
      return
    }
    const skillMatch = JSON.stringify(body.messages).match(/LOAD_PC_SKILL:(powercontext-(?:memory|handoff|review))/)
    const getScope = JSON.stringify(body.messages).includes('RUN_PC_GET_SCOPE')
    const secretWrite = JSON.stringify(body.messages).match(/RUN_PC_SECRET:(remember_memory|revise_memory_entry|capture_content_source|handoff_current_work|activate_handoff|finalize_handoff|commit_handoff)/)?.[1]
    const needsTool = (skillMatch || getScope || secretWrite || JSON.stringify(body.messages).includes('RUN_PC_SEARCH'))
      && !body.messages.some(message => message.role === 'tool')
    const content = JSON.stringify(body.messages).includes(CANARY) ? CANARY : 'Task completed.'
    const requestedScopeId = scopeIdForModel
    res.writeHead(200, { 'Content-Type': 'text/event-stream' })
    const chunk = (delta, finish_reason = null) => `data: ${JSON.stringify({
      id: 'dsh-fixture', object: 'chat.completion.chunk', model: body.model, created: 0,
      choices: [{ index: 0, delta, finish_reason }],
    })}\n\n`
    if (needsTool) {
      res.end(chunk({ role: 'assistant', tool_calls: [{
        index: 0, id: 'fixture-search', type: 'function',
        function: skillMatch
          ? { name: 'skill', arguments: JSON.stringify({ name: skillMatch[1] }) }
          : secretWrite
          ? { name: `mcp__powercontext__${secretWrite}`, arguments: JSON.stringify(secretArguments(secretWrite, requestedScopeId)) }
          : getScope
          ? { name: 'mcp__powercontext__get_scope', arguments: JSON.stringify({ scope_id: requestedScopeId }) }
          : { name: 'mcp__powercontext__search_memory', arguments: JSON.stringify({
            scope_id: requestedScopeId, query: 'aurora deployment color',
          }) },
      }] }) + chunk({}, 'tool_calls') + 'data: [DONE]\n\n')
    } else {
      res.end(chunk({ role: 'assistant', content }) + chunk({}, 'stop') + 'data: [DONE]\n\n')
    }
  })
  let server
  try {
    server = await startPowerContextServer({ env: {
    OPENAI_API_KEY: 'runtime-fixture',
    ...(fullCatalog ? { POWERCONTEXT_SERVER_HANDOFF_REPORT_ENABLED: 'true' } : {}),
    POWERCONTEXT_SERVER_INFERENCE: JSON.stringify({
      generation_model: `openai-chat:${realModel?.model ?? 'fixture'}`, generation_base_url: model.url + '/v1',
    }),
    } })
  } catch (error) {
    await model.close()
    throw error
  }
  const calls = []
  const mcpCalls = []
  let fault
  const stalledInitializations = new Set()
  const proxy = await listen(async (req, res) => {
    const path = new URL(req.url, 'http://localhost').pathname
    if (path === '/mcp' || path.startsWith('/mcp/')) {
      const rawBody = await rawBodyOf(req)
      const requestHeaders = new Headers(req.headers)
      for (const header of ['connection', 'content-length', 'host', 'transfer-encoding']) requestHeaders.delete(header)
      const mcpCall = { path, method: req.method, body: rawBody.length ? JSON.parse(rawBody.toString()) : undefined }
      mcpCalls.push(mcpCall)
      if (fault?.path === '/mcp' && fault.hold && mcpCall.body?.method === 'initialize') {
        await new Promise((resolve) => {
          const release = () => {
            stalledInitializations.delete(release)
            res.off('close', closed)
            resolve()
          }
          const closed = () => { mcpCall.closed = true; release() }
          stalledInitializations.add(release)
          res.once('close', closed)
        })
        if (res.destroyed) return
      }
      if (fault?.path === '/mcp' && fault.redirectTo && mcpCall.body?.method === 'tools/call') {
        res.writeHead(307, { Location: fault.redirectTo }).end()
        return
      }
      if (fault?.path === '/mcp' && fault.status && mcpCall.body?.method === 'tools/call') {
        mcpCall.status = fault.status
        res.writeHead(fault.status, { 'Content-Type': 'text/plain', 'X-Private-Diagnostic': NATIVE_ERROR_CANARY })
        res.end(NATIVE_ERROR_CANARY)
        return
      }
      const upstream = await fetch(server.baseUrl + req.url, {
        method: req.method,
        headers: requestHeaders,
        // Copy the incoming buffer before handing it to Node's fetch. Node 22's
        // undici detaches the buffer-backed ArrayBuffer while extracting the
        // request body, which otherwise prevents the MCP tools/list request
        // from completing through this proxy.
        ...(rawBody.length ? { body: rawBody.toString() } : {}),
      })
      const responseHeaders = Object.fromEntries(upstream.headers)
      for (const header of ['connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailer', 'transfer-encoding', 'upgrade']) {
        delete responseHeaders[header]
      }
      res.writeHead(upstream.status, responseHeaders)
      const catalogChunks = []
      if (upstream.body) for await (const chunk of upstream.body) {
        if (mcpCall.body?.method === 'tools/list') catalogChunks.push(Buffer.from(chunk))
        res.write(chunk)
      }
      if (catalogChunks.length) {
        const text = Buffer.concat(catalogChunks).toString()
        const messages = upstream.headers.get('content-type')?.includes('text/event-stream')
          ? text.split('\n').filter(line => line.startsWith('data: ')).map(line => JSON.parse(line.slice(6)))
          : [JSON.parse(text)]
        mcpCall.result = messages.find(message => message.result?.tools)?.result
      }
      res.end()
      return
    }
    const body = await bodyOf(req)
    const call = { path, body }
    calls.push(call)
    if (fault?.path === path || fault?.path === '*') {
      if (fault.hold) {
        await new Promise(resolve => res.once('close', () => { call.closed = true; resolve() }))
        return
      }
      if (fault.holdBody) {
        call.status = fault.status
        res.writeHead(fault.status, { 'Content-Type': 'application/json', 'X-PowerContext-Request-ID': 'req-runtime-body' })
        res.flushHeaders()
        res.write('{"error":{"message":"private-response-marker')
        await new Promise(resolve => res.once('close', () => { call.closed = true; resolve() }))
        return
      }
      json(res, { error: { code: fault.code, message: 'private-response-marker' } }, fault.status)
      call.status = fault.status
      return
    }
    const result = await fetch(server.baseUrl + req.url, {
      method: req.method,
      headers: { 'Content-Type': 'application/json' },
      ...(body ? { body: JSON.stringify(body) } : {}),
    })
    const value = await result.json()
    call.status = result.status
    call.result = value
    json(res, value, result.status)
  })
  const api = async (path, body) => {
    const result = await fetch(server.baseUrl + path, body ? {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    } : {})
    const value = await result.json()
    if (!result.ok) throw new Error(`fixture API ${path}: ${result.status} ${JSON.stringify(value)}`)
    return value
  }
  const { scope_id: scopeId } = await api('/v1/scopes/default')
  scopeIdForModel = scopeId
  const harnesses = []
  function harness(config = {}, options = {}) {
    const { initializeTimeoutMs = 30000, maxTokens = 128 } = options
    const dshHome = mkdtempSync(join(home, 'host-'))
    const workspace = join(dshHome, 'workspace')
    mkdirSync(workspace)
    // Copy the distributable files, not TypeScript source or development peers.
    const installed = join(dshHome, 'profiles/sdk/node_modules/powercontext-dsh')
    mkdirSync(installed, { recursive: true })
    for (const entry of ['lib', 'package.json', 'cordis.patch.yml']) cpSync(join(options.plugin ?? pluginRoot, entry), join(installed, entry), { recursive: true })
    const patch = join(dshHome, 'test.patch.json')
    const commandAddress = join(dshHome, 'command-address.txt')
    const commandObserver = join(dshHome, 'command-observer.mjs')
    if (options.commands) cpSync(join(import.meta.dirname, 'command-observer.mjs'), commandObserver)
    const diagnosticsFile = join(dshHome, 'diagnostics.jsonl')
    const loggerObserver = join(dshHome, 'logger-observer.mjs')
    // Observe the real Cordis logger through its public exporter API.
    writeFileSync(loggerObserver, `import { appendFileSync } from 'node:fs'
export function apply(ctx) {
  ctx.logger.exporter({ levels: { default: 3 }, export(message) {
    const line = message.args[0]
    if (typeof line === 'string' && line.startsWith('{"component":"powercontext.dsh"')) {
      appendFileSync(${JSON.stringify(diagnosticsFile)}, line + '\\n')
    }
  } })
}`)
    writeFileSync(patch, JSON.stringify([
      { insert: [{ id: 'diagnostic-observer', name: pathToFileURL(loggerObserver).href }] },
      ...options.commands ? [{ insert: [{ id: 'command-observer',
        name: pathToFileURL(commandObserver).href,
        config: { addressFile: commandAddress },
      }] }] : [],
      { id: 'skill-filesystem', config: { includeDefaultRoots: false, watch: false } },
      { id: 'session-persistence-jsonl', config: { root: join(dshHome, 'sessions'), compression: 'none' } },
      { id: 'llm-deepseek', config: { baseURL: model.url + '/v1', apiKeyEnv: 'DEEPSEEK_API_KEY', thinking: 'disabled' } },
      { insert: [{ id: 'powercontext-dsh', name: pathToFileURL(join(installed, 'lib/index.js')).href, config: {
        baseUrl: proxy.url, timeoutMs: 15000, requestTimeoutMs: 5000, flushOnCapture: true, ...config,
      } }] },
    ]))
    // Keep the runtime isolated from the developer's shared client endpoint; the patch owns this fixture's URL.
    const processEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) =>
      !key.startsWith('POWERCONTEXT_DSH_') && key !== 'POWERCONTEXT_CLIENT_SERVER_URL'))
    const env = { ...processEnv, DSH_HOME: dshHome, DSH_PROFILE: 'sdk', DEEPSEEK_API_KEY: 'runtime-fixture',
      DEEPSEEK_BASE_URL: model.url + '/v1', DSH_TELEMETRY_DISABLED: '1', ...options.env }
    const instance = new DeepSeekHarness({
      dshBin: process.env.DSH_TEST_BIN ?? dshBin, dshHome, patches: [patch], cwd: workspace, processCwd: workspace,
      provider: 'deepseek-official', model: realModel?.model ?? 'deepseek-v4-flash', maxTokens,
      initializeTimeoutMs, requestTimeoutMs: realModel ? 120000 : 30000,
      env,
    })
    harnesses.push(instance)
    const diagnostics = () => existsSync(diagnosticsFile)
      ? readFileSync(diagnosticsFile, 'utf8').trim().split('\n').map(line => JSON.parse(line)) : []
    const command = async (sessionId, view) => {
      const response = await fetch(readFileSync(commandAddress, 'utf8') + '/?session=' + encodeURIComponent(sessionId) + '&view=' + view)
      if (!response.ok) throw new Error(await response.text())
      return response.json()
    }
    const doctor = async sessionId => {
      const result = await command(sessionId, 'doctor')
      return { kind: result.kind, ...JSON.parse(result.text) }
    }
    const status = async sessionId => {
      const result = await command(sessionId, 'status')
      return { kind: result.kind, ...JSON.parse(result.text.split('\nautomatic=')[1]) }
    }
    return { instance, dshHome, workspace, installed, patch, env, diagnostics, doctor, status }
  }
  return {
    home, api, scopeId, calls, mcpCalls, modelRequests, harness, baseUrl: proxy.url,
    setFault(value) {
      fault = value
      if (fault?.path !== '/mcp' || !fault.hold) {
        for (const release of stalledInitializations) release()
      }
    },
    async close() {
      const results = await Promise.allSettled(harnesses.map(instance => instance.close()))
      results.push(...await Promise.allSettled([proxy.close(), server.stop(), model.close()]))
      const errors = results.filter(result => result.status === 'rejected').map(result => result.reason)
      if (errors.length) throw new AggregateError(errors, 'runtime fixture cleanup failed')
    },
  }
}

export function injected(run) {
  return run.events.filter(event => event.type === 'user/message'
    && event.data?.source?.plugin === 'powercontext-dsh'
    && event.data?.source?.sections?.some(section => section.name === 'PowerContext'))
    .map(event => event.data)
}
