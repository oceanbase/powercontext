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
import { containsSecret, scrubSecrets } from '../src/secrets.ts'

describe('secret detection', () => {
  it('recognizes assignments and token-shaped credentials', () => {
    expect(containsSecret('password = hunter2')).toBe(true)
    expect(containsSecret('sk-live-secret')).toBe(true)
    expect(containsSecret('-----BEGIN PRIVATE KEY-----')).toBe(true)
    expect(scrubSecrets('password = hunter2')).toBe('[REDACTED]')
  })

  it('does not reject ordinary text containing marker-like substrings', () => {
    expect(containsSecret('risk-based prioritization')).toBe(false)
    expect(scrubSecrets('risk-based prioritization')).toBe('risk-based prioritization')
  })
})

describe('Pi native tool invocation', () => {
  it('uses the resolved scope and refuses secret-bearing writes', async () => {
    let body: string | undefined
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        body = String(init.body)
        return new Response(JSON.stringify({ items: [] }), { status: 200 })
      },
    })

    await expect(invokeOperation(client, 'search_memory', {
      query: 'prior decision',
      scope_id: 'scope:untrusted',
    }, 'scp_resolved')).resolves.toMatchObject({ ok: true })
    expect(JSON.parse(body ?? '{}')).toMatchObject({ scope_id: 'scp_resolved' })

    await expect(invokeOperation(client, 'remember_memory', {
      kind: 'agent-note',
      text: 'api_key=secret',
    }, 'scp_resolved')).resolves.toMatchObject({
      ok: false,
      code: 'secret_rejected',
    })
  })

  it('limits observation requests to the derived Scope', async () => {
    const bodies: unknown[] = []
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        bodies.push(JSON.parse(String(init?.body)))
        return new Response(JSON.stringify({}), { status: 200 })
      },
    })

    await invokeOperation(client, 'get_stats', { selection: { mode: 'all' } }, 'scp_resolved')
    await invokeOperation(client, 'get_handoff_report', {
      selection: { mode: 'subtree', root_scope_id: 'scope:other' },
      format: 'json',
    }, 'scp_resolved')

    expect(bodies).toEqual([
      { selection: { mode: 'exact', scope_ids: ['scp_resolved'] } },
      { selection: { mode: 'exact', scope_ids: ['scp_resolved'] }, format: 'json' },
    ])
  })
})
