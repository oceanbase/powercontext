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
const { handleRequest } = require('../plugins/powercontext/node/worker.cjs');
const source = readFileSync(join(__dirname, '../plugins/powercontext/settings.js'), 'utf8');

async function settings(config = {}, credential) {
  const elements = new Map();
  const state = { config, credential, failSave: false };
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', checked: false, textContent: '', listeners: {},
      addEventListener(event, handler) { this.listeners[event] = handler; },
    });
    return elements.get(id);
  };
  runInNewContext(source, {
    URL,
    document: { getElementById: element },
    fetch: async (path, options = {}) => {
      let body;
      if (path === '/kv') {
        if (options.method === 'PUT') {
          if (state.failSave) return { ok: false };
          state.config = JSON.parse(options.body);
        }
        body = state.config;
      } else if (path === '/secrets') {
        body = [{ key: 'server_credential', saved: Boolean(state.credential) }];
      } else if (path === '/secrets/server_credential') {
        state.credential = options.method === 'DELETE' ? undefined : JSON.parse(options.body).value;
      } else throw new Error('Unexpected Host endpoint');
      return { ok: true, json: async () => body };
    },
  });
  await new Promise((done) => setImmediate(done));
  return { state, element, save: () => element('settings').listeners.submit({ preventDefault() {} }) };
}

test('settings save credentials only into the Host vault, alongside their normalized URL', async () => {
  const page = await settings();
  page.element('server-url').value = 'https://memory.example/mcp/';
  page.element('auth-mode').value = 'bearer';
  page.element('token').value = 'Bearer private-test-token';
  await page.save();
  assert.deepEqual(JSON.parse(page.state.credential), { server_url: 'https://memory.example', authorization: 'private-test-token' });
  assert.equal(page.state.config.server_url, 'https://memory.example');
  assert.doesNotMatch(JSON.stringify(page.state.config), /private-test-token/);
  assert.equal(page.element('token').value, '');
});

test('changing the URL resets plaintext consent and requires a newly supplied credential', async () => {
  const page = await settings({ server_url: 'http://192.0.2.1', allow_insecure_http: true, auth_mode: 'bearer' }, 'saved');
  page.element('server-url').value = 'https://other.example';
  page.element('server-url').listeners.input();
  assert.equal(page.element('allow-insecure').checked, false);
  await page.save();
  assert.equal(page.state.config.server_url, 'http://192.0.2.1');
  assert.match(page.element('status').textContent, /Enter a Bearer token/);
});

test('a partial settings save cannot send a new credential to the previously configured Server', async () => {
  const page = await settings({ server_url: 'https://old.example', auth_mode: 'bearer' }, 'saved');
  page.state.failSave = true;
  page.element('server-url').value = 'https://new.example';
  page.element('token').value = 'private-test-token';
  await page.save();
  const result = await handleRequest({
    method: 'powercontext/authenticated',
    params: { tool: 'list_scopes', args: {}, config: page.state.config },
    cindy: { secrets: { server_credential: page.state.credential } },
  });
  assert.equal(result.code, 'credential_url_mismatch');
  assert.equal(page.element('token').value, '');
});

test('removing a credential does not silently downgrade authenticated requests', async () => {
  const page = await settings({ server_url: 'https://memory.example', auth_mode: 'bearer' }, 'saved');
  await page.element('clear-token').listeners.click();
  assert.equal(page.state.credential, undefined);
  assert.equal(page.state.config.auth_mode, 'bearer');
  const result = await handleRequest({
    method: 'powercontext/authenticated', params: { tool: 'list_scopes', config: page.state.config },
  });
  assert.equal(result.code, 'authentication_failed');
});
