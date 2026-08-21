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

describe('PowerContext Pi HTTP client', () => {
  it('posts JSON with authorization and rejects redirects', async () => {
    const fetch = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe('http://127.0.0.1:8000/v1/context/prepare')
      expect(init?.method).toBe('POST')
      expect(init?.redirect).toBe('manual')
      expect(new Headers(init?.headers).get('Authorization')).toBe('Bearer token')
      expect(JSON.parse(String(init?.body))).toEqual({
        scope_id: 'project:demo',
        query: 'continue implementation',
        max_bytes: 8000,
      })
      return new Response(JSON.stringify({ status: 'ready' }), { status: 200 })
    })
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000/',
      authorization: 'Bearer token',
      requestTimeoutMs: 1000,
      fetch,
    })

    await expect(client.request('prepare_context', {
      scope_id: 'project:demo',
      query: 'continue implementation',
      max_bytes: 8000,
    })).resolves.toMatchObject({ kind: 'json', value: { status: 'ready' } })

    const redirected = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response(null, { status: 302, headers: { Location: 'https://example.invalid' } }),
    })
    await expect(redirected.request('get_liveness')).rejects.toThrow('violated the API schema')
  })

  it('fails requests closed when the request timeout expires', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 10,
      fetch: async (_url, init) => new Promise<Response>((_resolve, reject) => {
        init.signal?.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError')), { once: true })
      }),
    })

    await expect(client.request('get_liveness')).rejects.toThrow('request to /health/live failed')
  })
})
