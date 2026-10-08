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
import { ServerResponseError } from '../src/errors.ts'
import { toToolResult } from '../src/invoke.ts'

/**
 * The HTTP statuses `src/powercontext/server/app.py` maps a domain error to.
 * Every 4xx carries a specific meaning, so each needs a branch; the tail message
 * ("PowerContext is unavailable, continue the task.") is only accurate for an
 * availability outcome, which is why 5xx deliberately falls through.
 */
const BRANCHED_STATUSES = [400, 401, 403, 404, 409, 410, 412, 413, 422, 428, 429]
const DOMAIN_STATUSES = [400, 404, 409, 410, 412, 413, 422, 428, 429]
const AVAILABILITY_STATUSES = [500, 503]

const OUTAGE_MESSAGE = 'PowerContext is unavailable, continue the task.'

function serverError(statusCode: number, code?: string): ServerResponseError {
  return new ServerResponseError({ statusCode, path: '/v1/memory/entries/get', code })
}

describe('error mapping covers every status the server returns', () => {
  it.each(BRANCHED_STATUSES)('status %s has its own branch, not the generic tail', status => {
    const result = toToolResult(serverError(status))
    expect(result.status).toBe(status)
    expect(result.code).not.toBe('server_error')
  })

  it.each(DOMAIN_STATUSES)('status %s is not reported as an outage', status => {
    expect(toToolResult(serverError(status)).message).not.toContain('unavailable')
  })

  it('keeps the outage wording only for availability statuses', () => {
    expect(toToolResult(serverError(503))).toMatchObject({ code: 'unavailable', message: OUTAGE_MESSAGE })
    expect(toToolResult(serverError(500))).toMatchObject({ code: 'server_error', message: OUTAGE_MESSAGE })
  })
})

describe('412 precondition failures', () => {
  it('names the tag precondition instead of degrading to a generic code', () => {
    expect(toToolResult(serverError(412, 'tag_precondition_failed'))).toMatchObject({
      ok: false,
      code: 'tag_precondition_failed',
      status: 412,
      message: 'PowerContext rejected the request because a precondition no longer matches the current state. Re-read the current revision or tag, then retry with the fresh value.',
    })
  })

  it('keeps the code of the second 412 the server returns', () => {
    expect(toToolResult(serverError(412, 'revision_conflict'))).toMatchObject({
      code: 'revision_conflict',
      status: 412,
    })
  })

  it('still names a precondition failure when the response carries no code', () => {
    expect(toToolResult(serverError(412))).toMatchObject({
      code: 'precondition_failed',
      status: 412,
    })
  })
})

describe('409 capacity failures', () => {
  it('keeps the capacity code instead of degrading to a generic conflict', () => {
    expect(toToolResult(serverError(409, 'memory_capacity_exceeded'))).toMatchObject({
      code: 'memory_capacity_exceeded',
      status: 409,
    })
  })

  it('carries the details payload from the response body into the tool result', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response(JSON.stringify({
        error: {
          code: 'memory_capacity_exceeded',
          message: 'The Memory has reached its capacity budget.',
          details: { dimension: 'entries', limit: 100, observed: 101 },
        },
      }), { status: 409, headers: { 'Content-Type': 'application/json' } }),
    })
    const error = await client.request('revise_memory_entry', {}).catch(error => error)
    expect(toToolResult(error)).toMatchObject({
      ok: false,
      code: 'memory_capacity_exceeded',
      status: 409,
      details: { dimension: 'entries', limit: 100, observed: 101 },
    })
  })
})
