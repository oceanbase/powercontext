import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it, vi } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { PLUGIN_USER_AGENT, PLUGIN_VERSION, ServerResponseError, UnavailableError, UnknownOperationError } from '../src/errors.ts'

function jsonResponse(status: number, body: unknown, headers?: Record<string, string>): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json', ...headers },
  })
}

describe('PowerContextClient', () => {
  it('keeps the User-Agent version aligned with package.json', () => {
    const manifest = JSON.parse(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'package.json'), 'utf8'))
    expect(PLUGIN_VERSION).toBe(manifest.version)
    expect(PLUGIN_USER_AGENT).toBe(`powercontext-dsh/${manifest.version}`)
  })

  it('POSTs JSON for remember_memory and sends Authorization', async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe('http://127.0.0.1:8000/v1/memory/remember')
      expect(init?.method).toBe('POST')
      expect(init?.redirect).toBe('manual')
      const headers = new Headers(init?.headers)
      expect(headers.get('Authorization')).toBe('Bearer token')
      expect(headers.get('User-Agent')).toBe('powercontext-dsh/0.0.2')
      expect(JSON.parse(String(init?.body))).toEqual({ scope_id: 'project:demo', kind: 'decision', text: 'keep API async' })
      return jsonResponse(200, { entry: { text: 'keep API async' } }, { 'X-PowerContext-Request-ID': 'req-1' })
    })
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000/',
      authorization: 'Bearer token',
      requestTimeoutMs: 1000,
      fetch: fetchImpl,
    })
    const result = await client.request('remember_memory', {
      scope_id: 'project:demo',
      kind: 'decision',
      text: 'keep API async',
    })
    expect(result).toMatchObject({ kind: 'json', status: 200, requestId: 'req-1' })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('sends get_stats as a GET query string', async () => {
    const fetchImpl = vi.fn(async (url: string, init?: RequestInit) => {
      expect(url).toBe('http://127.0.0.1:8000/v1/stats?scope_id=project%3Ademo&period=7d')
      expect(init?.method).toBe('GET')
      expect(init?.body).toBeUndefined()
      return jsonResponse(200, { memories: 1 })
    })
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: fetchImpl,
    })
    await client.request('get_stats', { scope_id: 'project:demo', period: '7d' })
    expect(fetchImpl).toHaveBeenCalledOnce()
  })

  it('returns markdown text and raw bytes for get_handoff_report', async () => {
    const markdownClient = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response('# Report', { status: 200 }),
    })
    await expect(markdownClient.request('get_handoff_report', { project_id: 'p1', format: 'markdown' })).resolves.toMatchObject({
      kind: 'text',
      value: '# Report',
    })
    await expect(markdownClient.request('get_handoff_report', { project_id: 'p1' })).resolves.toMatchObject({
      kind: 'text',
      value: '# Report',
    })
    const bytesClient = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response(new Uint8Array([1, 2, 3]), { status: 200 }),
    })
    const downloaded = await bytesClient.request('get_handoff_report', { project_id: 'p1', download: true })
    expect(downloaded.kind).toBe('bytes')
    if (downloaded.kind === 'bytes') expect([...downloaded.value]).toEqual([1, 2, 3])
  })

  it('maps non-2xx JSON errors and unknown ids', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => jsonResponse(409, { error: { code: 'conflict', message: 'citation mismatch' } }, { 'X-PowerContext-Request-ID': 'req-9' }),
    })
    await expect(client.request('revise_memory_entry', {})).rejects.toMatchObject({
      statusCode: 409,
      code: 'conflict',
      requestId: 'req-9',
    } satisfies Partial<ServerResponseError>)
    await expect(client.request('not_an_operation', {})).rejects.toBeInstanceOf(UnknownOperationError)
  })

  it('maps network failure to UnavailableError and rejects redirects', async () => {
    const down = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => {
        throw new TypeError('fetch failed')
      },
    })
    await expect(down.request('get_liveness')).rejects.toBeInstanceOf(UnavailableError)
    const redirected = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response(null, { status: 302, headers: { Location: 'https://evil.example' } }),
    })
    await expect(redirected.request('get_liveness')).rejects.toThrow()
  })

  it('emits the generated method and path for every operationId', async () => {
    const { OPERATION_IDS, OPERATIONS } = await import('../src/operations.generated.ts')
    const seen: Array<{ method: string; url: string; hasBody: boolean }> = []
    const client = new PowerContextClient({
      baseUrl: 'http://example.test',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        seen.push({ method: String(init?.method), url, hasBody: Boolean(init?.body) })
        return jsonResponse(200, { ok: true })
      },
    })
    for (const id of OPERATION_IDS) {
      const spec = OPERATIONS[id]
      await client.request(id, spec.location === 'query' ? { scope_id: 's' } : { marker: id })
    }
    expect(seen).toHaveLength(48)
    OPERATION_IDS.forEach((id, index) => {
      const spec = OPERATIONS[id]
      expect(seen[index].method).toBe(spec.method)
      expect(seen[index].url.startsWith(`http://example.test${spec.path}`)).toBe(true)
      expect(seen[index].hasBody).toBe(spec.method === 'POST' && spec.location === 'body')
    })
  })
})
