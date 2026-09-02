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
import { failureEvent } from './diagnostics.js'
import { PowerContextRequestError } from './http.js'

describe('host-visible diagnostic classification', () => {
  it('uses version_mismatch only for compatibility or availability endpoints', () => {
    expect(failureEvent('context_prepare', new PowerContextRequestError(
      '/v1/context/prepare',
      'missing endpoint',
      404,
    ))).toEqual({ event: 'context_prepare', outcome: 'version_mismatch', http_status: 404 })

    expect(failureEvent('capture_source', new PowerContextRequestError(
      '/v1/memory/entries/get',
      'missing entry',
      404,
      'memory_not_found',
    ))).toBeUndefined()
  })

  it('keeps automatic domain failures visible at their actual endpoints', () => {
    const automaticOperations = [
      ['context_prepare', '/v1/context/prepare'],
      ['capture_source', '/v1/sources/content'],
      ['session_end_flush', '/v1/memory/flush'],
    ] as const
    const failures = [
      [404, 'not_found'],
      [409, 'conflict'],
      [422, 'invalid_request'],
    ] as const

    for (const [event, path] of automaticOperations) {
      for (const [status, code] of failures) {
        expect(failureEvent(event, new PowerContextRequestError(
          path,
          'domain error',
          status,
          code,
        ))).toEqual({
          event,
          outcome: 'invalid_response',
          http_status: status,
          error_code: code,
        })
      }
    }
  })

  it('does not emit availability diagnostics for direct domain errors', () => {
    for (const status of [404, 409, 422]) {
      expect(failureEvent('tool_call', new PowerContextRequestError(
        '/v1/memory/entries/get',
        'domain error',
        status,
        status === 404 ? 'memory_not_found' : status === 409 ? 'conflict' : 'invalid_request',
      ))).toBeUndefined()
    }
  })

  it('does not treat a coded compatibility response as a version mismatch', () => {
    expect(failureEvent('context_prepare', new PowerContextRequestError(
      '/v1/context/prepare',
      'invalid request',
      404,
      'invalid_request',
    ))).toEqual({
      event: 'context_prepare',
      outcome: 'invalid_response',
      http_status: 404,
      error_code: 'invalid_request',
    })
  })
})
