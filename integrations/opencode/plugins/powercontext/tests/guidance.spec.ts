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

import { readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { tool } from '@opencode-ai/plugin'
import { expect, it } from 'vitest'
import { PowerContextPlugin } from '../src/index.ts'

it('exposes guidance through the actual system transform and resolves its tool names', async () => {
  const hooks = await PowerContextPlugin({ directory: '/fixture', client: { app: { log: async () => ({}) } } } as never)
  const output = { system: [] as string[] }
  await hooks['experimental.chat.system.transform']?.({ model: {} as never }, output)
  const tools = Object.entries(hooks.tool ?? {}).map(([name, definition]) => ({ name, ...definition }))
  const names = new Set(tools.map(tool => tool.name))
  const finalize = tools.find(tool => tool.name === 'pc_handoff_finalize')!
  const parameters = tool.schema.object(finalize.args)
  const citation = { kind: 'source', source_ref: { name: 'content', source_id: 'boundary' } }
  const draft = { objective: 'Review docs', state: [{ text: 'README checked', citations: [citation] }],
    disposition: 'complete', next_action: null, omissions: [], generation: null }
  expect(parameters.safeParse({ draft }).success).toBe(true)
  expect(parameters.safeParse({ draft: { ok: true, data: draft } }).success).toBe(false)
  const current = tool.schema.object(tools.find(tool => tool.name === 'pc_handoff_current')!.args)
  const handoff = { schema: 'powercontext.current-work-handoff.v1', trust: 'untrusted_input', objective: 'Continue work',
    state: [{ text: 'Implementation inspected', basis: 'declared', evidence: [] }], disposition: 'continuable', next_action: null, omissions: [] }
  expect(current.safeParse({ source_id: 'boundary-1', handoff }).success).toBe(true)
  expect(current.safeParse({ source_id: 'boundary-1', handoff: { ...handoff, next_action: [] } }).success).toBe(false)
  expect(output.system.length).toBeGreaterThan(0)
  const skillRoot = new URL('../skills/powercontext-project-context/', import.meta.url)
  const router = readFileSync(new URL('SKILL.md', skillRoot), 'utf8')
  const references = [...router.matchAll(/\]\((references\/[^)]+)\)/g)]
    .map(match => readFileSync(new URL(match[1]!, skillRoot), 'utf8'))
  for (const name of (output.system.join('\n') + router + references.join('\n') + tools.map(tool => tool.description).join('\n')).match(/\bpc_[a-z_]+\b/g) ?? []) {
    expect(names.has(name), `unavailable tool referenced in OpenCode guidance: ${name}`).toBe(true)
  }
  const directory = process.env.POWERCONTEXT_GUIDANCE_EXPORT
  if (directory) writeFileSync(join(directory, 'opencode.json'), JSON.stringify({
    host: 'opencode', guidance: output.system.join('\n'),
    tools: tools.map(({ name, description, args }) => ({ name, description, parameters: tool.schema.toJSONSchema(tool.schema.object(args)) })),
    skill: { name: 'powercontext-project-context', content: readFileSync(new URL('../skills/powercontext-project-context/SKILL.md', import.meta.url), 'utf8') },
  }, null, 2))
})
