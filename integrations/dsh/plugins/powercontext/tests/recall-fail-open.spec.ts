import { describe, expect, it, vi } from 'vitest'
import { UnavailableError } from '../src/errors.ts'
import { runRecallPreStep, type RecallInput } from '../src/recall.ts'
import type { ResolvedConfig } from '../src/config.ts'
import { deriveScopeId } from '../src/scope.ts'

const config: ResolvedConfig = {
  baseUrl: 'http://127.0.0.1:8000',
  authorization: undefined,
  scopeId: 'project:demo',
  timeoutMs: 4000,
  requestTimeoutMs: 1000,
  maxBytes: 8000,
  capturePrompts: true,
  flushOnCapture: false,
  flushMaxCalls: 4,
}

function input(overrides: Partial<RecallInput> = {}): RecallInput {
  return {
    messages: [{
      content: [{ type: 'text', text: 'remember the public API stays async' }],
      source: { kind: 'user' },
    }],
    next: async () => ({ kind: 'enter', messages: [] }),
    cwd: '/repo',
    sessionId: 's1',
    turnId: '1',
    client: { request: vi.fn() } as never,
    config,
    resolveScope: async () => 'project:demo',
    wrapContent: (text) => ({ role: 'user', content: [{ type: 'text', text }] }),
    log: vi.fn(),
    ...overrides,
  }
}

describe('runRecallPreStep fail-open', () => {
  it('calls next when messages are empty', async () => {
    const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [] }))
    const result = await runRecallPreStep(input({ messages: [], next }))
    expect(next).toHaveBeenCalledOnce()
    expect(result).toEqual({ kind: 'enter', messages: [] })
  })

  it('still calls next when prepare fetch rejects', async () => {
    const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [{ id: 'user' }] }))
    const request = vi.fn(async (operationId: string) => {
      if (operationId === 'prepare_context') throw new UnavailableError('/v1/context/prepare')
      return { kind: 'json', value: { status: 'accepted' }, status: 202, requestId: undefined }
    })
    const result = await runRecallPreStep(input({ next, client: { request } as never }))
    expect(next).toHaveBeenCalledOnce()
    expect(result).toEqual({ kind: 'enter', messages: [{ id: 'user' }] })
    expect(request).toHaveBeenCalled()
  })

  it('does not throw when next is reached after an invalid prepare payload', async () => {
    const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [] }))
    const request = vi.fn(async (operationId: string) => {
      if (operationId === 'prepare_context') {
        return { kind: 'json', value: { schema: 'nope' }, status: 200, requestId: undefined }
      }
      throw new Error('capture should be independent')
    })
    await expect(runRecallPreStep(input({ next, client: { request } as never }))).resolves.toEqual({
      kind: 'enter',
      messages: [],
    })
    expect(next).toHaveBeenCalledOnce()
  })

  it('does not call next again when wrapContent throws after a successful step', async () => {
    const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [{ id: 'user' }] }))
    const content = 'Public API stays async.'
    const request = vi.fn(async (operationId: string) => {
      if (operationId === 'prepare_context') {
        return {
          kind: 'json' as const,
          value: {
            schema: 'powercontext.prepared-context.v1',
            status: 'ready',
            content,
            content_bytes: Buffer.byteLength(content, 'utf8'),
          },
          status: 200,
          requestId: undefined,
        }
      }
      return { kind: 'json' as const, value: { status: 'accepted', position: 1 }, status: 202, requestId: undefined }
    })
    const result = await runRecallPreStep(input({
      next,
      client: { request } as never,
      wrapContent: () => {
        throw new Error('wrap failed')
      },
    }))
    expect(next).toHaveBeenCalledOnce()
    expect(result).toEqual({ kind: 'enter', messages: [{ id: 'user' }] })
  })

  it('skips capture when POWERCONTEXT_DSH_CAPTURE_PROMPTS is disabled', async () => {
    const request = vi.fn(async (operationId: string) => {
      if (operationId === 'prepare_context') {
        return {
          kind: 'json' as const,
          value: {
            schema: 'powercontext.prepared-context.v1',
            status: 'empty',
            content: null,
            content_bytes: 0,
          },
          status: 200,
          requestId: undefined,
        }
      }
      throw new Error(`unexpected ${operationId}`)
    })
    await runRecallPreStep(input({
      client: { request } as never,
      config: { ...config, capturePrompts: false },
    }))
    expect(request.mock.calls.map((call) => call[0])).toEqual(['prepare_context'])
  })

  it('recalls from the full batch but captures only explicitly user-originated messages', async () => {
    const request = vi.fn(async (operationId: string) => {
      if (operationId === 'prepare_context') {
        return {
          kind: 'json' as const,
          value: {
            schema: 'powercontext.prepared-context.v1',
            status: 'empty',
            content: null,
            content_bytes: 0,
          },
          status: 200,
          requestId: undefined,
        }
      }
      return { kind: 'json' as const, value: { status: 'accepted', position: 1 }, status: 202, requestId: undefined }
    })
    await runRecallPreStep(input({
      client: { request } as never,
      messages: [
        { content: [{ type: 'text', text: 'Human request' }], source: { kind: 'user' } },
        {
          content: [{ type: 'text', text: 'Plugin-provided context' }],
          source: { kind: 'plugin', plugin: 'example-context', form: 'recall' },
        },
      ],
    }))

    expect(request).toHaveBeenCalledTimes(2)
    expect(request.mock.calls[0]).toEqual([
      'prepare_context',
      { scope_id: 'project:demo', query: 'Human request\n\nPlugin-provided context', max_bytes: 8000 },
      undefined,
    ])
    expect(request.mock.calls[1][0]).toBe('capture_content_source')
    expect(request.mock.calls[1][1]).toMatchObject({
      scope_id: 'project:demo',
      content: 'Human request',
      metadata: { origin: 'dsh', event: 'user_prompt_submit' },
    })
  })

  it('does not capture a batch that contains only plugin-originated context', async () => {
    const request = vi.fn(async () => ({
      kind: 'json' as const,
      value: {
        schema: 'powercontext.prepared-context.v1',
        status: 'empty',
        content: null,
        content_bytes: 0,
      },
      status: 200,
      requestId: undefined,
    }))
    await runRecallPreStep(input({
      client: { request } as never,
      messages: [{
        content: [{ type: 'text', text: 'Plugin-only context' }],
        source: { kind: 'plugin', plugin: 'example-context' },
      }],
    }))

    expect(request.mock.calls.map((call) => call[0])).toEqual(['prepare_context'])
  })

  it('appends untrusted context after a ready prepare result', async () => {
    const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [] }))
    const content = 'Public API stays async.'
    const request = vi.fn(async (operationId: string) => {
      if (operationId === 'prepare_context') {
        return {
          kind: 'json' as const,
          value: {
            schema: 'powercontext.prepared-context.v1',
            status: 'ready',
            content,
            content_bytes: Buffer.byteLength(content, 'utf8'),
          },
          status: 200,
          requestId: undefined,
        }
      }
      return { kind: 'json' as const, value: { status: 'accepted', position: 1 }, status: 202, requestId: undefined }
    })
    const result = await runRecallPreStep(input({ next, client: { request } as never }))
    expect(result.kind).toBe('enter')
    if (result.kind === 'enter') {
      expect(result.messages).toHaveLength(1)
      const wrapped = result.messages[0] as { content: Array<{ text: string }> }
      expect(wrapped.content[0].text).toContain('untrusted historical evidence')
      expect(wrapped.content[0].text).toContain(content)
    }
  })

  it('skips recall when cwd is missing and scopeId is not configured', async () => {
    const next = vi.fn(async () => ({ kind: 'enter' as const, messages: [{ id: 'user' }] }))
    const log = vi.fn()
    const result = await runRecallPreStep(input({
      next,
      cwd: undefined,
      config: { ...config, scopeId: undefined },
      resolveScope: (cwd) => deriveScopeId(cwd),
      log,
    }))
    expect(next).toHaveBeenCalledOnce()
    expect(result).toEqual({ kind: 'enter', messages: [{ id: 'user' }] })
    expect(log).toHaveBeenCalledWith({
      event: 'context_prepare',
      outcome: 'skipped',
      reason: 'missing_session_cwd',
    })
  })

  it('recalls with configured scopeId and omits a fabricated cwd from Source', async () => {
    const request = vi.fn(async (operationId: string) => {
      if (operationId === 'prepare_context') {
        return {
          kind: 'json' as const,
          value: {
            schema: 'powercontext.prepared-context.v1',
            status: 'empty',
            content: null,
            content_bytes: 0,
          },
          status: 200,
          requestId: undefined,
        }
      }
      return { kind: 'json' as const, value: { status: 'accepted', position: 1 }, status: 202, requestId: undefined }
    })
    await runRecallPreStep(input({
      cwd: undefined,
      client: { request } as never,
      resolveScope: (cwd) => deriveScopeId(cwd, { configuredScopeId: 'project:demo' }),
    }))
    const capture = request.mock.calls.find((call) => call[0] === 'capture_content_source')
    expect(capture?.[0]).toBe('capture_content_source')
    expect(capture?.[1]).toMatchObject({
      scope_id: 'project:demo',
      metadata: { origin: 'dsh', event: 'user_prompt_submit', session_id: 's1' },
    })
    expect((capture?.[1] as { metadata: { cwd?: string } }).metadata.cwd).toBeUndefined()
  })
})
