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
import { registerGuidance, registerSkill } from '../src/skill.ts'

it('exposes native MCP guidance', () => {
  const sections: Array<{ text: string }> = []
  const skills: Array<{ name: string; description: string; content: string }> = []
  const ctx = { get: (name: string) => name === 'systemPrompt'
    ? { section: (section: { text: string }) => sections.push(section) }
    : { register: (skill: typeof skills[number]) => skills.push(skill) } }
  registerGuidance(ctx)
  expect(sections).toHaveLength(1)
  const names = new Set(sections[0].text.match(/\bmcp__powercontext__[a-z_]+\b/g) ?? [])
  expect(names.size).toBeGreaterThan(0)
  expect(names.has('mcp__powercontext__search_memory')).toBe(true)
  expect(names.has('mcp__powercontext__resolve_scope_binding')).toBe(true)
  expect(names.has('mcp__powercontext__handoff_current_work')).toBe(true)
  expect([...names].every(name => name.startsWith('mcp__powercontext__'))).toBe(true)
  registerSkill(ctx)
  expect(skills.some(skill => skill.name === 'powercontext-project-context')).toBe(true)
  for (const name of ['powercontext-memory', 'powercontext-handoff', 'powercontext-review']) {
    const skill = skills.find(item => item.name === name)
    expect(skill, `router points to unavailable runtime Skill: ${name}`).toBeDefined()
    for (const tool of skill!.content.match(/\bmcp__powercontext__[a-z_]+\b/g) ?? []) {
      expect(tool.startsWith('mcp__powercontext__'), tool).toBe(true)
    }
  }
  const handoff = skills.find(skill => skill.name === 'powercontext-handoff')!.content
  expect(handoff).toContain('top-level `draft`')
  expect(handoff).toContain('complete native finalization result unchanged')
  expect(sections[0].text).toContain('complete native finalize_handoff result unchanged')
  for (const guidance of [sections[0].text, handoff]) {
    expect(guidance).not.toMatch(/data\.draft|finalize(?:_handoff)?\.data/)
  }
})
