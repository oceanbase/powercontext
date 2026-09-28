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

// Execute registered adapters against a controlled transport, without contacting a Server.
import { createInterface } from 'node:readline'
import { OPERATIONS } from '../integrations/dsh/plugins/powercontext/src/operations.generated.ts'

const lines = createInterface({ input: process.stdin, terminal: false })[Symbol.asyncIterator]()
const send = value => process.stdout.write(JSON.stringify(value) + '\n')
const receive = async () => {
  const line = await lines.next()
  if (line.done) throw new Error('Evaluation transport closed before replying')
  return JSON.parse(line.value)
}
const host = process.argv[2]
const scope = 'fixture-scope'
const signal = new AbortController().signal
const request = async (operation, payload) => {
  send({ kind: 'request', operation, payload })
  const reply = await receive()
  if (reply.kind !== 'response') throw new Error('Expected a controlled HTTP response')
  return { kind: 'json', status: OPERATIONS[operation].successStatuses[0], requestId: undefined, value: reply.value }
}
let execute
if (host === 'dsh') {
  const { registerTools } = await import('../integrations/dsh/plugins/powercontext/src/tools.ts')
  const tools = []
  registerTools({ tools: { register: tool => tools.push(tool) }, on: () => {} }, {
    client: { request }, resolveScope: async () => scope, config: {}, log: () => {},
  }, tool => tool)
  execute = (name, args) => {
    const tool = tools.find(tool => tool.name === name)
    if (!tool) throw new Error(`DSH has no tool ${name}`)
    return tool.execute(args, { signal })
  }
} else if (host === 'pi') {
  const { registerTools } = await import('../integrations/pi/plugins/powercontext/src/tools.ts')
  const tools = []
  registerTools({ registerTool: tool => tools.push(tool) }, {
    client: { request }, resolveScope: async () => scope, config: { maxBytes: 8000 },
  })
  execute = async (name, args) => {
    const tool = tools.find(tool => tool.name === name)
    if (!tool) throw new Error(`Pi has no tool ${name}`)
    const result = await tool.execute('evaluation', args, signal, () => {}, {
      cwd: process.cwd(), hasUI: true, ui: { confirm: async () => true },
    })
    return JSON.parse(result.content[0].text)
  }
} else if (host === 'opencode') {
  delete process.env.POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_PATH
  delete process.env.POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_NONCE
  process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = scope
  process.env.POWERCONTEXT_OPENCODE_BASE_URL = 'http://127.0.0.1:1'
  const { OPERATIONS } = await import('../integrations/opencode/plugins/powercontext/src/operations.generated.ts')
  globalThis.fetch = async (url, init) => {
    const path = new URL(url).pathname
    if (path.endsWith('/scope-bindings/resolve')) return Response.json({ scope_id: scope })
    const operation = Object.entries(OPERATIONS).find(([, spec]) => spec.path === path && spec.method === init.method)?.[0]
    if (!operation) throw new Error(`Unexpected evaluation HTTP request: ${init.method} ${path}`)
    const result = await request(operation, JSON.parse(init.body))
    return Response.json(result.value, { status: result.status })
  }
  const { PowerContextPlugin } = await import('../integrations/opencode/plugins/powercontext/src/index.ts')
  const hooks = await PowerContextPlugin({ directory: process.cwd(), client: {
    session: { get: async () => ({ data: { directory: process.cwd() } }) }, app: { log: async () => ({}) },
  } })
  execute = async (name, args) => {
    if (!hooks.tool[name]) throw new Error(`OpenCode has no tool ${name}`)
    return JSON.parse(await hooks.tool[name].execute(args, {
      sessionID: 'evaluation', abort: signal, ask: async () => {},
    }))
  }
} else if (host === 'openclaw') {
  globalThis.fetch = async (url, init) => {
    const path = new URL(url).pathname
    if (path.endsWith('/scope-bindings/resolve')) return Response.json({ scope_id: scope })
    const operation = Object.entries(OPERATIONS).find(([, spec]) => spec.path === path && spec.method === init.method)?.[0]
    if (!operation) throw new Error(`Unexpected evaluation HTTP request: ${init.method} ${path}`)
    const result = await request(operation, JSON.parse(init.body))
    return Response.json(result.value, { status: result.status })
  }
  const { default: plugin } = await import('../integrations/openclaw/plugins/memory-powercontext/dist/index.js')
  const tools = []
  const context = { agentId: 'main', sessionKey: 'agent:main:telegram:direct:fixture' }
  plugin.register({
    config: {}, pluginConfig: { endpoint: 'http://127.0.0.1:1', scopeId: scope },
    runtime: { config: { current: () => ({}) }, agent: { session: { getSessionEntry: () => ({ chatType: 'direct' }) } } },
    registerTool: factory => { const tool = factory(context); if (tool) tools.push(tool) },
    registerMemoryCapability: () => {}, registerCommand: () => {}, registerService: () => {}, on: () => {},
    logger: { warn: () => {}, debug: () => {}, info: () => {} },
  })
  execute = async (name, args) => {
    const tool = tools.find(tool => tool.name === name)
    if (!tool) throw new Error(`OpenClaw has no tool ${name}`)
    return JSON.parse((await tool.execute('evaluation', args, signal)).content[0].text)
  }
} else {
  throw new Error(`Unsupported native adapter: ${host}`)
}
send({ kind: 'ready' })
while (true) {
  const line = await lines.next()
  if (line.done) break
  try {
    const { name, arguments: args } = JSON.parse(line.value)
    send({ kind: 'result', value: await execute(name, args) })
  } catch (error) {
    send({ kind: 'error', message: String(error) })
  }
}
