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
import { expect, it } from 'vitest'
import { Value } from 'typebox/value'
import type { TSchema } from 'typebox'
import powercontextPi from '../extensions/powercontext.ts'
import { GUIDANCE } from '../src/guidance.ts'

it('routes only to the registered Pi tools without requiring a Skill load', () => {
  const tools: Array<{ name: string; description: string; parameters: unknown }> = []
  powercontextPi({
    on: () => undefined, registerCommand: () => undefined,
    registerTool: (tool: typeof tools[number]) => tools.push(tool),
  } as never)
  const names = new Set(tools.map(tool => tool.name))
  const finalize = tools.find(tool => tool.name === 'pc_handoff_finalize')!
  const citation = { kind: 'source', source_ref: { name: 'content', source_id: 'boundary' } }
  const draft = { objective: 'Review docs', state: [{ text: 'README checked', citations: [citation] }],
    disposition: 'complete', next_action: null, omissions: [], generation: null }
  expect(Value.Check(finalize.parameters as TSchema, { draft })).toBe(true)
  expect(Value.Check(finalize.parameters as TSchema, { draft: { ok: true, data: draft } })).toBe(false)
  for (const name of (GUIDANCE + tools.map(tool => tool.description).join('\n')).match(/\bpc_[a-z_]+\b/g) ?? []) {
    expect(names.has(name), `unavailable tool referenced in Pi guidance: ${name}`).toBe(true)
  }
  const output = process.env.POWERCONTEXT_GUIDANCE_EXPORT
  if (output) writeFileSync(join(output, 'pi.json'), JSON.stringify({
    host: 'pi', guidance: GUIDANCE, tools: tools.map(({ name, description, parameters }) => ({ name, description, parameters })),
    skill: { name: 'powercontext-project-context', content: readFileSync(new URL('../skills/powercontext-project-context/SKILL.md', import.meta.url), 'utf8') },
  }, null, 2))
})
