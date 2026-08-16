import { describe, expect, it } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { invokeOperation } from '../src/invoke.ts'

describe('Pi native tool invocation', () => {
  it('uses the derived project scope and refuses secret-bearing writes', async () => {
    let body: string | undefined
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (_url, init) => {
        body = String(init.body)
        return new Response(JSON.stringify({ items: [] }), { status: 200 })
      },
    })

    await expect(invokeOperation(client, 'search_memory', {
      query: 'prior decision',
      scope_id: 'project:untrusted',
    }, 'project:derived')).resolves.toMatchObject({ ok: true })
    expect(JSON.parse(body ?? '{}')).toMatchObject({ scope_id: 'project:derived' })

    await expect(invokeOperation(client, 'remember_memory', {
      kind: 'agent-note',
      text: 'api_key=secret',
    }, 'project:derived')).resolves.toMatchObject({
      ok: false,
      code: 'secret_rejected',
    })
  })
})
