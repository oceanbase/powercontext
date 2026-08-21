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
import { PREPARED_CONTEXT_SCHEMA, validatePreparedContext } from '../src/prepared-context.ts'

describe('prepared context validation', () => {
  it('accepts only the complete bounded v1 response', () => {
    const ready = {
      schema: PREPARED_CONTEXT_SCHEMA,
      status: 'ready',
      content: 'Prior decision',
      content_bytes: Buffer.byteLength('Prior decision', 'utf8'),
    }
    expect(validatePreparedContext(ready, 8000)).toEqual(ready)
    expect(() => validatePreparedContext({ ...ready, extra: true }, 8000)).toThrow('violated the API schema')
    expect(() => validatePreparedContext({ ...ready, content_bytes: 1 }, 8000)).toThrow('violated the API schema')
  })
})
