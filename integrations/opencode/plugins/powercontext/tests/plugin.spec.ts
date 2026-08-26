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

import { randomUUID } from 'node:crypto'
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { join } from 'node:path'
import { tmpdir } from 'node:os'

import { afterEach, describe, expect, it, vi } from 'vitest'
import { PowerContextPlugin } from '../src/index.ts'

const ENV_KEYS = [
  'POWERCONTEXT_OPENCODE_BASE_URL',
  'POWERCONTEXT_OPENCODE_SCOPE_ID',
  'POWERCONTEXT_OPENCODE_AUTHORIZATION',
  'POWERCONTEXT_OPENCODE_CAPTURE_PROMPTS',
  'POWERCONTEXT_OPENCODE_FLUSH_ON_CAPTURE',
  'POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_PATH',
  'POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_NONCE',
] as const

function pluginInput(sessionDirectories: Map<string, string> = new Map([['session-1', '/tmp/project']])) {
  return {
    directory: '/tmp/project',
    worktree: '/tmp/project',
    serverUrl: new URL('http://127.0.0.1:4096'),
    project: {},
    $: {},
    client: {
      app: { log: vi.fn(async () => ({})) },
      session: {
        get: vi.fn(async ({ path }: { path: { id: string } }) => ({
          data: sessionDirectories.has(path.id) ? { directory: sessionDirectories.get(path.id) } : undefined,
        })),
      },
    },
  } as any
}

function userMessage() {
  return {
    info: { id: 'msg-1', sessionID: 'session-1', role: 'user' },
    parts: [{ type: 'text', text: 'continue the parser work', messageID: 'msg-1', sessionID: 'session-1' }],
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
  for (const key of ENV_KEYS) delete process.env[key]
})

describe('PowerContextPlugin', () => {
  it('signals successful activation with the doctor nonce', async () => {
    const path = join(tmpdir(), `powercontext-opencode-${randomUUID()}`)
    process.env.POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_PATH = path
    process.env.POWERCONTEXT_OPENCODE_ACTIVATION_PROBE_NONCE = 'expected-nonce'
    try {
      const hooks = await PowerContextPlugin(pluginInput())
      expect(hooks.tool?.pc_remember).toBeDefined()
      expect(await readFile(path, 'utf8')).toBe('expected-nonce')
    } finally {
      await rm(path, { force: true })
    }
  })

  it('recalls, captures, and injects context once without persisting it through chat.message', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const calls: Array<{ url: string; body: any }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      const body = init.body ? JSON.parse(String(init.body)) : undefined
      calls.push({ url, body })
      if (url.endsWith('/v1/context/prepare')) {
        const content = 'Parser decision: preserve the public token shape.'
        return Response.json({
          schema: 'powercontext.prepared-context.v1',
          status: 'ready',
          content,
          content_bytes: Buffer.byteLength(content),
        })
      }
      return Response.json({ position: 7 }, { status: 202 })
    }))

    const hooks = await PowerContextPlugin(pluginInput())
    const incoming = userMessage()
    await hooks['chat.message']?.(
      { sessionID: 'session-1', messageID: 'msg-1' },
      { message: incoming.info, parts: incoming.parts } as any,
    )
    expect(incoming.parts).toHaveLength(1)

    const transformed = { messages: [incoming] }
    await hooks['experimental.chat.messages.transform']?.({}, transformed as any)
    await hooks['experimental.chat.messages.transform']?.({}, transformed as any)

    expect(incoming.parts).toHaveLength(2)
    expect(incoming.parts[1]).toMatchObject({ synthetic: true, messageID: 'msg-1', sessionID: 'session-1' })
    expect(incoming.parts[1]?.text).toContain('Parser decision')
    expect(calls.map((call) => call.url)).toEqual([
      'http://127.0.0.1:8000/v1/context/prepare',
      'http://127.0.0.1:8000/v1/sources/content',
    ])
    expect(calls[1]?.body.source_id).toMatch(/^opencode-user-prompt:/)
    expect(calls[1]?.body.metadata.origin).toBe('opencode')
  })

  it('fails open when the Server is unavailable', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('offline') }))
    const hooks = await PowerContextPlugin(pluginInput())
    const incoming = userMessage()
    await expect(hooks['chat.message']?.(
      { sessionID: 'session-1', messageID: 'msg-1' },
      { message: incoming.info, parts: incoming.parts } as any,
    )).resolves.toBeUndefined()
    await hooks['experimental.chat.messages.transform']?.({}, { messages: [incoming] } as any)
    expect(incoming.parts).toHaveLength(1)
  })

  it('normalizes the JSON string representation emitted by opencode run', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const calls: Array<{ url: string; body: any }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) })
      if (url.endsWith('/v1/context/prepare')) {
        return Response.json({
          schema: 'powercontext.prepared-context.v1',
          status: 'empty',
          content: null,
          content_bytes: 0,
        })
      }
      return Response.json({ position: 7 }, { status: 202 })
    }))
    const hooks = await PowerContextPlugin(pluginInput())
    const incoming = userMessage()
    incoming.parts[0]!.text = '"multi word prompt"'

    await hooks['chat.message']?.(
      { sessionID: 'session-1' },
      { message: incoming.info, parts: incoming.parts } as any,
    )

    expect(calls[0]?.body.query).toBe('multi word prompt')
    expect(calls[1]?.body.content).toBe('multi word prompt')
  })

  it('preserves intentional outer quotes in an already-decoded chat message', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const calls: Array<{ url: string; body: any }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) })
      if (url.endsWith('/v1/context/prepare')) {
        return Response.json({
          schema: 'powercontext.prepared-context.v1',
          status: 'empty',
          content: null,
          content_bytes: 0,
        })
      }
      return Response.json({ position: 7 }, { status: 202 })
    }))
    const hooks = await PowerContextPlugin(pluginInput())
    const incoming = userMessage()
    incoming.parts[0]!.text = '"preserve these quotes"'

    await hooks['chat.message']?.(
      { sessionID: 'session-1', messageID: 'msg-1' },
      { message: incoming.info, parts: incoming.parts } as any,
    )

    expect(calls[0]?.body.query).toBe('"preserve these quotes"')
    expect(calls[1]?.body.content).toBe('"preserve these quotes"')
  })

  it('asks before a durable tool operation', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    vi.stubGlobal('fetch', vi.fn(async () => Response.json({ revision: 1 })))
    const hooks = await PowerContextPlugin(pluginInput())
    const ask = vi.fn(async () => undefined)
    const result = await hooks.tool?.pc_remember?.execute(
      { kind: 'decision', text: 'Keep the stable v1 plugin API.' },
      {
        sessionID: 'session-1',
        messageID: 'msg-1',
        agent: 'build',
        directory: '/tmp/project',
        worktree: '/tmp/project',
        abort: new AbortController().signal,
        metadata: vi.fn(),
        ask,
      },
    )
    expect(ask).toHaveBeenCalledWith(expect.objectContaining({ permission: 'powercontext' }))
    expect(JSON.parse(String(result))).toMatchObject({ ok: true })
  })

  it('does not send secret-shaped prompts', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const fetchMock = vi.fn(async () => Response.json({
      schema: 'powercontext.prepared-context.v1',
      status: 'empty',
      content: null,
      content_bytes: 0,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const hooks = await PowerContextPlugin(pluginInput())
    const incoming = userMessage()
    incoming.parts[0]!.text = 'api_key=sk-1234567890'
    await hooks['chat.message']?.(
      { sessionID: 'session-1', messageID: 'msg-1' },
      { message: incoming.info, parts: incoming.parts } as any,
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('resolves automatic scope and capture cwd per OpenCode session', async () => {
    const firstDirectory = await mkdtemp(join(tmpdir(), 'powercontext-opencode-a-'))
    const secondDirectory = await mkdtemp(join(tmpdir(), 'powercontext-opencode-b-'))
    try {
      const calls: Array<{ url: string; body: any }> = []
      vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, body: JSON.parse(String(init.body)) })
        if (url.endsWith('/v1/context/prepare')) {
          return Response.json({
            schema: 'powercontext.prepared-context.v1',
            status: 'empty',
            content: null,
            content_bytes: 0,
          })
        }
        return Response.json({ position: 7 }, { status: 202 })
      }))
      const sessionDirectories = new Map([
        ['session-a', firstDirectory],
        ['session-b', secondDirectory],
      ])
      const input = pluginInput(sessionDirectories)
      const hooks = await PowerContextPlugin(input)

      for (const [sessionID, messageID] of [['session-a', 'msg-a'], ['session-b', 'msg-b']] as const) {
        await hooks.event?.({
          event: { type: 'session.created', properties: { info: { id: sessionID, directory: sessionDirectories.get(sessionID) } } },
        } as any)
        const incoming = userMessage()
        incoming.info.sessionID = sessionID
        incoming.info.id = messageID
        incoming.parts[0]!.sessionID = sessionID
        incoming.parts[0]!.messageID = messageID
        await hooks['chat.message']?.(
          { sessionID, messageID },
          { message: incoming.info, parts: incoming.parts } as any,
        )
      }

      const prepared = calls.filter((call) => call.url.endsWith('/v1/context/prepare'))
      expect(prepared).toHaveLength(2)
      expect(new Set(prepared.map((call) => call.body.scope_id)).size).toBe(2)
      const captured = calls.filter((call) => call.url.endsWith('/v1/sources/content'))
      expect(captured.map((call) => call.body.metadata.cwd)).toEqual([firstDirectory, secondDirectory])
      expect(input.client.session.get).not.toHaveBeenCalled()
    } finally {
      await rm(firstDirectory, { recursive: true, force: true })
      await rm(secondDirectory, { recursive: true, force: true })
    }
  })

  it('loads uncached session directories and shares scope only within the same project', async () => {
    const firstProject = await mkdtemp(join(tmpdir(), 'powercontext-opencode-shared-'))
    const secondProject = await mkdtemp(join(tmpdir(), 'powercontext-opencode-isolated-'))
    try {
      const calls: Array<{ url: string; body: any }> = []
      vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
        calls.push({ url, body: JSON.parse(String(init.body)) })
        if (url.endsWith('/v1/context/prepare')) {
          return Response.json({
            schema: 'powercontext.prepared-context.v1',
            status: 'empty',
            content: null,
            content_bytes: 0,
          })
        }
        return Response.json({ position: 7 }, { status: 202 })
      }))
      const input = pluginInput(new Map([
        ['session-a', firstProject],
        ['session-a-peer', firstProject],
        ['session-b', secondProject],
      ]))
      const hooks = await PowerContextPlugin(input)

      for (const [sessionID, messageID] of [
        ['session-a', 'msg-a'],
        ['session-a-peer', 'msg-a-peer'],
        ['session-b', 'msg-b'],
      ] as const) {
        const incoming = userMessage()
        incoming.info.sessionID = sessionID
        incoming.info.id = messageID
        incoming.parts[0]!.sessionID = sessionID
        incoming.parts[0]!.messageID = messageID
        await hooks['chat.message']?.(
          { sessionID, messageID },
          { message: incoming.info, parts: incoming.parts } as any,
        )
      }

      const prepared = calls.filter((call) => call.url.endsWith('/v1/context/prepare'))
      expect(prepared).toHaveLength(3)
      expect(prepared.map((call) => call.body.scope_id)).toEqual([
        prepared[0]?.body.scope_id,
        prepared[0]?.body.scope_id,
        prepared[2]?.body.scope_id,
      ])
      expect(prepared[2]?.body.scope_id).not.toBe(prepared[0]?.body.scope_id)
      const captured = calls.filter((call) => call.url.endsWith('/v1/sources/content'))
      expect(captured.map((call) => call.body.metadata.cwd)).toEqual([firstProject, firstProject, secondProject])
      expect(input.client.session.get).toHaveBeenCalledTimes(3)
    } finally {
      await rm(firstProject, { recursive: true, force: true })
      await rm(secondProject, { recursive: true, force: true })
    }
  })

  it('clears the cached session context when the session is deleted', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const calls: Array<{ url: string; body: any }> = []
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) })
      if (url.endsWith('/v1/context/prepare')) {
        return Response.json({
          schema: 'powercontext.prepared-context.v1',
          status: 'empty',
          content: null,
          content_bytes: 0,
        })
      }
      return Response.json({ position: 7 }, { status: 202 })
    }))
    const sessionDirectories = new Map([['session-1', '/tmp/project-before-delete']])
    const input = pluginInput(sessionDirectories)
    const hooks = await PowerContextPlugin(input)

    await hooks['chat.message']?.(
      { sessionID: 'session-1', messageID: 'msg-before' },
      { message: userMessage().info, parts: userMessage().parts } as any,
    )
    sessionDirectories.set('session-1', '/tmp/project-after-delete')
    await hooks.event?.({
      event: { type: 'session.deleted', properties: { info: { id: 'session-1' } } },
    } as any)
    await hooks['chat.message']?.(
      { sessionID: 'session-1', messageID: 'msg-after' },
      { message: { ...userMessage().info, id: 'msg-after' }, parts: userMessage().parts } as any,
    )

    const captured = calls.filter((call) => call.url.endsWith('/v1/sources/content'))
    expect(captured.map((call) => call.body.metadata.cwd)).toEqual([
      '/tmp/project-before-delete',
      '/tmp/project-after-delete',
    ])
    expect(input.client.session.get).toHaveBeenCalledTimes(2)
  })

  it('fails open when the OpenCode session directory is unavailable', async () => {
    process.env.POWERCONTEXT_OPENCODE_SCOPE_ID = 'project:test'
    const fetchMock = vi.fn(async () => Response.json({
      schema: 'powercontext.prepared-context.v1',
      status: 'empty',
      content: null,
      content_bytes: 0,
    }))
    vi.stubGlobal('fetch', fetchMock)
    const hooks = await PowerContextPlugin(pluginInput(new Map()))

    await hooks['chat.message']?.(
      { sessionID: 'missing-session', messageID: 'msg-1' },
      { message: userMessage().info, parts: userMessage().parts } as any,
    )

    expect(fetchMock).not.toHaveBeenCalled()
  })
})
