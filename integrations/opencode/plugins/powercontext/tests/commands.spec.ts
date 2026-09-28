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
    const fetchImpl = vi.fn(async () => Response.json({ status: 'ok' }))
    const result = await handlePcCommand('doctor', runtime(fetchImpl), 'project:test')

    expect(result.kind).toBe('success')
    expect(fetchImpl).toHaveBeenCalledTimes(2)
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
