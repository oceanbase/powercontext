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
const { resolve } = require('node:path');
const { runInNewContext } = require('node:vm');
const { test } = require('node:test');
const { runHook, runReadTool, CONTEXT_START } = require('../plugins/powercontext-hooks/node/worker.cjs');
const main = readFileSync(resolve(__dirname, '../plugins/powercontext-hooks/main.js'), 'utf8');
const config = { enabled: true, server_url: 'http://127.0.0.1:8000', scope_id: 'project' };
const input = { config, session_id: 'session', hook_id: 'event', text: 'What is the deployment codename?' };
const response = (value, status = 200) => new Response(JSON.stringify(value), { status });

function server(prepared = 'Deployment codename: QUARTZ-8413.') {
  const captures = [];
  const request = async (url, init) => {
    const payload = JSON.parse(init.body);
    if (url.endsWith('/scope-bindings/resolve')) {
      assert.equal(payload.allow_default, false);
      assert.deepEqual(payload.binding_keys, [{ integration: 'cindy', kind: 'session', external_id: 'session' }]);
      return response({ scope_id: 'project' });
    }
    if (url.endsWith('/context/prepare')) {
      assert.equal(payload.scope_id, 'project');
      return response(prepared === null
        ? { schema: 'powercontext.prepared-context.v1', status: 'empty', content: null, content_bytes: 0 }
        : { schema: 'powercontext.prepared-context.v1', status: 'ready', content: prepared, content_bytes: Buffer.byteLength(prepared) });
    }
    assert.ok(url.endsWith('/sources/content'));
    assert.equal(init.headers['User-Agent'], 'powercontext-cindy-hooks/0.1.0');
    captures.push(payload);
    return response({ status: 'accepted', position: 1, source: { name: payload.source_id, version: 'v1' } }, 202);
  };
  return { captures, request };
}

function host(settings, worker, acknowledge = async () => {}) {
  let dispatch;
  const sent = [];
  runInNewContext(main, {
    setTimeout, clearTimeout,
    fetch: async () => ({ ok: true, json: async () => settings }),
    cindy: { onHostMessage: handler => { dispatch = handler; }, node: { request: worker }, send: async value => { sent.push(value); await acknowledge(value); } },
  });
  return { dispatch, sent };
}
const event = { type: 'event', name: 'will-user-message', hookId: 'event', data: { sessionId: 'session', text: input.text } };

test('automatic recall rewrites the host message and captures only the original prompt', async () => {
  const backend = server();
  const adapter = host(config, async request => ({ ok: true, result: await runHook(request.params, undefined, backend.request) }));
  await adapter.dispatch(event);
  assert.equal(adapter.sent[0].action, 'rewrite');
  assert.ok(adapter.sent[0].text.startsWith(input.text));
  assert.ok(adapter.sent[0].text.includes('QUARTZ-8413'));
  assert.equal(backend.captures[0].content, input.text);
  assert.equal(backend.captures[0].metadata.session_id, 'session');
  assert.equal(backend.captures[0].metadata.hook_id, 'event');
});

test('same delivery identity preserves Source identity; a new hook is a new prompt', async () => {
  const backend = server();
  const first = await runHook(input, undefined, backend.request);
  const repeated = await runHook(input, undefined, backend.request);
  const next = await runHook({ ...input, hook_id: 'another-event' }, undefined, backend.request);
  assert.equal(first.source_id, repeated.source_id);
  assert.notEqual(next.source_id, first.source_id);
});

test('empty recall allows normal work while independently capturing the original prompt', async () => {
  const backend = server(null);
  const adapter = host(config, async request => ({ ok: true, result: await runHook(request.params, undefined, backend.request) }));
  await adapter.dispatch(event);
  assert.equal(adapter.sent[0].action, 'allow');
  assert.equal(backend.captures[0].content, input.text);
});

test('a malformed PreparedContext is not injected and does not authorize a Memory write', async () => {
  const backend = server();
  const request = async (url, init) => url.endsWith('/context/prepare')
    ? response({ schema: 'wrong', status: 'ready', content: 'invented', content_bytes: 8 }) : backend.request(url, init);
  const adapter = host(config, async value => ({ ok: true, result: await runHook(value.params, undefined, request) }));
  await adapter.dispatch(event);
  assert.equal(adapter.sent[0].action, 'allow');
  assert.equal(backend.captures[0].content, input.text);
});

test('a missing binding stops scoped work without falling back or blocking the message', async () => {
  const adapter = host({ ...config, scope_id: undefined }, async request => ({
    ok: true, result: await runHook(request.params, undefined, async (_url, init) => {
      assert.equal(JSON.parse(init.body).allow_default, false);
      return response({ error: { code: 'scope_binding_target_missing' } }, 409);
    }),
  }));
  await adapter.dispatch(event);
  assert.equal(adapter.sent[0].action, 'allow');
  await adapter.dispatch({ type: 'tool-call', tool: 'status', callId: 'status', args: { session_context: { session_id: 'session' } } });
  assert.equal(adapter.sent[1].result.code, 'scope_binding_target_missing');
});

test('credentials bound to another URL never reach a network request', async () => {
  let contacted = false;
  const value = await runHook({ ...input, config: { ...config, auth_mode: 'bearer' } },
    JSON.stringify({ server_url: 'https://another.example', authorization: 'Bearer synthetic-token' }),
    async () => { contacted = true; return response({}); });
  assert.equal(value.ok, false);
  const read = await runReadTool({ config: { ...config, auth_mode: 'bearer' }, session_id: 'session', tool: 'resolve_scope' },
    JSON.stringify({ server_url: 'https://another.example', authorization: 'Bearer synthetic-token' }),
    async () => { contacted = true; return response({}); });
  assert.equal(read.ok, false);
  assert.equal(contacted, false);
});

test('edited recalled messages and flagged credentials are not captured again', async () => {
  for (const text of [input.text + CONTEXT_START + 'old history', 'Bearer synthetic-token', 'api_key=synthetic']) {
    let contacted = false;
    await runHook({ ...input, text }, undefined, async () => { contacted = true; return response({}); });
    assert.equal(contacted, false);
  }
});

test('capture can be disabled independently of automatic recall', async () => {
  const backend = server();
  const outcome = await runHook({ ...input, config: { ...config, capture_prompts: false } }, undefined, backend.request);
  assert.equal(outcome.prepare, 'ready');
  assert.equal(outcome.capture, 'disabled');
  assert.deepEqual(backend.captures, []);
});

test('uncertain Source acceptance is reported without an automatic retry', async () => {
  const backend = server();
  let writes = 0;
  const outcome = await runHook(input, undefined, async (url, init) => {
    if (!url.endsWith('/sources/content')) return backend.request(url, init);
    writes++;
    throw new Error('connection closed after transmission');
  });
  assert.equal(outcome.capture, 'unknown');
  assert.equal(writes, 1);
});

test('a hook timeout returns one allow verdict and ignores a late rewrite', async () => {
  let release;
  const adapter = host(config, () => new Promise(done => { release = done; }));
  const pending = adapter.dispatch(event);
  await new Promise(done => setTimeout(done, 2250));
  assert.equal(adapter.sent[0].action, 'allow');
  release({ ok: true, result: { ok: true, prepare: 'ready', content: 'late history' } });
  await pending;
  assert.equal(adapter.sent.length, 1);
});

test('oversized rewrites allow the original message without truncating it', async () => {
  const adapter = host(config, async () => ({ ok: true, result: { ok: true, prepare: 'ready', content: 'history' } }));
  await adapter.dispatch({ ...event, data: { ...event.data, text: 'x'.repeat(15990) } });
  assert.equal(adapter.sent[0].action, 'allow');
});

test('a worker reached after the hook deadline cannot start a late capture', async () => {
  let contacted = false;
  const outcome = await runHook({ ...input, deadline_at: Date.now() - 1 }, undefined,
    async () => { contacted = true; return response({}); });
  assert.equal(outcome.capture, 'not_sent');
  assert.equal(contacted, false);
});

test('overlapping sessions retain the latest session outcome after an earlier verdict acknowledgement', async () => {
  let release;
  const oldAcknowledgement = new Promise(done => { release = done; });
  const adapter = host(config, async request => ({ ok: true, result: {
    ok: true, prepare: request.params.session_id === 'first' ? 'ready' : 'empty', capture: 'disabled',
    scope_id: request.params.session_id, content: 'history',
  } }), async message => {
    if (message.type === 'event-verdict' && message.hookId === 'first-event') await oldAcknowledgement;
  });
  const first = adapter.dispatch({ ...event, hookId: 'first-event', data: { ...event.data, sessionId: 'first' } });
  await new Promise(done => setImmediate(done));
  await adapter.dispatch({ ...event, hookId: 'second-event', data: { ...event.data, sessionId: 'second' } });
  release();
  await first;
  await adapter.dispatch({ type: 'tool-call', tool: 'status', callId: 'status', args: { session_context: { session_id: 'second' } } });
  const result = adapter.sent.at(-1).result;
  assert.equal(result.session_id, 'second');
  assert.equal(result.injection, 'not_applied');
  await adapter.dispatch({ type: 'tool-call', tool: 'status', callId: 'first-status', args: { session_context: { session_id: 'first' } } });
  assert.equal(adapter.sent.at(-1).result.session_id, 'first');
  assert.equal(adapter.sent.at(-1).result.injection, 'verdict_sent');
});

test('plugin recall works with automatic hooks disabled and shares the hook Scope resolver', async () => {
  const backend = server();
  const adapter = host({ ...config, enabled: false }, async request => ({
    ok: true, result: await runReadTool(request.params, undefined, backend.request),
  }));
  await adapter.dispatch({ type: 'tool-call', tool: 'prepare_context', callId: 'read', args: {
    session_context: { session_id: 'session' }, query: input.text,
  } });
  const result = adapter.sent[0];
  assert.equal(result.ok, true);
  assert.equal(result.result.scope_id, 'project');
  assert.equal(result.result.result.content, 'Deployment codename: QUARTZ-8413.');
  assert.deepEqual(backend.captures, []);
});

test('plugin reads reject model-selected Scope and connection overrides before contacting the Server', async () => {
  for (const args of [{ scope_id: 'another', query: 'task' }, { server_url: 'https://another.example', query: 'task' }]) {
    const result = await runReadTool({ config, session_id: 'session', tool: 'prepare_context', args }, undefined,
      async () => { assert.fail('must not contact Server'); });
    assert.equal(result.ok, false);
    assert.equal(result.code, 'invalid_arguments');
  }
});

test('plugin reads require a trusted session identity and refresh changed bindings', async () => {
  const request = async () => response({ error: { code: 'scope_binding_target_missing' } }, 409);
  const read = { config, session_id: 'session', tool: 'resolve_scope' };
  assert.equal((await runReadTool(read, undefined, server().request)).data.scope_id, 'project');
  assert.equal((await runReadTool(read, undefined, request)).code, 'scope_binding_target_missing');
  assert.equal((await runReadTool({ ...read, session_id: undefined }, undefined,
    async () => { assert.fail('missing session'); })).code, 'session_unavailable');
});

test('canonical and legacy saved credentials produce the same authenticated hook and read requests', async () => {
  for (const authorization of ['synthetic-token', 'Bearer synthetic-token']) {
    const backend = server();
    const settings = { ...config, auth_mode: 'bearer' };
    const credential = JSON.stringify({ server_url: config.server_url, authorization });
    const request = async (url, init) => {
      assert.equal(init.headers.Authorization, 'Bearer synthetic-token');
      return backend.request(url, init);
    };
    assert.equal((await runHook({ ...input, config: settings }, credential, request)).prepare, 'ready');
    assert.equal((await runReadTool({ config: settings, session_id: 'session', tool: 'resolve_scope' }, credential, request)).ok, true);
  }
});

test('status cannot expose another session outcome', async () => {
  const adapter = host(config, async () => ({ ok: true, result: { ok: true, prepare: 'empty', capture: 'disabled', scope_id: 'private' } }));
  await adapter.dispatch(event);
  await adapter.dispatch({ type: 'tool-call', tool: 'status', callId: 'other', args: { session_context: { session_id: 'other' } } });
  assert.equal(adapter.sent.at(-1).result.outcome, 'not_observed');
  assert.equal(adapter.sent.at(-1).result.scope_id, undefined);
});
