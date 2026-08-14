import { describe, expect, it } from 'vitest'
import { invokeOperation } from '../src/invoke.ts'
import { PowerContextClient } from '../src/client.ts'
import { containsSecret } from '../src/secrets.ts'
import { PROJECT_CONTEXT_SKILL } from '../src/skill-body.ts'
import { readFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

describe('secrets', () => {
  it('rejects token-like markers', () => {
    expect(containsSecret('sk-live-secret')).toBe(true)
    expect(containsSecret('api_key=foo')).toBe(true)
    expect(containsSecret('-----BEGIN PRIVATE KEY-----')).toBe(true)
    expect(containsSecret('keep the public API async')).toBe(false)
  })
})

describe('invokeOperation', () => {
  it('injects scope_id for scoped operations and skips health', async () => {
    const seen: Array<{ id: string; url: string; body: string | undefined }> = []
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        seen.push({ id: url, url, body: init?.body ? String(init.body) : undefined })
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      },
    })
    await invokeOperation(client, 'search_memory', { query: 'api' }, 'project:demo')
    await invokeOperation(client, 'get_liveness', {}, 'project:demo')
    expect(JSON.parse(seen[0].body ?? '{}')).toMatchObject({ query: 'api', scope_id: 'project:demo' })
    expect(seen[1].body).toBeUndefined()
    expect(seen[1].url).toBe('http://127.0.0.1:8000/health/live')
  })

  it('overwrites a caller-supplied scope_id with the derived workspace scope', async () => {
    let body: string | undefined
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        body = init?.body ? String(init.body) : undefined
        return new Response(JSON.stringify({ ok: true }), { status: 200 })
      },
    })

    await invokeOperation(client, 'search_memory', {
      query: 'api',
      scope_id: 'project:attacker-controlled',
    }, 'project:derived-workspace')

    expect(JSON.parse(body ?? '{}')).toMatchObject({
      query: 'api',
      scope_id: 'project:derived-workspace',
    })
  })

  it('returns unavailable instead of throwing when the server is down', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => {
        throw new TypeError('fetch failed')
      },
    })
    await expect(invokeOperation(client, 'search_memory', { query: 'api' }, 'project:demo')).resolves.toMatchObject({
      ok: false,
      code: 'unavailable',
      message: 'PowerContext is unavailable, continue the task.',
    })
  })

  it('refuses secret-like remember payloads', async () => {
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async () => new Response('{}', { status: 200 }),
    })
    await expect(invokeOperation(client, 'remember_memory', { kind: 'decision', text: 'sk-secret' }, 'project:demo')).resolves.toMatchObject({
      ok: false,
      code: 'secret_rejected',
    })
  })
})

describe('skill body', () => {
  it('stays aligned with the markdown source', () => {
    const markdown = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'src', 'skill-body.md'), 'utf8')
    expect(PROJECT_CONTEXT_SKILL.replaceAll('\r\n', '\n').trim()).toBe(markdown.replaceAll('\r\n', '\n').trim())
  })
})
