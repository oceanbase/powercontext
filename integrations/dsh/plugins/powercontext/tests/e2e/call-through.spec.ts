import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { PowerContextClient } from '../../src/client.ts'
import { startPowerContextServer } from '../../scripts/e2e-server.mjs'

const SCOPE_ID = 'project:dsh-e2e'
const TEXT = 'Keep the DSH plugin on the public HTTP contract.'

describe('plugin HTTP call-through without a model', () => {
  let server
  let client

  beforeAll(async () => {
    server = await startPowerContextServer()
    client = new PowerContextClient({
      baseUrl: server.baseUrl,
      requestTimeoutMs: 5000,
    })
  }, 60_000)

  afterAll(async () => {
    await server?.stop()
  })

  it('reaches liveness and readiness without inference', async () => {
    const live = await client.request('get_liveness')
    expect(live.kind).toBe('json')
    expect(live.value).toMatchObject({ status: 'ok' })
    const ready = await client.request('get_readiness')
    expect(ready.kind).toBe('json')
    expect(['ready', 'degraded']).toContain(ready.value.status)
  })

  it('remembers, searches, prepares, and captures over HTTP', async () => {
    const remembered = await client.request('remember_memory', {
      scope_id: SCOPE_ID,
      kind: 'decision',
      text: TEXT,
    })
    expect(remembered.kind).toBe('json')

    const found = await client.request('search_memory', {
      scope_id: SCOPE_ID,
      query: 'DSH plugin HTTP contract',
    })
    expect(found.kind).toBe('json')
    const hits = found.value.hits
    expect(Array.isArray(hits)).toBe(true)
    expect(hits.some((hit) => hit.text === TEXT)).toBe(true)

    const prepared = await client.request('prepare_context', {
      scope_id: SCOPE_ID,
      query: 'DSH plugin HTTP contract',
    })
    expect(prepared.kind).toBe('json')
    expect(typeof prepared.value.content === 'string' || prepared.value.content === null).toBe(true)

    const captured = await client.request('capture_content_source', {
      scope_id: SCOPE_ID,
      source_id: 'dsh-e2e-turn-1',
      content: 'Call through the plugin client without a model.',
      metadata: { origin: 'dsh', event: 'e2e' },
    })
    expect(captured.kind).toBe('json')
  })
})
