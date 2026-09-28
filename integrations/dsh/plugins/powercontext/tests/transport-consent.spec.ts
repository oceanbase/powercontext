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

import { mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { resolveConfig } from '../src/config.ts'
import { PowerContextClient } from '../src/client.ts'

const remote = 'http://memory.example.test:8000'
const hostUrl = 'POWERCONTEXT_DSH_BASE_URL'
const hostFlag = 'POWERCONTEXT_DSH_ALLOW_INSECURE_HTTP'
let directory: string
let configFile: string

function resolve(overrides: NodeJS.ProcessEnv = {}, native: Record<string, unknown> = {}) {
  const env = { POWERCONTEXT_CLIENT_CONFIG_FILE: configFile, ...overrides }
  return resolveConfig(native, env)
}

function save(serverUrl = remote, consent: unknown = true) {
  writeFileSync(configFile, JSON.stringify({
    version: 1,
    hosts: { dsh: { server_url: serverUrl, allow_insecure_http: consent } },
  }))
}

beforeEach(() => {
  directory = mkdtempSync(join(tmpdir(), 'powercontext-dsh-transport-'))
  configFile = join(directory, 'clients.json')
})
afterEach(() => rmSync(directory, { recursive: true, force: true }))

describe('dsh transport consent', () => {
  it('rejects non-boolean consent from direct JavaScript callers', () => {
    expect(() => new PowerContextClient({
      baseUrl: remote, requestTimeoutMs: 1000,
      allowInsecureHttp: 'false' as unknown as boolean,
    })).toThrow(/boolean/)
  })

  it('enforces transport policy when constructing a client directly', async () => {
    const options = {
      baseUrl: remote, requestTimeoutMs: 1000,
      fetch: async (_url: string, init: RequestInit) => {
        expect(init.redirect).toBe('manual')
        return new Response('{}', { headers: { 'content-type': 'application/json' } })
      },
    }
    expect(() => new PowerContextClient(options)).toThrow(/HTTPS/)
    const client = new PowerContextClient({ ...options, allowInsecureHttp: true })
    await expect(client.request('get_liveness')).resolves.toMatchObject({ status: 200 })
  })

  it('rejects remote cleartext without consent', () => {
    expect(() => resolve({ [hostUrl]: remote })).toThrow(/HTTPS/)
  })

  it.each(['true', '1', 'yes', 'on', ' TRUE '])('accepts explicit consent %s', (value) => {
    expect(resolve({ [hostUrl]: remote, [hostFlag]: value }).baseUrl).toBe(remote)
  })

  it('accepts common consent and a common server URL', () => {
    const result = resolve({
      POWERCONTEXT_CLIENT_SERVER_URL: remote,
      POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP: 'yes',
    })
    expect(result.baseUrl).toBe(remote)
    expect(result.allowInsecureHttp).toBe(true)
  })

  it.each(['false', '0', 'no', 'off'])('lets host %s override common consent', (value) => {
    expect(() => resolve({
      [hostUrl]: remote,
      [hostFlag]: value,
      POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP: 'true',
    })).toThrow(/HTTPS/)
  })

  it.each([hostFlag, 'POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP'])('rejects invalid %s', (name) => {
    expect(() => resolve({ [name]: 'sometimes' })).toThrow(/boolean/)
  })

  it('loads setup-saved endpoint and consent when native configuration is absent', () => {
    save()
    expect(resolve().baseUrl).toBe(remote)
    expect(resolve().allowInsecureHttp).toBe(true)
  })

  it('normalizes saved MCP URLs before matching consent', () => {
    save(remote + '/mcp/')
    expect(resolve({ [hostUrl]: remote + '/' }).baseUrl).toBe(remote)
  })

  it('does not reuse saved consent for a different endpoint', () => {
    save()
    expect(() => resolve({ [hostUrl]: 'http://other.test' })).toThrow(/HTTPS/)
  })

  it('lets explicit common false revoke saved consent', () => {
    save()
    expect(() => resolve({ POWERCONTEXT_CLIENT_ALLOW_INSECURE_HTTP: 'false' })).toThrow(/HTTPS/)
  })

  it('rejects non-boolean saved consent', () => {
    save(remote, 'true')
    expect(() => resolve()).toThrow(/boolean/)
  })

  it.each(['localhost', '127.0.0.2', '[::1]'])('allows loopback HTTP at %s', (host) => {
    expect(resolve({ [hostUrl]: 'http://' + host + ':8000' }).baseUrl).toBe('http://' + host + ':8000')
  })

  it.each(['ftp://memory.test', 'https://user:password@memory.test', 'https://memory.test/?secret=value', 'https://memory.test/#fragment'])(
    'keeps URL restrictions when opted in: %s', (url) => {
      expect(() => resolve({ [hostUrl]: url, [hostFlag]: 'true' })).toThrow()
    },
  )

  it('honors explicit native consent and rejects a non-boolean native value', () => {
    expect(resolve({}, { baseUrl: remote, allowInsecureHttp: true }).baseUrl).toBe(remote)
    expect(() => resolve({}, { baseUrl: remote, allowInsecureHttp: 'yes' })).toThrow(/boolean/)
  })

  it('does not transfer native consent when an environment URL changes the endpoint', () => {
    expect(() => resolve({ [hostUrl]: 'http://other.test' }, {
      baseUrl: remote, allowInsecureHttp: true,
    })).toThrow(/HTTPS/)
  })

  it('matches native consent against normalized endpoints', () => {
    expect(resolve({ [hostUrl]: remote + '/' }, {
      baseUrl: remote + '/mcp/', allowInsecureHttp: true,
    }).baseUrl).toBe(remote)
  })

  it('requires an endpoint alongside native consent', () => {
    expect(() => resolve({ [hostUrl]: remote }, { allowInsecureHttp: true })).toThrow(/HTTPS/)
  })

  it('permits a changed endpoint when the environment explicitly authorizes it', () => {
    expect(resolve({ [hostUrl]: 'http://other.test', [hostFlag]: 'true' }, {
      baseUrl: remote, allowInsecureHttp: true,
    }).baseUrl).toBe('http://other.test')
  })

  it('lets an environment false override native consent', () => {
    expect(() => resolve({ [hostFlag]: 'false' }, {
      baseUrl: remote, allowInsecureHttp: true,
    })).toThrow(/HTTPS/)
  })

  it('does not let native false or a changed native endpoint inherit saved consent', () => {
    save()
    expect(() => resolve({}, { allowInsecureHttp: false })).toThrow(/HTTPS/)
    expect(() => resolve({}, { baseUrl: 'http://other.test' })).toThrow(/HTTPS/)
  })
})
