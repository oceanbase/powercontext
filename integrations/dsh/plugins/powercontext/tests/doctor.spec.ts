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
import { handlePcCommand } from '../src/commands.ts'
import { resolveConfig } from '../src/config.ts'

describe('native doctor command', () => {
  it.each([true, false])('displays the Python result with ok=%s without Scope probes', async ok => {
    const config = resolveConfig({}, {})
    const client = new PowerContextClient(config)
    const report = { ok, status: ok ? 'ok' : 'failed', checks: { readiness: { status: ok ? 'ok' : 'failed' } } }
    vi.spyOn(client, 'doctor').mockResolvedValue(report)
    const runtime = { config, client, log: vi.fn(), resolveScope: async () => { throw new Error('unavailable') } }
    const result = await handlePcCommand('doctor', runtime, '/fixture/workspace')
    expect(result).toEqual({ kind: ok ? 'success' : 'error', text: JSON.stringify(report, null, 2) })
  })
})
