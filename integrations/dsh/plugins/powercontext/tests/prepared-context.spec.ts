import { describe, expect, it } from 'vitest'
import { InvalidResponseError } from '../src/errors.ts'
import { PREPARED_CONTEXT_SCHEMA, validatePreparedContext } from '../src/prepared-context.ts'

const ready = {
  schema: PREPARED_CONTEXT_SCHEMA,
  status: 'ready',
  content: 'hello',
  content_bytes: Buffer.byteLength('hello', 'utf8'),
}

describe('validatePreparedContext', () => {
  it('accepts a ready v1 payload', () => {
    expect(validatePreparedContext(ready)).toEqual(ready)
  })

  it('accepts an empty v1 payload', () => {
    expect(validatePreparedContext({
      schema: PREPARED_CONTEXT_SCHEMA,
      status: 'empty',
      content: null,
      content_bytes: 0,
    })).toMatchObject({ status: 'empty', content: null, content_bytes: 0 })
  })

  it('rejects extra or missing fields', () => {
    expect(() => validatePreparedContext({ ...ready, extra: true })).toThrow(InvalidResponseError)
    expect(() => validatePreparedContext({ schema: PREPARED_CONTEXT_SCHEMA, status: 'ready' })).toThrow(InvalidResponseError)
  })

  it('rejects a wrong schema name', () => {
    expect(() => validatePreparedContext({ ...ready, schema: 'other' })).toThrow(InvalidResponseError)
  })

  it('rejects byte-count mismatch and content above the requested max', () => {
    expect(() => validatePreparedContext({ ...ready, content_bytes: 1 })).toThrow(InvalidResponseError)
    const huge = 'x'.repeat(8001)
    const bytes = Buffer.byteLength(huge, 'utf8')
    expect(() => validatePreparedContext({
      schema: PREPARED_CONTEXT_SCHEMA,
      status: 'ready',
      content: huge,
      content_bytes: bytes,
    }, '/v1/context/prepare', 8000)).toThrow(InvalidResponseError)
    expect(validatePreparedContext({
      schema: PREPARED_CONTEXT_SCHEMA,
      status: 'ready',
      content: huge,
      content_bytes: bytes,
    }, '/v1/context/prepare', 16000)).toMatchObject({ status: 'ready', content_bytes: bytes })
  })

  it('rejects empty status with leftover content', () => {
    expect(() => validatePreparedContext({
      schema: PREPARED_CONTEXT_SCHEMA,
      status: 'empty',
      content: 'nope',
      content_bytes: 0,
    })).toThrow(InvalidResponseError)
  })
})
