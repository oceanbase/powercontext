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
const script = readFileSync(resolve(__dirname, '../plugins/powercontext-hooks/settings.js'), 'utf8');

function settingsHost() {
  const elements = new Map();
  const saved = { config: {}, secret: undefined };
  const element = id => {
    if (!elements.has(id)) elements.set(id, { value: '', checked: false, handlers: {},
      addEventListener(event, handler) { this.handlers[event] = handler; } });
    return elements.get(id);
  };
  runInNewContext(script, {
    URL, document: { getElementById: element },
    fetch: async (url, options) => {
      if (url === '/kv' && options?.method === 'PUT') saved.config = JSON.parse(options.body);
      if (url === '/secrets/server_credential' && options?.method === 'PUT') {
        saved.secret = JSON.parse(JSON.parse(options.body).value);
      }
      return { ok: true, json: async () => url === '/kv' ? saved.config
        : (saved.secret ? [{ key: 'server_credential', saved: true }] : []) };
    },
  });
  return { element, saved };
}

test('settings save a URL-bound Bearer header outside ordinary plugin configuration', async () => {
  const host = settingsHost();
  await new Promise(done => setImmediate(done));
  host.element('auth-mode').value = 'bearer';
  host.element('token').value = 'Bearer synthetic-token';
  host.element('enabled').checked = true;
  await host.element('settings').handlers.submit({ preventDefault() {} });
  assert.deepEqual(host.saved.secret, { server_url: 'http://127.0.0.1:8000', authorization: 'Bearer synthetic-token' });
  assert.equal(host.saved.config.auth_mode, 'bearer');
  assert.equal(JSON.stringify(host.saved.config).includes('synthetic-token'), false);
  assert.equal(host.element('token').value, '');
});

test('changing the Server requires a new credential before saving Bearer settings', async () => {
  const host = settingsHost();
  await new Promise(done => setImmediate(done));
  host.element('auth-mode').value = 'bearer';
  host.element('token').value = 'synthetic-token';
  await host.element('settings').handlers.submit({ preventDefault() {} });
  host.element('server-url').value = 'https://different.example';
  await host.element('settings').handlers.submit({ preventDefault() {} });
  assert.equal(host.saved.config.server_url, 'http://127.0.0.1:8000');
  assert.match(host.element('status').textContent, /Enter a Bearer token/);
});
