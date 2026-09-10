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

import { expect, it } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { resolveConfig } from '../src/config.ts'
import { registerTools } from '../src/tools.ts'
import { registerGuidance, registerSkill } from '../src/skill.ts'

it('exposes independently available guidance whose tool references resolve in the registered catalog', () => {
  const tools: Array<Record<string, any>> = []
  const sections: Array<{ text: string }> = []
  const skills: Array<{ name: string; description: string; content: string }> = []
  const config = resolveConfig({ baseUrl: 'http://127.0.0.1:1' })
  registerTools({ tools: { register: tool => tools.push(tool as Record<string, any>) }, on: () => undefined }, {
    client: new PowerContextClient({ baseUrl: config.baseUrl, requestTimeoutMs: 1000 }), config,
    resolveScope: async () => 'fixture-scope', log: () => undefined,
  }, definition => definition)
  const ctx = { get: (name: string) => name === 'systemPrompt'
    ? { section: (section: { text: string }) => sections.push(section) }
    : { register: (skill: typeof skills[number]) => skills.push(skill) } }
  registerGuidance(ctx)
  // No Skill has been loaded or even registered at this point.
  expect(sections.length).toBeGreaterThan(0)
  const names = new Set(tools.map(tool => tool.name))
  for (const name of (sections.map(section => section.text).join('\n') + tools.map(tool => tool.description).join('\n')).match(/\bpc_[a-z_]+\b/g) ?? []) {
    expect(names.has(name), `unavailable tool referenced in DSH guidance: ${name}`).toBe(true)
  }
  registerSkill(ctx)
  expect(skills[0].name).toBe('project-context')
})
