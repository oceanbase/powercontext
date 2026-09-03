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
import { failureEvent } from '../src/diagnostics.ts'
import { ServerResponseError } from '../src/errors.ts'

describe('host-visible diagnostic classification', () => {
  it('uses version_mismatch only for compatibility or availability endpoints', () => {
    expect(failureEvent('context_prepare', new ServerResponseError({
      statusCode: 404,
      path: '/v1/context/prepare',
    }))).toEqual({ event: 'context_prepare', outcome: 'version_mismatch', http_status: 404 })

    expect(failureEvent('capture_content_source', new ServerResponseError({
      statusCode: 404,
      path: '/v1/memory/entries/get',
    }))).toBeUndefined()
  })

  it('keeps automatic domain failures visible at their actual endpoints', () => {
    const automaticOperations = [
      ['context_prepare', '/v1/context/prepare'],
      ['capture_content_source', '/v1/sources/content'],
      ['flush_memory', '/v1/memory/flush'],
    ] as const
    const failures = [
      [404, 'not_found'],
      [409, 'conflict'],
      [422, 'invalid_request'],
    ] as const

    for (const [event, path] of automaticOperations) {
      for (const [statusCode, code] of failures) {
        expect(failureEvent(event, new ServerResponseError({
          statusCode,
          path,
          code,
        }))).toEqual({
          event,
          outcome: 'invalid_response',
          http_status: statusCode,
          error_code: code,
        })
      }
    }
  })

  it('does not emit availability diagnostics for direct domain errors', () => {
    for (const statusCode of [404, 409, 422]) {
      expect(failureEvent('tool_call', new ServerResponseError({
        statusCode,
        path: '/v1/memory/entries/get',
        code: statusCode === 404 ? 'memory_not_found' : statusCode === 409 ? 'conflict' : 'invalid_request',
      }))).toBeUndefined()
    }
  })

  it('does not treat a coded compatibility response as a version mismatch', () => {
    expect(failureEvent('context_prepare', new ServerResponseError({
      statusCode: 404,
      path: '/v1/context/prepare',
      code: 'invalid_request',
    }))).toEqual({
      event: 'context_prepare',
      outcome: 'invalid_response',
      http_status: 404,
      error_code: 'invalid_request',
    })
  })
})
