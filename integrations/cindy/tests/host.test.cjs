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
'use strict';

const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { join } = require('node:path');
const { test } = require('node:test');
const { runInNewContext } = require('node:vm');
const main = readFileSync(join(__dirname, '../plugins/powercontext/main.js'), 'utf8');

function host({ config = {}, result = { ok: true, data: { hits: [] } }, storage = new Map(), notifyFails = false } = {}) {
  let listener;
  const messages = [];
  const calls = [];
  runInNewContext(main, {
    fetch: async (path) => ({ ok: true, json: async () => config, text: async () => `Manual at ${path}` }),
    localStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    cindy: {
      onHostMessage: (handler) => { listener = handler; },
      node: { request: async (request) => { calls.push(JSON.parse(JSON.stringify(request))); return { ok: true, result }; } },
      send: async (message) => {
        if (message.type === 'notify' && notifyFails) throw new Error('Host notification unavailable');
        messages.push(JSON.parse(JSON.stringify(message)));
        return { ok: true };
      },
    },
  });
  return { call: listener, messages, calls };
}

test('host bridges trusted context and saved configuration without putting credentials in arguments', async () => {
  const runtime = host({ config: { server_url: 'https://memory.example', auth_mode: 'bearer', scope_id: 'scope-a' } });
  const context = { session_id: 'session-a', workdir: '/workspace', workdir_is_local: true };
  await runtime.call({ type: 'tool-call', callId: 'call-a', tool: 'search_memory', args: { query: 'decision', session_context: context } });
  assert.equal(runtime.calls[0].method, 'powercontext/authenticated');
  assert.deepEqual(runtime.calls[0].params.args, { query: 'decision' });
  assert.deepEqual(runtime.calls[0].params.session_context, context);
  assert.equal(runtime.calls[0].params.config.scope_id, 'scope-a');
  assert.deepEqual(runtime.messages, [{ type: 'tool-result', callId: 'call-a', ok: true, result: { hits: [] } }]);
});

test('diagnostics are separate from tool results and deduplicated across sandbox invocations', async () => {
  const storage = new Map();
  const result = { ok: false, code: 'server_unavailable', http_status: 503, message: 'PowerContext operation failed.' };
  const first = host({ storage, result });
  await first.call({ type: 'tool-call', callId: 'first', tool: 'prepare_context', args: { query: 'private query' } });
  const second = host({ storage, result });
  await second.call({ type: 'tool-call', callId: 'second', tool: 'prepare_context', args: {} });
  const notifications = [...first.messages, ...second.messages].filter((item) => item.type === 'notify');
  assert.equal(notifications.length, 1);
  assert.deepEqual(JSON.parse(notifications[0].text), {
    component: 'powercontext.cindy', event: 'prepare_context', outcome: 'server_unavailable', http_status: 503,
    recovery: 'powercontext doctor',
  });
  assert.ok(notifications[0].text.length <= 200);
  assert.equal(first.messages.at(-1).ok, false);
  assert.equal(second.messages.at(-1).ok, false);
  assert.doesNotMatch(JSON.stringify(first.messages), /private query/);
});

test('failed notification cannot suppress a tool result or the next tool call', async () => {
  const runtime = host({ notifyFails: true, result: { ok: false, code: 'invalid_response', message: 'Operation failed.' } });
  await runtime.call({ type: 'tool-call', callId: 'first', tool: 'status', args: {} });
  await runtime.call({ type: 'tool-call', callId: 'next', tool: 'read_manual', args: {} });
  assert.deepEqual(runtime.messages.map((item) => [item.callId, item.ok]), [['first', false], ['next', true]]);
  assert.equal(runtime.messages[1].result.content, 'Manual at /MANUAL.md');
});

test('unknown write outcomes stay visible to the agent', async () => {
  const runtime = host({ result: { ok: false, code: 'server_unavailable', write_status: 'unknown' } });
  await runtime.call({ type: 'tool-call', callId: 'write', tool: 'remember_memory', args: {} });
  assert.match(runtime.messages.at(-1).message, /outcome is unknown/);
});
