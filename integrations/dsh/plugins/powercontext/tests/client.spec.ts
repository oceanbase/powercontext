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

import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { PLUGIN_USER_AGENT, PLUGIN_VERSION, ServerResponseError, UnavailableError, UnknownOperationError } from '../src/errors.ts'

function jsonResponse(status: number, body: unknown, headers?: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('PowerContextClient', () => {
  it('keeps the User-Agent version aligned with package.json', () => {
    const manifest = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'package.json'), 'utf8'))
    expect(PLUGIN_VERSION).toBe(manifest.version)
    expect(PLUGIN_USER_AGENT).toBe(`powercontext-dsh/${manifest.version}`)
  })

  it('POSTs JSON for remember_memory and sends Authorization', async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe('http://127.0.0.1:8000/v1/memory/remember')
      expect(init?.method).toBe('POST')
      expect(init?.redirect).toBe('manual')
      const headers = new Headers(init?.headers)
      expect(headers.get('Authorization')).toBe('Bearer token')
      expect(headers.get('User-Agent')).toBe('powercontext-dsh/0.0.2')
      expect(JSON.parse(String(init?.body))).toEqual({ scope_id: 'project:demo', kind: 'decision', text: 'keep API async' })
      return jsonResponse(200, { entry: { text: 'keep API async' } }, { 'X-PowerContext-Request-ID': 'req-1' })
    })
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000/',
      authorization: 'Bearer token',
      requestTimeoutMs: 1000,
      fetch: fetchImpl,
    })
    const result = await client.request('remember_memory', {
      scope_id: 'project:demo',
      kind: 'decision',
      text: 'keep API async',
    })
    expect(result).toMatchObject({ kind: 'json', status: 200, requestId: 'req-1' })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('sends get_stats as a POST selection', async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe('http://127.0.0.1:8000/v1/stats')
      expect(init?.method).toBe('POST')
      expect(JSON.parse(String(init?.body))).toEqual({
        selection: { mode: 'exact', scope_ids: ['project:demo'] },
        period: '7d',
      })
      return jsonResponse(200, { memories: 1 })
    })
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: fetchImpl,
    })
    await client.request('get_stats', {
      selection: { mode: 'exact', scope_ids: ['project:demo'] },
      period: '7d',
    })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('encodes scoped paths and separates path, query, header, and PUT body fields', async () => {
    let call = 0
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      call += 1
      const headers = new Headers(init?.headers)
      if (call === 1) {
        expect(url).toBe(
          'http://127.0.0.1:8000/v1/scopes/scope%2Fteam/artifacts/memory?limit=5',
        )
        expect(init?.method).toBe('GET')
        expect(init?.body).toBeUndefined()
        return jsonResponse(200, { items: [] })
      }
      if (call === 2) {
        expect(url).toBe(
          'http://127.0.0.1:8000/v1/scopes/scope%2Fteam/artifacts/memory/artifact%2F1',
        )
        expect(init?.method).toBe('PUT')
        expect(headers.get('If-Match')).toBe('"revision:1"')
        expect(JSON.parse(String(init?.body))).toEqual({ content: { title: 'kept' } })
        return jsonResponse(200, { revision: 2 })
      }
      if (call === 3) {
        expect(init?.method).toBe('GET')
        expect(headers.get('If-None-Match')).toBe('"revision:2"')
        expect(init?.body).toBeUndefined()
        return new Response(null, { status: 304 })
      }
      throw new Error('unexpected request')
    })
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000/',
      requestTimeoutMs: 1000,
      fetch: fetchImpl,
    })
    const path = {
      scope_id: 'scope/team',
      family: 'memory',
    }

    await client.request('list_artifacts', { ...path, limit: 5, ignored: 'value' })
    await client.request('replace_artifact', {
      ...path,
      artifact_id: 'artifact/1',
      if_match: '"revision:1"',
      content: { title: 'kept' },
    })
    await expect(client.request('get_artifact', {
      ...path,
      artifact_id: 'artifact/1',
      if_none_match: '"revision:2"',
    })).resolves.toMatchObject({ kind: 'json', value: null, status: 304 })
    expect(fetchImpl).toHaveBeenCalledTimes(3)
  })

  it('returns markdown text and raw bytes for get_handoff_report', async () => {
    const markdownClient = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response('# Report', { status: 200 }),
    })
    const selection = { mode: 'exact', scope_ids: ['scope-1'] }
    await expect(markdownClient.request('get_handoff_report', { selection, format: 'markdown' })).resolves.toMatchObject({
      kind: 'text',
      value: '# Report',
    })
    await expect(markdownClient.request('get_handoff_report', { selection })).resolves.toMatchObject({
      kind: 'text',
      value: '# Report',
    })
    const bytesClient = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 }),
    })
    const downloaded = await bytesClient.request('get_handoff_report', { selection, download: true })
    expect(downloaded.kind).toBe('bytes')
    if (downloaded.kind === 'bytes') expect([...downloaded.value]).toEqual([1, 2, 3])
  })

  it('maps non-2xx JSON errors and unknown ids', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => jsonResponse(409, { error: { code: 'conflict', message: 'citation mismatch' } }, { 'X-PowerContext-Request-ID': 'req-9' }),
    })
    await expect(client.request('revise_memory_entry', {})).rejects.toMatchObject({
      statusCode: 409,
      code: 'conflict',
      requestId: 'req-9',
    } satisfies Partial<ServerResponseError>)
    await expect(client.request('not_an_operation', {})).rejects.toBeInstanceOf(UnknownOperationError)
  })

  it('maps network failure to UnavailableError and rejects redirects', async () => {
    const down = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => {
        throw new TypeError('fetch failed')
      },
    })
    await expect(down.request('get_liveness')).rejects.toBeInstanceOf(UnavailableError)
    const redirected = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response(null, { status: 302, headers: { Location: 'https://evil.example' } }),
    })
    await expect(redirected.request('get_liveness')).rejects.toThrow()
  })

  it('emits the generated method and path for every operationId', async () => {
    const { OPERATION_IDS, OPERATIONS } = await import('../src/operations.generated.ts')
    const seen: Array<{ method: string; url: string; hasBody: boolean }> = []
    let operationIndex = 0
    const client = new PowerContextClient({
      baseUrl: 'http://example.test',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        const spec = OPERATIONS[OPERATION_IDS[operationIndex++]!]
        seen.push({ method: String(init?.method), url, hasBody: Boolean(init?.body) })
        const status = spec.successStatuses[0] ?? 200
        return (spec.emptyStatuses as readonly number[]).includes(status)
          ? new Response(null, { status })
          : jsonResponse(status, { ok: true })
      },
    })
    for (const id of OPERATION_IDS) {
      const spec = OPERATIONS[id]
      const payload: Record<string, unknown> = { marker: id }
      for (const name of spec.pathParameters) payload[name] = `value/${name}`
      for (const name of spec.queryParams) payload[name] = `value-${name}`
      for (const name of spec.headerParams) payload[name] = `value-${name}`
      await client.request(id, payload)
    }
    expect(seen).toHaveLength(OPERATION_IDS.length)
    OPERATION_IDS.forEach((id, index) => {
      const spec = OPERATIONS[id]
      let expectedPath = spec.path as string
      for (const name of spec.pathParameters) {
        expectedPath = expectedPath.replace(`{${name}}`, encodeURIComponent(`value/${name}`))
      }
      expect(seen[index].method).toBe(spec.method)
      expect(seen[index].url.startsWith(`http://example.test${expectedPath}`)).toBe(true)
      expect(seen[index].url).not.toContain('{')
      expect(seen[index].hasBody).toBe(spec.location === 'body')
    })
  })
})
