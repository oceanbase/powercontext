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
import { MAX_RESPONSE_BYTES } from '../src/errors.ts'

function clientFor(response: Response): PowerContextClient {
  return new PowerContextClient({
    baseUrl: 'http://127.0.0.1:8000',
    requestTimeoutMs: 1000,
    fetch: async () => response,
  })
}

describe('PowerContextClient response limits', () => {
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
