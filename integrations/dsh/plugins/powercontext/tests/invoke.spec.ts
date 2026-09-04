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
import { invokeOperation } from '../src/invoke.ts'
import { PowerContextClient } from '../src/client.ts'
import { containsSecret } from '../src/secrets.ts'
import { PROJECT_CONTEXT_SKILL } from '../src/skill-body.ts'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

describe('secrets', () => {
  it('rejects token-like markers', () => {
    expect(containsSecret('sk-live-secret')).toBe(true)
    expect(containsSecret('api_key=foo')).toBe(true)
    expect(containsSecret('-----BEGIN PRIVATE KEY-----')).toBe(true)
    expect(containsSecret('keep the public API async')).toBe(false)
  })
})

describe('invokeOperation', () => {
  it('injects scope_id for scoped operations and skips health', async () => {
    const seen: Array<{ id: string; url: string; body: string | undefined }> = []
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        seen.push({ id: url, url, body: init?.body ? String(init.body) : undefined })
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      },
    })
    await invokeOperation(client, 'search_memory', { query: 'api' }, 'project:demo')
    await invokeOperation(client, 'get_liveness', {}, 'project:demo')
    expect(JSON.parse(seen[0].body ?? '{}')).toMatchObject({ query: 'api', scope_id: 'project:demo' })
    expect(seen[1].body).toBeUndefined()
    expect(seen[1].url).toBe('http://127.0.0.1:8000/health/live')
  })

  it('overwrites a caller-supplied scope_id with the resolved workspace scope', async () => {
    let body: string | undefined
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        body = init?.body ? String(init.body) : undefined
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      },
    })

    await invokeOperation(client, 'search_memory', {
      query: 'api',
      scope_id: 'project:attacker-controlled',
    }, 'scp_workspace_binding')

    expect(JSON.parse(body ?? '{}')).toMatchObject({
      query: 'api',
      scope_id: 'scp_workspace_binding',
    })
  })

  it('limits observation selections to the resolved workspace scope', async () => {
    const bodies: unknown[] = []
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        bodies.push(JSON.parse(String(init?.body)))
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      },
    })

    await invokeOperation(client, 'get_stats', { selection: { mode: 'all' } }, 'scp_workspace_binding')
    await invokeOperation(client, 'get_handoff_report', {
      selection: { mode: 'subtree', root_scope_id: 'scope:other' },
      format: 'json',
    }, 'scp_workspace_binding')

    expect(bodies).toEqual([
      { selection: { mode: 'exact', scope_ids: ['scp_workspace_binding'] } },
      {
        selection: { mode: 'exact', scope_ids: ['scp_workspace_binding'] },
        format: 'json',
      },
    ])
  })

  it('preserves explicit Scope control requests', async () => {
    let body: string | undefined
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        body = init?.body ? String(init.body) : undefined
        return new Response(JSON.stringify({ scope_id: 'scp_target' }), { status: 200 })
      },
    })

    await invokeOperation(client, 'set_scope_binding', {
      key: { integration: 'dsh', kind: 'session', external_id: 'session-1' },
      scope_id: 'scp_target',
    }, 'scp_current')

    expect(JSON.parse(body ?? '{}').scope_id).toBe('scp_target')
  })

  it('returns unavailable instead of throwing when the server is down', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => {
        throw new TypeError('fetch failed')
      },
    })
    await expect(invokeOperation(client, 'search_memory', { query: 'api' }, 'project:demo')).resolves.toMatchObject({
      ok: false,
      code: 'unavailable',
      message: 'PowerContext is unavailable, continue the task.',
    })
  })

  it('refuses secret-like remember payloads', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response('{}', { status: 200 }),
    })
    await expect(invokeOperation(client, 'remember_memory', { kind: 'decision', text: 'sk-secret' }, 'project:demo')).resolves.toMatchObject({
      ok: false,
      code: 'secret_rejected',
    })
  })
})

describe('skill body', () => {
  it('stays aligned with the markdown source', () => {
    const markdown = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'skill-body.md'), 'utf8')
    expect(PROJECT_CONTEXT_SKILL.replaceAll('\r\n', '\n').trim()).toBe(markdown.replaceAll('\r\n', '\n').trim())
  })
})
