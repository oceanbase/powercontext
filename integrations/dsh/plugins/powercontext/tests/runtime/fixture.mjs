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
export const pluginRoot = resolve(import.meta.dirname, '../..')

async function listen(handler) {
  const server = createServer((req, res) => {
    Promise.resolve(handler(req, res)).catch(() => {
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
  const chunks = []
  for await (const chunk of req) chunks.push(chunk)
  const text = Buffer.concat(chunks).toString()
  return text ? JSON.parse(text) : undefined
}

function json(res, value, status = 200) {
  res.writeHead(status, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(value))
}

export async function environment({ realModel } = {}) {
  const home = mkdtempSync(join(tmpdir(), 'pc-dsh-runtime-'))
  const modelRequests = []
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
    const needsTool = JSON.stringify(body.messages).includes('RUN_PC_SEARCH')
      && !body.messages.some(message => message.role === 'tool')
    const content = JSON.stringify(body.messages).includes(CANARY) ? CANARY : 'Task completed.'
    res.writeHead(200, { 'Content-Type': 'text/event-stream' })
    const chunk = (delta, finish_reason = null) => `data: ${JSON.stringify({
      id: 'dsh-fixture', object: 'chat.completion.chunk', model: body.model, created: 0,
      choices: [{ index: 0, delta, finish_reason }],
    })}\n\n`
    if (needsTool) {
      res.end(chunk({ role: 'assistant', tool_calls: [{
        index: 0, id: 'fixture-search', type: 'function',
        function: { name: 'pc_search', arguments: JSON.stringify({ query: 'aurora deployment color' }) },
      }] }) + chunk({}, 'tool_calls') + 'data: [DONE]\n\n')
    } else {
      res.end(chunk({ role: 'assistant', content }) + chunk({}, 'stop') + 'data: [DONE]\n\n')
    }
  })
  let server
  try {
    server = await startPowerContextServer({ env: {
    OPENAI_API_KEY: 'runtime-fixture',
    POWERCONTEXT_SERVER_INFERENCE: JSON.stringify({
      generation_model: `openai-chat:${realModel?.model ?? 'fixture'}`, generation_base_url: model.url + '/v1',
    }),
    } })
  } catch (error) {
    await model.close()
    throw error
  }
  const calls = []
  let fault
  const proxy = await listen(async (req, res) => {
    const path = new URL(req.url, 'http://localhost').pathname
    const body = await bodyOf(req)
    const call = { path, body }
    calls.push(call)
    if (fault?.path === path || fault?.path === '*') {
      if (fault.hold) {
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
  const harnesses = []
  function harness(config = {}) {
    const dshHome = mkdtempSync(join(home, 'host-'))
    const workspace = join(dshHome, 'workspace')
    mkdirSync(workspace)
    // Copy the distributable files, not TypeScript source or development peers.
    const installed = join(dshHome, 'profiles/sdk/node_modules/powercontext-dsh')
    mkdirSync(installed, { recursive: true })
    for (const entry of ['lib', 'package.json', 'cordis.patch.yml']) cpSync(join(pluginRoot, entry), join(installed, entry), { recursive: true })
    const patch = join(dshHome, 'test.patch.json')
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
      { id: 'skill-filesystem', config: { includeDefaultRoots: false, watch: false } },
      { id: 'session-persistence-jsonl', config: { root: join(dshHome, 'sessions'), compression: 'none' } },
      { id: 'llm-deepseek', config: { baseURL: model.url + '/v1', apiKeyEnv: 'DEEPSEEK_API_KEY', thinking: 'disabled' } },
      { insert: [{ id: 'powercontext-dsh', name: pathToFileURL(join(installed, 'lib/index.js')).href, config: {
        baseUrl: proxy.url, timeoutMs: 15000, requestTimeoutMs: 5000, flushOnCapture: true, ...config,
      } }] },
    ]))
    const processEnv = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('POWERCONTEXT_DSH_')))
    const env = { ...processEnv, DSH_HOME: dshHome, DSH_PROFILE: 'sdk', DEEPSEEK_API_KEY: 'runtime-fixture',
      DEEPSEEK_BASE_URL: model.url + '/v1', DSH_TELEMETRY_DISABLED: '1' }
    const instance = new DeepSeekHarness({
      dshBin: process.env.DSH_TEST_BIN ?? dshBin, dshHome, patches: [patch], cwd: workspace, processCwd: workspace,
      provider: 'deepseek-official', model: realModel?.model ?? 'deepseek-v4-flash', maxTokens: 128,
      initializeTimeoutMs: 30000, requestTimeoutMs: realModel ? 120000 : 30000,
      env,
    })
    harnesses.push(instance)
    const diagnostics = () => existsSync(diagnosticsFile)
      ? readFileSync(diagnosticsFile, 'utf8').trim().split('\n').map(line => JSON.parse(line)) : []
    return { instance, dshHome, workspace, installed, patch, env, diagnostics }
  }
  return {
    home, api, scopeId, calls, modelRequests, harness,
    setFault(value) { fault = value },
    async close() {
      const results = await Promise.allSettled(harnesses.map(instance => instance.close()))
      results.push(...await Promise.allSettled([proxy.close(), server.stop(), model.close()]))
      const errors = results.filter(result => result.status === 'rejected').map(result => result.reason)
      if (errors.length) throw new AggregateError(errors, 'runtime fixture cleanup failed')
    },
  }
}

export function injected(run) {
  return run.events.filter(event => event.type === 'user/message' && event.data?.source?.plugin === 'powercontext-dsh')
    .map(event => event.data)
}
