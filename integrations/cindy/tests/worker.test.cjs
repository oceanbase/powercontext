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
const { createServer } = require('node:http');
const { test } = require('node:test');
const { handleRequest } = require('../plugins/powercontext/node/worker.cjs');

async function backend(t, respond) {
  const requests = [];
  const server = createServer(async (req, res) => {
    let body = '';
    for await (const chunk of req) body += chunk;
    const request = { path: req.url, method: req.method, headers: req.headers, body: body ? JSON.parse(body) : undefined };
    requests.push(request);
    res.setHeader('Content-Type', 'application/json');
    await respond(request, res);
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  t.after(() => { server.closeAllConnections(); return new Promise((resolve) => server.close(resolve)); });
  return { url: `http://127.0.0.1:${server.address().port}`, requests };
}

function request(url, tool, args = {}, options = {}) {
  const config = { server_url: url, auth_mode: 'none', ...options.config };
  return {
    jsonrpc: '2.0', id: 1,
    method: config.auth_mode === 'bearer' ? 'powercontext/authenticated' : 'powercontext/request',
    params: { config, tool, args, session_context: options.context },
    ...(options.credential ? { cindy: { secrets: { server_credential: JSON.stringify(options.credential) } } } : {}),
  };
}

test('scope binding uses trusted session first and never falls back to a global default', async (t) => {
  const server = await backend(t, (req, res) => {
    res.end(JSON.stringify(req.path.endsWith('/resolve') ? { scope_id: 'scope-a' } : { hits: [] }));
  });
  const result = await handleRequest(request(server.url, 'search_memory', { query: 'decision', mode: 'fts' }, {
    context: { session_id: 'session-a', workdir: '/workspace', workdir_is_local: true },
  }));
  assert.deepEqual(result, { ok: true, data: { hits: [] } });
  assert.equal(server.requests[0].body.allow_default, false);
  assert.equal(server.requests[0].body.binding_keys[0].external_id, 'session-a');
  assert.equal(server.requests[0].body.binding_keys[1].kind, 'workspace');
  assert.equal(server.requests[1].body.scope_id, 'scope-a');
});

test('remote workspace paths cannot become local workspace bindings', async (t) => {
  const server = await backend(t, (_req, res) => res.end('{}'));
  const result = await handleRequest(request(server.url, 'bind_scope', { scope_id: 'scope-a', kind: 'workspace' }, {
    context: { session_id: 'remote-session', workdir: '/workspace', workdir_is_local: false },
  }));
  assert.equal(result.code, 'session_context_unavailable');
  assert.equal(server.requests.length, 0);
});

test('missing scope never silently routes a durable write elsewhere', async (t) => {
  const server = await backend(t, (_req, res) => { res.statusCode = 404; res.end('{}'); });
  const result = await handleRequest(request(server.url, 'remember_memory', { kind: 'fact', text: 'A decision' }));
  assert.equal(result.code, 'not_found');
  assert.deepEqual(server.requests.map((item) => item.path), ['/v1/scope-bindings/resolve']);
});

test('credentials are bound to the Server URL and are never taken from ordinary arguments', async (t) => {
  const server = await backend(t, (req, res) => {
    assert.equal(req.headers.authorization, 'Bearer private-test-token');
    res.end('{"items":[]}');
  });
  const options = { config: { auth_mode: 'bearer' }, credential: { server_url: server.url, authorization: 'private-test-token' } };
  const result = await handleRequest(request(server.url, 'list_scopes', {}, options));
  assert.equal(result.ok, true);
  options.credential.server_url = 'https://different.example';
  const mismatch = await handleRequest(request(server.url, 'list_scopes', {}, options));
  assert.equal(mismatch.code, 'credential_url_mismatch');
  const injected = await handleRequest(request(server.url, 'list_scopes', { config: { server_url: 'https://other.example' } }));
  assert.equal(injected.code, 'invalid_request');
  assert.equal(server.requests.length, 1);
  assert.doesNotMatch(JSON.stringify([result, mismatch, injected]), /private-test-token/);
});

test('remote plaintext requires explicit consent and redirects never forward credentials', async (t) => {
  const blocked = await handleRequest(request('http://192.0.2.1:8000', 'list_scopes'));
  assert.equal(blocked.code, 'insecure_http_not_allowed');
  const target = await backend(t, (_req, res) => res.end('{"items":[]}'));
  const server = await backend(t, (_req, res) => {
    res.writeHead(302, { Location: `${target.url}/v1/scopes` }); res.end();
  });
  const result = await handleRequest(request(server.url, 'list_scopes', {}, {
    config: { auth_mode: 'bearer' }, credential: { server_url: server.url, authorization: 'private-test-token' },
  }));
  assert.equal(result.code, 'invalid_response');
  assert.equal(target.requests.length, 0);
});

test('prepared content stays byte-for-byte intact, with explicit assembly forwarded', async (t) => {
  const content = '\nHistorical context: \u4e2d\u6587\n';
  const envelope = { schema: 'powercontext.prepared-context.v1', status: 'ready', content, content_bytes: Buffer.byteLength(content) };
  const server = await backend(t, (req, res) => res.end(JSON.stringify(
    req.path.endsWith('/resolve') ? { scope_id: 'scope-a' } : envelope,
  )));
  const assembly = { format: 'markdown', sections: [{ family: 'topic-memory', limit: 2 }] };
  const result = await handleRequest(request(server.url, 'prepare_context', { query: 'context', max_bytes: 512, assembly }));
  assert.deepEqual(result, { ok: true, data: envelope });
  assert.deepEqual(server.requests[1].body.assembly, assembly);
  envelope.content_bytes += 1;
  const invalid = await handleRequest(request(server.url, 'prepare_context', { query: 'context' }));
  assert.equal(invalid.code, 'invalid_response');
  assert.equal(Object.hasOwn(server.requests.at(-1).body, 'assembly'), false);
  assert.equal(Object.hasOwn(invalid, 'data'), false);
});

test('empty prepared context is a successful response', async (t) => {
  const envelope = { schema: 'powercontext.prepared-context.v1', status: 'empty', content: null, content_bytes: 0 };
  const server = await backend(t, (req, res) => res.end(JSON.stringify(
    req.path.endsWith('/resolve') ? { scope_id: 'scope-a' } : envelope,
  )));
  assert.deepEqual(await handleRequest(request(server.url, 'prepare_context', { query: 'context' })), { ok: true, data: envelope });
});

test('typed HTTP failures retain domain errors and redact backend bodies', async (t) => {
  const cases = [[401, 'authentication_failed'], [403, 'forbidden'], [404, 'not_found'],
    [409, 'conflict'], [422, 'invalid_request'], [500, 'invalid_response'], [503, 'server_unavailable']];
  for (const [status, code] of cases) {
    await t.test(String(status), async (t) => {
      const server = await backend(t, (_req, res) => { res.statusCode = status; res.end('private-test-token backend body'); });
      const result = await handleRequest(request(server.url, 'list_scopes'));
      assert.equal(result.code, code);
      assert.equal(result.http_status, status);
      assert.doesNotMatch(JSON.stringify(result), /private-test-token|backend body|127\.0\.0\.1/);
      if (status === 503) assert.equal(result.recovery, 'powercontext doctor');
      if (status === 404) assert.equal((await handleRequest(request(server.url, 'status'))).code, 'version_mismatch');
    });
  }
});

test('malformed, oversized, and invalid-shape success responses are rejected', async (t) => {
  for (const body of ['{', '{}', JSON.stringify({ items: [], padding: 'x'.repeat(1_048_576) })]) {
    await t.test(String(body.length), async (t) => {
      const server = await backend(t, (_req, res) => res.end(body));
      assert.equal((await handleRequest(request(server.url, 'list_scopes'))).code, 'invalid_response');
    });
  }
});

test('timeout preserves an unknown write outcome and never retries it', async (t) => {
  const server = await backend(t, (req, res) => {
    if (req.path.endsWith('/resolve')) res.end('{"scope_id":"scope-a"}');
  });
  const result = await handleRequest(request(server.url, 'remember_memory', { kind: 'fact', text: 'Decision' }, {
    config: { timeout_ms: 1000 },
  }));
  assert.equal(result.code, 'server_unavailable');
  assert.equal(result.write_status, 'unknown');
  assert.equal(server.requests.filter((item) => item.path.endsWith('/remember')).length, 1);
});
