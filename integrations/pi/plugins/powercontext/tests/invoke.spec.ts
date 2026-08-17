import { describe, expect, it } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { invokeOperation } from '../src/invoke.ts'

describe('Pi native tool invocation', () => {
  it('uses scope only where required and refuses secret-bearing writes', async () => {
    let body: string | undefined
    const client = new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch: async (url, init) => {
        body = String(init.body)
        if (url.endsWith('/v1/handoff-reports/get')) return new Response('# Handoff report', { status: 200 })
        return new Response(JSON.stringify({ items: [] }), { status: 200 })
      },
    })

    await expect(invokeOperation(client, 'search_memory', {
      query: 'prior decision',
      scope_id: 'project:untrusted',
    }, 'project:derived')).resolves.toMatchObject({ ok: true })
    expect(JSON.parse(body ?? '{}')).toMatchObject({ scope_id: 'project:derived' })

    await expect(invokeOperation(client, 'get_handoff_report', {
      project_id: 'report-project',
      format: 'markdown',
    }, 'project:derived')).resolves.toMatchObject({
      ok: true,
      data: { markdown: '# Handoff report' },
    })
    expect(JSON.parse(body ?? '{}')).toEqual({ project_id: 'report-project', format: 'markdown' })

    await expect(invokeOperation(client, 'remember_memory', {
      kind: 'agent-note',
      text: 'api_key=secret',
    }, 'project:derived')).resolves.toMatchObject({
      ok: false,
      code: 'secret_rejected',
    })

    await expect(invokeOperation(client, 'reject_artifact_candidate', {
      candidate_id: 'candidate-1',
      expected_version: 1,
      reason: 'api_key=secret',
    }, 'project:derived')).resolves.toMatchObject({
      ok: false,
      code: 'secret_rejected',
    })
  })
})
