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

import { afterEach, describe, expect, it, vi } from 'vitest'
import { handlePcCommand } from '../src/commands.ts'
import { PowerContextClient } from '../src/client.ts'
import type { PluginRuntime } from '../src/recall.ts'

function runtime(fetch: typeof globalThis.fetch, resolveScope = async () => 'project:demo'): PluginRuntime {
  return {
    client: new PowerContextClient({
      baseUrl: 'http://127.0.0.1:8000',
      requestTimeoutMs: 1000,
      fetch,
    }),
    config: {
      baseUrl: 'http://127.0.0.1:8000',
      scopeId: undefined,
      authorization: undefined,
      capturePrompts: true,
      requestTimeoutMs: 1000,
      httpBudgetMs: 4000,
      maxBytes: 8000,
      flushOnCapture: false,
      flushMaxCalls: 4,
    },
    resolveScope,
  }
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('/pc command', () => {
  it('dispatches query commands with the current project scope and reports results', async () => {
    const requests: Array<{ url: URL; body: unknown }> = []
    const notifications: Array<{ message: string; level: 'info' | 'error' }> = []
    const fetch = async (url: string | URL | Request, init?: RequestInit) => {
      const requestUrl = new URL(String(url))
      requests.push({
        url: requestUrl,
        body: init?.body ? JSON.parse(String(init.body)) : undefined,
      })
      return new Response(JSON.stringify({ items: [] }))
    }
    const context = {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: {
        confirm: async () => true,
        notify: (message: string, level: 'info' | 'error') => notifications.push({ message, level }),
      },
    }

    await handlePcCommand('search prior decision', runtime(fetch), context)
    await handlePcCommand('flush', runtime(fetch), context)
    await handlePcCommand('stats', runtime(fetch), context)
    await handlePcCommand('doctor', runtime(fetch), context)

    const search = requests.find(({ url }) => url.pathname === '/v1/memory/search')
    const flush = requests.find(({ url }) => url.pathname === '/v1/memory/flush')
    const stats = requests.find(({ url }) => url.pathname === '/v1/stats')
    const live = requests.find(({ url }) => url.pathname === '/health/live')
    const ready = requests.find(({ url }) => url.pathname === '/health/ready')
    expect(search?.body).toEqual({ query: 'prior decision', limit: 8, mode: 'auto', scope_id: 'project:demo' })
    expect(flush?.body).toEqual({ scope_id: 'project:demo' })
    expect(stats?.url.searchParams.get('scope_id')).toBe('project:demo')
    expect(live).toBeDefined()
    expect(ready).toBeDefined()
    expect(notifications.some(({ message }) => JSON.parse(message).ok === true)).toBe(true)
    expect(notifications.every(({ level }) => level === 'info')).toBe(true)
  })

  it('reports a confirmation requirement before explicit commands can persist data', async () => {
    const log = vi.spyOn(console, 'log').mockImplementation(() => undefined)
    const fetch = vi.fn(async () => new Response(JSON.stringify({ entry: { text: 'remembered' } })))
    const context = {
      cwd: '/workspace/repo',
      hasUI: false,
      ui: {
        confirm: async () => true,
        notify: () => undefined,
      },
    }

    await handlePcCommand('remember keep API async', runtime(fetch), context)
    await handlePcCommand('flush', runtime(fetch), context)

    const reports = log.mock.calls.map(([message]) => JSON.parse(String(message))).filter(Boolean)
    expect(reports).toEqual([
      expect.objectContaining({ ok: false, code: 'confirmation_required' }),
      expect.objectContaining({ ok: false, code: 'confirmation_required' }),
    ])
    expect(fetch).not.toHaveBeenCalled()
  })

  it('reports an unavailable command instead of throwing when scope resolution fails', async () => {
    const notifications: Array<{ message: string; level: 'info' | 'error' }> = []
    const context = {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: {
        confirm: async () => true,
        notify: (message: string, level: 'info' | 'error') => notifications.push({ message, level }),
      },
    }
    const unavailableScope = async () => {
      throw new Error('scope unavailable')
    }

    await expect(handlePcCommand('search prior decision', runtime(fetch, unavailableScope), context)).resolves.toBeUndefined()

    expect(notifications).toEqual([{
      message: JSON.stringify({ ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.' }, null, 2),
      level: 'error',
    }])
  })

  it('reports an unavailable /pc doctor instead of throwing when scope resolution fails', async () => {
    const notifications: Array<{ message: string; level: 'info' | 'error' }> = []
    const context = {
      cwd: '/workspace/repo',
      hasUI: true,
      ui: {
        confirm: async () => true,
        notify: (message: string, level: 'info' | 'error') => notifications.push({ message, level }),
      },
    }
    const unavailableScope = async () => {
      throw new Error('scope unavailable')
    }

    await expect(handlePcCommand('doctor', runtime(fetch, unavailableScope), context)).resolves.toBeUndefined()

    expect(notifications).toEqual([{
      message: JSON.stringify({ ok: false, code: 'unavailable', message: 'PowerContext is unavailable, continue the task.' }, null, 2),
      level: 'error',
    }])
  })
})
