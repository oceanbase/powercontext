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
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { once } from 'node:events';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { createServer } from 'node:net';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createInterface } from 'node:readline';
import { fileURLToPath } from 'node:url';
import { runInNewContext } from 'node:vm';

const root = fileURLToPath(new URL('../../../', import.meta.url));
const plugin = join(root, 'integrations/cindy/plugins/powercontext');
const temporary = await mkdtemp(join(tmpdir(), 'powercontext-cindy-smoke-'));
const listener = createServer();
listener.listen(0, '127.0.0.1');
await once(listener, 'listening');
const port = listener.address().port;
await new Promise((done) => listener.close(done));
const url = `http://127.0.0.1:${port}`;
const token = randomUUID();
const env = Object.fromEntries(Object.entries(process.env).filter(([key]) => !key.startsWith('POWERCONTEXT_')));
Object.assign(env, {
  POWERCONTEXT_HOME: join(temporary, 'home'),
  POWERCONTEXT_SERVER_DATABASE_URL: `sqlite+aiosqlite:///${join(temporary, 'runtime.db')}`,
  POWERCONTEXT_SERVER_AUTH_ENABLED: 'true',
  POWERCONTEXT_SERVER_AUTH_TOKEN: token,
});
const executable = join(root, '.venv', process.platform === 'win32' ? 'Scripts/powercontext.exe' : 'bin/powercontext');
const server = spawn(executable, ['server', 'run', '--host', '127.0.0.1', '--port', String(port)], { cwd: temporary, env });
let serverError;
server.on('error', (error) => { serverError = error; });
server.stdout.resume();
let serverLog = '';
server.stderr.on('data', (chunk) => { serverLog = (serverLog + chunk).slice(-16000); });
const worker = spawn(process.execPath, [join(plugin, 'node/worker.cjs')], { stdio: ['pipe', 'pipe', 'pipe'] });
const pending = new Map();
const lines = createInterface({ input: worker.stdout });
lines.on('line', (line) => {
  const reply = JSON.parse(line);
  pending.get(reply.id)?.(reply.result);
  pending.delete(reply.id);
});
let sequence = 0;
const workerRequest = (request) => new Promise((done, reject) => {
  const id = ++sequence;
  const timer = setTimeout(() => { pending.delete(id); reject(new Error('Worker response timed out')); }, 20000);
  pending.set(id, (result) => { clearTimeout(timer); done({ ok: true, result }); });
  worker.stdin.write(`${JSON.stringify({ jsonrpc: '2.0', id, ...request,
    ...(request.method === 'powercontext/authenticated'
      ? { cindy: { secrets: { server_credential: JSON.stringify({ server_url: url, authorization: token }) } } } : {}),
  })}\n`);
});

async function stop(child) {
  if (child.exitCode !== null || child.signalCode !== null || !child.pid) return;
  const exited = once(child, 'exit');
  child.kill('SIGTERM');
  const timer = setTimeout(() => child.kill('SIGKILL'), 5000);
  try { await exited; } finally { clearTimeout(timer); }
}

try {
  const deadline = Date.now() + 60000;
  while (true) {
    if (serverError) throw new Error('Server could not start; run uv sync --locked first.', { cause: serverError });
    if (server.exitCode !== null) throw new Error(`Server exited before readiness: ${serverLog.replaceAll(token, '[REDACTED]')}`);
    try { if ((await fetch(`${url}/health/ready`, { signal: AbortSignal.timeout(1000) })).ok) break; } catch {}
    if (Date.now() > deadline) throw new Error('Server readiness timed out.');
    await new Promise((done) => setTimeout(done, 250));
  }
  let dispatch;
  let config = { server_url: url, auth_mode: 'none' };
  const messages = [];
  const storage = new Map();
  runInNewContext(await readFile(join(plugin, 'main.js'), 'utf8'), {
    fetch: async (path) => ({ ok: true, json: async () => config,
      text: async () => readFile(join(plugin, path.slice(1)), 'utf8') }),
    localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    cindy: { onHostMessage: (handler) => { dispatch = handler; }, node: { request: workerRequest },
      send: async (message) => { messages.push(JSON.parse(JSON.stringify(message))); return { ok: true }; } },
  });
  const context = { session_id: 'cindy-smoke', workdir: temporary, workdir_is_local: true, workdir_is_read_only: false };
  async function call(tool, args = {}, expected = true) {
    const callId = randomUUID();
    await dispatch({ type: 'tool-call', callId, tool, args: { ...args, session_context: context } });
    const result = messages.find((message) => message.type === 'tool-result' && message.callId === callId);
    assert.equal(result?.ok, expected, `${tool}: ${JSON.stringify(result)}`);
    return result;
  }
  assert.equal((await call('status', {}, false)).errorCode, 'authentication_failed');
  config = { server_url: url, auth_mode: 'bearer' };
  await call('status');
  const scope = (await call('create_scope', { title: 'Cindy smoke', summary: 'Disposable integration verification', idempotency_key: 'cindy-smoke' })).result;
  await call('bind_scope', { scope_id: scope.scope_id, kind: 'session' });
  assert.equal((await call('resolve_scope')).result.scope_id, scope.scope_id);
  const scopes = [];
  let cursor;
  do {
    const page = (await call('list_scopes', { limit: 1, ...(cursor ? { cursor } : {}) })).result;
    assert.ok(page.items.length <= 1);
    scopes.push(...page.items);
    cursor = page.next_cursor;
    assert.ok(scopes.length < 10, 'Unexpected scopes in the fresh smoke database');
  } while (cursor);
  assert.ok(scopes.some((item) => item.scope_id === scope.scope_id));
  const memory = 'Cindy integration uses a URL-bound credential and explicit Scope bindings.';
  await call('remember_memory', { kind: 'decision', text: memory });
  const search = (await call('search_memory', { query: 'Cindy', mode: 'fts' })).result;
  assert.ok(search.hits.length > 0);
  const prepared = (await call('prepare_context', { query: 'Cindy', assembly: { format: 'markdown', sections: [{ family: 'memory', limit: 2 }] } })).result;
  assert.equal(prepared.status, 'ready');
  assert.ok(prepared.content.includes(memory));
  assert.equal(Buffer.byteLength(prepared.content), prepared.content_bytes);
  const captureArgs = { source_id: 'cindy-smoke-source', content: 'The plugin smoke scenario completed successfully.' };
  const captured = (await call('capture_content_source', captureArgs)).result;
  const replayed = (await call('capture_content_source', captureArgs)).result;
  assert.equal(captured.status, 'accepted');
  assert.deepEqual(replayed.source, captured.source);
  assert.equal(replayed.position, captured.position);
  const conflict = await call('capture_content_source', { ...captureArgs, content: 'Changed content.' }, false);
  assert.equal(conflict.errorCode, 'conflict');
  assert.ok((await call('read_manual')).result.content.length > 0);
  context.session_id = 'different-session';
  const unbound = await call('resolve_scope', {}, false);
  assert.equal(unbound.errorCode, 'not_found');
  assert.ok(!JSON.stringify(messages).includes(token));
  console.log('Verified main.js -> JSON-RPC worker -> authenticated SQLite Server: Scope, Memory, prepared text, capture, replay, and isolation.');
  console.log('Cindy Electron installation and safeStorage UI still require desktop verification.');
} finally {
  await stop(worker);
  await stop(server);
  await rm(temporary, { recursive: true, force: true });
}
