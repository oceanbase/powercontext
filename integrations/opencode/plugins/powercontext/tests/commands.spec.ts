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

import { describe, expect, it, vi } from 'vitest'

import { PowerContextClient } from '../src/client.ts'
import { handlePcCommand, type PcCommandRuntime } from '../src/commands.ts'
import { resolveConfig } from '../src/config.ts'

function runtime(fetchImpl: typeof fetch = async () => Response.json({ ok: true })): PcCommandRuntime {
  const config = resolveConfig({ POWERCONTEXT_OPENCODE_SCOPE_ID: 'project:test' })
  return {
    config,
    client: new PowerContextClient({
      baseUrl: config.baseUrl,
      requestTimeoutMs: config.requestTimeoutMs,
      fetch: fetchImpl,
    }),
  }
}

describe('handlePcCommand', () => {
  it('shows the current scope and Server URL for bare /pc', async () => {
    const result = await handlePcCommand('', runtime(), 'project:test')

    expect(result).toEqual({
      kind: 'success',
      text: expect.stringContaining('scope=project:test'),
    })
  })

  it('runs model-free Server diagnostics', async () => {
    const fetchImpl = vi.fn(async (url: string | URL | Request) => Response.json(new URL(String(url)).pathname === '/v1/server-info'
      ? { feature_contracts: { 'memory.explicit': { version: { major: 1, minor: 0 } } } } : { status: 'ok' }))
    const result = await handlePcCommand('doctor', runtime(fetchImpl), 'project:test')

    expect(result.kind).toBe('success')
    expect(fetchImpl.mock.calls.map(([url]) => new URL(String(url)).pathname)).toContain('/v1/server-info')
    expect(result.text).toContain('"live"')
    expect(result.text).toContain('"ready"')
  })

  it('keeps DSH review command argument validation', async () => {
    const result = await handlePcCommand('review approve only-id', runtime(), 'project:test')

    expect(result).toEqual({
      kind: 'error',
      text: 'Usage: /pc review approve <candidate_id> <expected_version>',
    })
  })
})


it.each([undefined, { version: { major: 2, minor: 0 } }])('OpenCode Doctor rejects incompatible Memory declarations %j', async memory => {
  const fetchImpl = async (url: string | URL | Request) => Response.json(new URL(String(url)).pathname === '/v1/server-info'
    ? { feature_contracts: { 'memory.explicit': memory } } : { status: 'ok' })
  const result = await handlePcCommand('doctor', runtime(fetchImpl), 'project:test')
  expect(result.kind).toBe('error')
  expect(JSON.parse(result.text)).toMatchObject({ ok: false, data: { memory: {
    code: 'memory_protocol_mismatch', recovery: expect.stringContaining('powercontext setup opencode'),
  } } })
})
