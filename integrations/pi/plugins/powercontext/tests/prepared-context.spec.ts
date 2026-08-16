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
