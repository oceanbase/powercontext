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

import assert from 'node:assert/strict'
import test from 'node:test'
import { serverOrigin } from '../shared/transport.mjs'

const origin = (serverUrl, allowInsecureHttp = false) => serverOrigin({ serverUrl, allowInsecureHttp })

test('plaintext HTTP is accepted for localhost, ::1 and the whole 127/8 range, as in the shared transport', () => {
  for (const [url, expected] of [
    ['http://127.0.0.1:8000/', 'http://127.0.0.1:8000'],
    ['http://127.0.0.2:18765/', 'http://127.0.0.2:18765'],
    ['http://127.255.255.254/', 'http://127.255.255.254'],
    ['http://127.1:8000/', 'http://127.0.0.1:8000'],
    ['http://127.0.0.2.:8000/', 'http://127.0.0.2:8000'],
    ['http://localhost:8000/', 'http://localhost:8000'],
    ['http://LOCALHOST:8000/', 'http://localhost:8000'],
    ['http://[::1]:8000/', 'http://[::1]:8000'],
    ['http://[0:0:0:0:0:0:0:1]:8000/', 'http://[::1]:8000'],
  ]) assert.equal(origin(url), expected, url)
})

test('plaintext HTTP elsewhere still requires allowInsecureHttp', () => {
  for (const url of [
    'http://10.0.0.1/',
    'http://128.0.0.1/',
    'http://127.example.com/',
    'http://127.0.0.1.example.com/',
    'http://localhost./',
    'http://localhost.example.com/',
    'http://[::ffff:127.0.0.1]/',
    'http://[::2]/',
  ]) {
    assert.throws(() => origin(url), { message: 'invalid_server_url' }, url)
    assert.equal(origin(url, true), new URL(url).origin, url)
  }
  assert.equal(origin('https://10.0.0.1/'), 'https://10.0.0.1')
})

test('loopback does not relax the other Server URL requirements', () => {
  for (const url of [
    'http://user:secret@127.0.0.2/',
    'http://127.0.0.2/?q=1',
    'http://127.0.0.2/#fragment',
    'http://127.0.0.2/mcp',
    'ftp://127.0.0.2/',
    'not a url',
  ]) assert.throws(() => origin(url), { message: 'invalid_server_url' }, url)
})
