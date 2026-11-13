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

import { readFileSync } from 'node:fs'
import { describe, expect, it, vi } from 'vitest'
import type { ResolvedConfig } from '../src/config.ts'
import { runRecallPreStep } from '../src/recall.ts'
import { containsSecret, scrubSecrets } from '../src/secrets.ts'

const { cases } = JSON.parse(readFileSync(
  new URL('../../../../../tests/fixtures/automatic_prompt_privacy.json', import.meta.url), 'utf8',
)) as { cases: { id: string; prompt: string; sensitive: boolean; authorization?: string }[] }

const config: ResolvedConfig = {
  sources: { baseUrl: 'plugin', authorization: 'plugin', scopeId: 'plugin' },
  baseUrl: 'http://127.0.0.1:8000',
  allowInsecureHttp: false,
  authorization: undefined,
  scopeId: 'project:demo',
  timeoutMs: 4000,
  requestTimeoutMs: 1000,
  maxBytes: 8000,
  capturePrompts: true,
  flushOnCapture: false,
  flushMaxCalls: 4,
}

function expectNoSyntheticSecret(value: string): void {
  expect(value).not.toContain('synthetic-value-2084')
  expect(value).not.toContain('synthetic_value_2084')
}

describe('shared automatic prompt privacy policy', () => {
  it.each(cases)('$id', ({ prompt, sensitive, authorization }) => {
    expect(containsSecret(prompt, authorization)).toBe(sensitive)
    const query = scrubSecrets(prompt, authorization)
    if (sensitive) expectNoSyntheticSecret(query)
    else expect(query).toBe(prompt)
  })
})

describe('automatic recall and capture', () => {
  it.each(cases)('$id', async ({ prompt, sensitive, authorization }) => {
    const requests: { operationId: string; body: unknown }[] = []
    const request = vi.fn(async (operationId: string, body: unknown) => {
      requests.push({ operationId, body })
      if (operationId === 'prepare_context') {
        return { kind: 'json', value: { schema: 'powercontext.prepared-context.v1', status: 'empty' }, status: 200 }
      }
      return { kind: 'json', value: { status: 'accepted', position: 1 }, status: 202 }
    })
    const messages = [{ content: [{ type: 'text', text: prompt }], source: { kind: 'user' } }]
    await runRecallPreStep({
      messages,
      next: async () => ({ kind: 'enter', messages }),
      cwd: '/repo',
      sessionId: 's1',
      turnId: '1',
      client: { request } as never,
      config: { ...config, authorization },
      resolveScope: async () => 'project:demo',
      wrapContent: (text) => ({ role: 'user', content: [{ type: 'text', text }] }),
      log: vi.fn(),
    })

    const prepare = requests.find(({ operationId }) => operationId === 'prepare_context')
    const captures = requests.filter(({ operationId }) => operationId === 'capture_content_source')
    expect(prepare).toBeDefined()
    if (sensitive) {
      expect(captures).toEqual([])
      expectNoSyntheticSecret(JSON.stringify(requests))
    } else {
      expect(prepare!.body).toMatchObject({ query: prompt.trim() })
      expect(captures).toHaveLength(1)
      expect(captures[0]!.body).toMatchObject({ content: prompt.trim() })
    }
  })
})
