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
import { PowerContextClient } from '../src/client.ts'
import { invokeOperation } from '../src/invoke.ts'
import { registerMcpPolicy } from '../src/mcp.ts'

vi.mock('../src/mcp-transport.ts', () => ({ protectMcpEndpoint: async (_ctx: unknown, endpoint: string) => endpoint }))

type Case = { id: string; prompt: string; sensitive: boolean; authorization?: string }
type PolicyHook = (exec: unknown, next: () => Promise<unknown>) => Promise<unknown>

const { cases } = JSON.parse(readFileSync(
  new URL('../../../../../tests/fixtures/automatic_prompt_privacy.json', import.meta.url), 'utf8',
)) as { cases: Case[] }

const ORDINARY = [
  'Use a risk-based rollout with task-specific checks before the task-runner starts.',
  'The disk-space alert and the ask-first policy stay enabled.',
]

// Each write nests the text where its operation carries content, including nested Handoff state.
const WRITES: [string, (text: string) => Record<string, unknown>][] = [
  ['remember_memory', text => ({ kind: 'decision', text })],
  ['capture_content_source', text => ({ content: text })],
  ['handoff_current_work', text => ({ handoff: { state: [{ text, evidence: [] }], omissions: [] } })],
  ['finalize_handoff', text => ({ draft: { next_action: { text, evidence: [] } } })],
  ['record_task_outcome', text => ({ outcome: { remaining_work: [text] } })],
  ['create_scope', text => ({ summary: text })],
]

function policyHook(resolveScope: () => Promise<string>, authorization?: string): PolicyHook {
  let hook: PolicyHook | undefined
  registerMcpPolicy({ on: (_event, listener) => { hook = listener as unknown as PolicyHook } }, resolveScope, authorization)
  if (!hook) throw new Error('MCP policy hook was not registered')
  return hook
}

function expectNoSyntheticSecret(value: unknown): void {
  const serialized = JSON.stringify(value)
  expect(serialized).not.toContain('synthetic-value-2084')
  expect(serialized).not.toContain('synthetic_value_2084')
}

const policyCases = [
  ...cases,
  ...ORDINARY.map((prompt, index) => ({ id: `ordinary-${index + 1}`, prompt, sensitive: false })),
].flatMap(item => WRITES.map(([operation, build]) => ({ ...item, operation, build })))

describe('explicit MCP writes follow the shared prompt privacy rule', () => {
  it.each(policyCases)('$operation: $id', async ({ operation, build, prompt, sensitive, authorization }) => {
    const resolveScope = vi.fn(async () => 'scope-a')
    const result = await policyHook(resolveScope, authorization)({
      name: `mcp__powercontext__${operation}`,
      arguments: { scope_id: 'scope-a', ...build(prompt) },
      signal: new AbortController().signal,
    }, async () => ({ kind: 'allow' }))
    if (sensitive) {
      expect(result).toMatchObject({ kind: 'deny', reason: expect.stringContaining('secret_rejected') })
      expectNoSyntheticSecret(result)
      expect(resolveScope).not.toHaveBeenCalled()
    } else {
      expect(JSON.stringify(result)).not.toContain('secret_rejected')
    }
  })
})

describe('explicit operation writes follow the shared prompt privacy rule', () => {
  it.each([
    ...cases,
    ...ORDINARY.map((prompt, index) => ({ id: `ordinary-${index + 1}`, prompt, sensitive: false })),
  ])('$id', async ({ prompt, sensitive, authorization }) => {
    const fetch = vi.fn(async () => Response.json({ status: 'accepted' }, { status: 202 }))
    const client = new PowerContextClient({ baseUrl: 'http://127.0.0.1:8000', requestTimeoutMs: 1000, authorization, fetch })
    const result = await invokeOperation(client, 'remember_memory', { kind: 'decision', text: prompt }, 'project:demo')
    if (sensitive) {
      expect(result).toMatchObject({ ok: false, code: 'secret_rejected' })
      expectNoSyntheticSecret(result)
      expect(fetch).not.toHaveBeenCalled()
    } else {
      expect(result).not.toMatchObject({ code: 'secret_rejected' })
      expect(fetch).toHaveBeenCalled()
    }
  })
})
