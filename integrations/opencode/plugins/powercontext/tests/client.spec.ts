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
import { MAX_RESPONSE_BYTES } from '../src/errors.ts'

function clientFor(response: Response): PowerContextClient {
  return new PowerContextClient({
    baseUrl: 'http://127.0.0.1:8000',
    requestTimeoutMs: 1000,
    fetch: async () => response,
  })
}

describe('PowerContextClient response limits', () => {
  it('preserves repeated tag filters and the opaque ETag for conditional tag writes', async () => {
    const requests: Array<{ url: string; init: RequestInit }> = []
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        requests.push({ url, init })
        return new Response(JSON.stringify({ tags: ['Release'] }), {
          status: 200, headers: { ETag: '"opaque-tag-token"' },
        })
      },
    })
    const target = { scope_id: 'project', family: 'memory', artifact_id: 'memory' }
    await client.request('list_artifacts', { ...target, tag: ['Release', '客户A'], tag_match: 'all' })
    const listed = new URL(requests[0]!.url)
    expect(listed.searchParams.getAll('tag')).toEqual(['Release', '客户A'])
    expect(listed.searchParams.get('tag_match')).toBe('all')
    const current = await client.request('get_artifact_tags', target)
    expect(current.etag).toBe('"opaque-tag-token"')
    await client.request('replace_artifact_tags', { ...target, tags: [], if_match: current.etag })
    expect(new Headers(requests[2]!.init.headers).get('If-Match')).toBe(current.etag)
    expect(JSON.parse(String(requests[2]!.init.body))).toEqual({ tags: [] })
  })

  it('forwards authorization and preserves Access denial details', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      authorization: 'Bearer integration-token',
      requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        expect(new Headers(init.headers).get('Authorization')).toBe('Bearer integration-token')
        return new Response(
          JSON.stringify({ error: { code: 'access_denied', message: 'scope access denied' } }),
          { status: 403, headers: { 'X-PowerContext-Request-ID': 'request-access-1' } },
        )
      },
    })

    await expect(client.request('get_scope', { scope_id: 'scope:feature' })).rejects.toMatchObject({
      statusCode: 403,
      code: 'access_denied',
      serverMessage: 'scope access denied',
      requestId: 'request-access-1',
    })
  })

  it('binds scope resource paths and omits path values from the request body', async () => {
    const requests: Array<{ url: string; init: RequestInit }> = []
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        requests.push({ url, init })
        return new Response(JSON.stringify({ scope_id: 'scope:feature' }), { status: 200 })
      },
    })

    await client.request('get_scope', { scope_id: 'scope:feature' })
    await client.request('update_scope', {
      scope_id: 'scope:feature',
      expected_version: 1,
      title: 'Feature',
      summary: 'Current work',
    })

    expect(requests.map((request) => request.url)).toEqual([
      'http://127.0.0.1:8000/v1/scopes/scope%3Afeature',
      'http://127.0.0.1:8000/v1/scopes/scope%3Afeature',
    ])
    expect(requests[0]!.init.body).toBeUndefined()
    expect(JSON.parse(String(requests[1]!.init.body))).not.toHaveProperty('scope_id')
  })

  it('cancels a chunked response before it can exceed 1 MiB', async () => {
    let pulls = 0
    let cancelled = false
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        pulls += 1
        if (pulls <= 8) controller.enqueue(new Uint8Array(256 * 1024))
        else controller.close()
      },
      cancel() {
        cancelled = true
      },
    })

    await expect(clientFor(new Response(body)).request('get_liveness')).rejects.toThrow(
      'violated the API schema',
    )
    expect(cancelled).toBe(true)
    expect(pulls).toBeLessThanOrEqual(5)
  })

  it('rejects and cancels a declared response larger than 1 MiB', async () => {
    let cancelled = false
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(new Uint8Array([123]))
      },
      cancel() {
        cancelled = true
      },
    })
    const response = new Response(body, {
      headers: { 'Content-Length': String(MAX_RESPONSE_BYTES + 1) },
    })

    await expect(clientFor(response).request('get_liveness')).rejects.toThrow('violated the API schema')
    expect(cancelled).toBe(true)
  })

  it('accepts a valid JSON response exactly at the 1 MiB boundary', async () => {
    const payload = JSON.stringify('x'.repeat(MAX_RESPONSE_BYTES - 2))
    const response = new Response(payload, {
      headers: { 'Content-Length': String(MAX_RESPONSE_BYTES) },
    })

    const result = await clientFor(response).request('get_liveness')

    expect(result.value).toBe('x'.repeat(MAX_RESPONSE_BYTES - 2))
  })
})

describe('PowerContextClient generated operation requests', () => {
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
        return new Response(JSON.stringify({ items: [] }), { status: 200 })
      }
      if (call === 2) {
        expect(url).toBe(
          'http://127.0.0.1:8000/v1/scopes/scope%2Fteam/artifacts/memory/artifact%2F1',
        )
        expect(init?.method).toBe('PUT')
        expect(headers.get('If-Match')).toBe('"revision:1"')
        expect(JSON.parse(String(init?.body))).toEqual({ content: { title: 'kept' } })
        return new Response(JSON.stringify({ revision: 2 }), { status: 200 })
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
    const path = { scope_id: 'scope/team', family: 'memory' }

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

  it('rejects an undeclared redirect response', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response(null, { status: 302, headers: { Location: 'https://example.invalid' } }),
    })

    await expect(client.request('get_liveness')).rejects.toThrow('violated the API schema')
  })
})
