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

import { describe, expect, it } from 'vitest'
import { PowerContextClient } from '../src/client.ts'
import { registerCommands } from '../src/commands.ts'
import { resolveConfig } from '../src/config.ts'
import { requireService } from '../src/dsh-service.ts'
import { inject } from '../src/index.ts'
import type { PluginRuntime } from '../src/invoke.ts'
import { GUIDANCE, registerGuidance, registerSkill } from '../src/skill.ts'
import { PROJECT_CONTEXT_SKILL } from '../src/skill-body.ts'

function runtime(): PluginRuntime {
  const config = resolveConfig({ baseUrl: 'http://127.0.0.1:8000' })
  return {
    client: new PowerContextClient({ baseUrl: config.baseUrl, requestTimeoutMs: 1000, fetch: async () => new Response('{}') }),
    config,
    resolveScope: async () => 'project:demo',
    log: () => undefined,
  }
}

describe('plugin inject contract', () => {
  it('declares every DSH service the plugin registers against', () => {
    expect(inject).toEqual(['tools', 'agents', 'commands', 'skills', 'systemPrompt'])
  })
})

describe('requireService', () => {
  it('returns the named service when present', () => {
    expect(requireService({ get: (name) => name === 'commands' ? { ok: true } : undefined }, 'commands')).toEqual({ ok: true })
  })

  it('rejects a missing service instead of returning undefined', () => {
    expect(() => requireService({ get: () => undefined }, 'commands')).toThrow(
      'powercontext-dsh requires the "commands" service',
    )
  })
})

describe('registerCommands', () => {
  it('fails loudly when the commands service is missing', () => {
    expect(() => registerCommands({ get: () => undefined }, runtime())).toThrow(/commands/)
  })

  it('registers /pc when the commands service is present', () => {
    const registered: Array<{ name: string }> = []
    registerCommands({
      get: (name) => name === 'commands'
        ? { register: (definition: { name: string }) => registered.push(definition) }
        : undefined,
    }, runtime())
    expect(registered.map((item) => item.name)).toEqual(['pc'])
  })
})

describe('registerSkill', () => {
  it('fails loudly when the skills service is missing', () => {
    expect(() => registerSkill({ get: () => undefined })).toThrow(/skills/)
  })

  it('registers project-context when the skills service is present', () => {
    const registered: Array<{ name: string; content: string; source: string }> = []
    registerSkill({
      get: (name) => name === 'skills'
        ? { register: (skill: { name: string; content: string; source: string }) => registered.push(skill) }
        : undefined,
    })
    expect(registered).toEqual([expect.objectContaining({
      name: 'project-context',
      source: 'runtime',
      content: PROJECT_CONTEXT_SKILL,
    })])
  })
})

describe('plugin surface mount', () => {
  it('registers /pc, skill, and guidance together when all services exist', () => {
    const commands: Array<{ name: string }> = []
    const skills: Array<{ name: string }> = []
    const sections: Array<{ name: string }> = []
    const ctx = {
      get: (name: string) => {
        if (name === 'commands') return { register: (definition: { name: string }) => commands.push(definition) }
        if (name === 'skills') return { register: (skill: { name: string }) => skills.push(skill) }
        if (name === 'systemPrompt') return { section: (section: { name: string }) => sections.push(section) }
        return undefined
      },
    }
    registerCommands(ctx, runtime())
    registerSkill(ctx)
    registerGuidance(ctx)
    expect(commands.map((item) => item.name)).toEqual(['pc'])
    expect(skills.map((item) => item.name)).toEqual(['project-context'])
    expect(sections.map((item) => item.name)).toEqual(['tool:powercontext'])
  })
})

describe('registerGuidance', () => {
  it('fails loudly when the systemPrompt service is missing', () => {
    expect(() => registerGuidance({ get: () => undefined })).toThrow(/systemPrompt/)
  })

  it('registers PowerContext guidance when the systemPrompt service is present', () => {
    const sections: Array<{ name: string; text: string }> = []
    registerGuidance({
      get: (name) => name === 'systemPrompt'
        ? { section: (section: { name: string; text: string }) => sections.push(section) }
        : undefined,
    })
    expect(sections).toEqual([expect.objectContaining({
      name: 'tool:powercontext',
      text: GUIDANCE,
    })])
  })
})
