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
// Exercise the pinned upstream gateway. Database/continuation modules are unused boundary stubs;
// the plugin message bridge and credential injection are simulated, not Electron acceptance.
import { readFile } from 'node:fs/promises';
import { readFileSync } from 'node:fs';
import { stripTypeScriptTypes } from 'node:module';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { runInNewContext } from 'node:vm';
import { once } from 'node:events';
import { pathToFileURL } from 'node:url';

const [source, plugin] = process.argv.slice(2);
const input = JSON.parse(readFileSync(0, 'utf8'));
const dataUrl = text => `data:text/javascript;base64,${Buffer.from(text).toString('base64')}`;
const brain = join(source, 'apps/desktop/src/main/cindy-brain');
const shared = await readFile(join(source, 'apps/desktop/src/shared/ghost.ts'), 'utf8');
const constants = ['GHOST_ASSISTANT_HOOK_TIMEOUT_MS', 'GHOST_HOOK_FUSE_THRESHOLD', 'GHOST_HOOK_REWRITE_MAX_CHARS', 'GHOST_HOOK_TIMEOUT_MS', 'GHOST_SUB_QUEUE_MAX']
  .map(name => shared.match(new RegExp(`export const ${name} = [^;]+;`))[0]).join('\n');
const owner = stripTypeScriptTypes(await readFile(join(brain, 'ghostOwnerScope.ts'), 'utf8'), { mode: 'transform' });
const unused = 'export function eq(){throw new Error("unexpected database access")};export function getDbClient(){throw new Error("unexpected database access")};export const sessions={};export function isTurnContinuationBoundaryEvent(){throw new Error("unexpected continuation access")};';
const replacements = {
  '../../shared/ghost.js': dataUrl(constants),
  './ghostOwnerScope.js': dataUrl(owner),
  '@cindy/maker-shared/turn-continuation': dataUrl(unused),
  'drizzle-orm': dataUrl(unused),
  '../localDb/client/current.js': dataUrl(unused),
  '../localDb/schema.js': dataUrl(unused),
  '../../shared/pluginIdentity.js': dataUrl('export function installedGhostStoragePart(ghost) { return ghost.manifest.id; }'),
};
const gatewaySource = stripTypeScriptTypes(await readFile(join(brain, 'subscriptionGateway.ts'), 'utf8'), { mode: 'transform' })
  .replace(/from ['"]([^'"]+)['"]/g, (original, path) => replacements[path] ? `from '${replacements[path]}'` : original);
const { GhostSubscriptionGateway } = await import(dataUrl(gatewaySource));

const manifest = JSON.parse(await readFile(join(plugin, 'ghost.json'), 'utf8'));
const worker = spawn(process.execPath, ['--experimental-permission', `--allow-fs-read=${plugin}`, join(plugin, manifest.node.entry)], {
  cwd: plugin,
  env: Object.fromEntries(Object.entries(process.env).filter(([key]) => !['NODE_PATH', 'NODE_OPTIONS'].includes(key) && !key.startsWith('POWERCONTEXT_'))),
  stdio: ['pipe', 'pipe', 'pipe'],
});
worker.stderr.resume();
const pending = new Map();
let sequence = 0;
createInterface({ input: worker.stdout }).on('line', line => {
  const reply = JSON.parse(line);
  pending.get(reply.id)?.(reply.result);
  pending.delete(reply.id);
});
const settings = { enabled: true, capture_prompts: true, server_url: input.url, auth_mode: 'bearer', max_bytes: 6000 };
let dispatch;
let hookSequence = 0;
const toolResults = new Map();
const gateway = new GhostSubscriptionGateway({
  listGhosts: () => [{ manifest, enabled: true }],
  isRunning: () => true,
  wake: async () => {},
  now: Date.now,
  resolveMessageHookContext: () => ({}),
  newHookId: () => `delivery-${++hookSequence}`,
  sendToGhost: (_id, payload) => { void dispatch(payload); },
});
runInNewContext(await readFile(join(plugin, manifest.entry), 'utf8'), {
  setTimeout, clearTimeout,
  fetch: async () => ({ ok: true, json: async () => settings }),
  cindy: {
    onHostMessage: handler => { dispatch = handler; },
    send: async value => {
      if (value.type === 'tool-result') toolResults.get(value.callId)?.(value);
      else gateway.handleVerdict(manifest.id, value);
    },
    node: { request: request => new Promise((resolve, reject) => {
      const id = ++sequence;
      const timer = setTimeout(() => { pending.delete(id); reject(new Error('worker timeout')); }, request.timeoutMs);
      pending.set(id, result => { clearTimeout(timer); resolve({ ok: true, result }); });
      worker.stdin.write(JSON.stringify({
        jsonrpc: '2.0', id, method: request.method, params: request.params,
        cindy: { secrets: { server_credential: JSON.stringify({ server_url: input.url, authorization: `Bearer ${input.token}` }) } },
      }) + '\n');
    }) },
  },
});
async function readTool(sessionId, tool, args) {
  const callId = `read-${++sequence}`;
  const result = new Promise(resolve => { toolResults.set(callId, resolve); });
  try {
    await dispatch({ type: 'tool-call', tool, callId, args: { ...args, session_context: { session_id: sessionId } } });
    return await result;
  } finally { toolResults.delete(callId); }
}

async function runCodex(first) {
  const { AppServerHost, createStdioTransport } = await import(pathToFileURL(input.nativeRuntime).href);
  const logger = { trace() {}, debug() {}, info() {}, warn() {}, error() {}, fatal() {}, child() { return this; } };
  const host = new AppServerHost({
    logger,
    clientInfo: { name: 'powercontext-cindy-acceptance', version: '0.1.0' },
    createTransport: () => createStdioTransport({
      binaryPath: input.codexBinary, cwd: plugin, env: process.env,
      extraArgs: ['-c', 'features.memories=false'],
    }),
  });
  const receipts = [];
  try {
    for (const [sessionId, text] of [
      ['session-a', first.text],
      ['session-b', 'Use the powercontext_prepare_context tool to find the Quartz application deployment codename. Answer only with that codename.'],
    ]) {
      const thread = await host.request('thread/start', {
        ...(input.model ? { model: input.model } : {}),
        cwd: plugin, approvalPolicy: 'never', sandbox: 'read-only', ephemeral: true,
        developerInstructions: 'This is a synthetic integration acceptance task. Use only supplied history or the PowerContext recall tool. Do not read files, run commands, browse, or use other tools. Answer only with the deployment codename.',
        dynamicTools: manifest.tools.filter(tool => sessionId === 'session-b' && tool.name === 'prepare_context').map(tool => ({
          name: `powercontext_${tool.name}`, description: tool.description, inputSchema: tool.parameters,
        })),
      }, { timeoutMs: 30000 });
      const messages = [];
      let calls = 0;
      let resolveDone;
      let rejectDone;
      const done = new Promise((resolve, reject) => { resolveDone = resolve; rejectDone = reject; });
      const subscription = host.subscribeThread(thread.thread.id, {
        itemCompleted: ({ item }) => { if (item.type === 'agentMessage') messages.push(item.text); },
        error: () => { rejectDone(new Error('Codex reported an error')); },
        turnCompleted: ({ turn }) => turn.status === 'completed' ? resolveDone() : rejectDone(new Error(`Codex turn ${turn.status}`)),
        dynamicToolCall: async params => {
          if (params.tool !== 'powercontext_prepare_context') return { success: false, contentItems: [{ type: 'inputText', text: 'Unknown tool' }] };
          calls++;
          const result = await readTool(sessionId, 'prepare_context', params.arguments);
          return { success: result.ok, contentItems: [{ type: 'inputText', text: JSON.stringify(result.result ?? { code: result.errorCode }) }] };
        },
      });
      const timer = setTimeout(() => { rejectDone(new Error('Codex acceptance timed out')); }, 90000);
      try {
        await host.request('turn/start', { threadId: thread.thread.id, input: [{ type: 'text', text }], effort: 'low' }, { timeoutMs: 30000 });
        await done;
        receipts.push({ session_id: sessionId, answer: messages.join('\n'), tool_calls: calls });
      } finally { clearTimeout(timer); await subscription.release(); }
    }
    return receipts;
  } finally { await host.shutdown(); }
}

try {
  const first = await gateway.screenUserMessage({ sessionId: 'session-a', text: input.prompt });
  const second = await gateway.screenUserMessage({ sessionId: 'session-b', text: input.prompt });
  const missing = await gateway.screenUserMessage({ sessionId: 'unbound', text: input.prompt });
  const edited = await gateway.screenUserMessage({ sessionId: 'session-a', text: first.text ?? input.prompt });
  settings.enabled = false;
  const read = await readTool('session-b', 'prepare_context', { query: input.prompt });
  const search = await readTool('session-b', 'search_memory', { query: 'Quartz', mode: 'fts', limit: 5 });
  const unboundRead = await readTool('unbound', 'resolve_scope', {});
  const native = input.nativeRuntime ? await runCodex(first) : undefined;
  process.stdout.write(JSON.stringify({ first, second, missing, edited, read, search, unboundRead, native }));
} finally {
  const exited = once(worker, 'exit');
  worker.kill('SIGTERM');
  const timer = setTimeout(() => worker.kill('SIGKILL'), 3000);
  try { await exited; } finally { clearTimeout(timer); }
}
