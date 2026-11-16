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

import { describe, expect, it } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { invokeOperation } from '../src/invoke.ts'

// A legacy response may carry user content in message/details. Only the known code
// and locally owned recovery instructions should be exposed by this mapping.
describe('legacy Memory rejection', () => {
  it('preserves the code and gives recovery without leaking response content', async () => {
    const client = new PowerContextClient({ baseUrl: 'http://127.0.0.1:8000', requestTimeoutMs: 1000,
      fetch: async () => Response.json({ error: { code: 'legacy_memory_operation_unsupported',
        message: 'private-response-marker' } }, { status: 422, headers: { 'x-powercontext-request-id': 'req-compat' } }),
    })
    const result = await invokeOperation(client, 'search_memory', { query: 'test' }, 'scope:test')
    expect(result).toMatchObject({
      ok: false, status: 422, code: 'legacy_memory_operation_unsupported', request_id: 'req-compat',
      recovery: expect.stringContaining('powercontext setup opencode'),
    })
    expect(result.recovery).toContain('Do not drop collection CAS')
    expect(JSON.stringify(result)).not.toContain('private-response-marker')
  })
})
