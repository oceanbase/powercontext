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

import { Context, Service } from '@deepseek-ai/cordis'
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest'
import { PowerContextClient } from '../../src/client.ts'
import * as plugin from '../../src/index.ts'
import { GUIDANCE } from '../../src/skill.ts'
import { PROJECT_CONTEXT_SKILL } from '../../src/skill-body.ts'
import { startPowerContextServer } from '../../scripts/e2e-server.mjs'

vi.mock('../../src/peers.ts', () => ({
  loadPeer: async (specifier: string) => {
    if (specifier === '@deepseek-ai/dsh-tools') {
      return { defineTool: (definition: Record<string, unknown>) => definition }
    }
    if (specifier === '@deepseek-ai/dsh-llm') {
      return { createUserMessage: (input: unknown) => input }
    }
    throw new Error(`unexpected peer ${specifier}`)
  },
}))

const SCOPE_ID = 'project:dsh-e2e'
const TEXT = 'Keep the DSH plugin on the public HTTP contract.'
const REQUIRED_SERVICES = ['tools', 'agents', 'commands', 'skills', 'systemPrompt'] as const

type RequiredService = typeof REQUIRED_SERVICES[number]
type PcHandler = (invocation: {
  rawInput: string
  signal: AbortSignal
  agent: { session: { header: { cwd?: string } } }
}) => Promise<{ kind: string; text: string }>

type CommandDefinition = { name: string; handler: PcHandler }
type SkillRegistration = { name: string; content: string }
type PromptSection = { name: string; text: string }

type Registrations = {
  tools: Record<string, unknown>[]
  commands: CommandDefinition[]
  skills: SkillRegistration[]
  sections: PromptSection[]
}

abstract class RegistrationService<T> extends Service {
  private readonly registrations: T[]

  constructor(ctx: Context, name: string, registrations: T[]) {
    super(ctx, name)
    this.registrations = registrations
  }

  protected registerOwned(value: T, label: string): () => void {
    return this.ctx.effect(() => {
      this.registrations.push(value)
      return () => {
        const index = this.registrations.indexOf(value)
        if (index >= 0) this.registrations.splice(index, 1)
      }
    }, label)
  }
}

class ToolsService extends RegistrationService<Record<string, unknown>> {
  constructor(ctx: Context, registrations: Record<string, unknown>[]) {
    super(ctx, 'tools', registrations)
  }

  register(tool: Record<string, unknown>): () => void {
    return this.registerOwned(tool, 'tools.register()')
  }
}

class AgentsService extends Service {
  constructor(ctx: Context) {
    super(ctx, 'agents')
  }
}

class CommandsService extends RegistrationService<CommandDefinition> {
  constructor(ctx: Context, registrations: CommandDefinition[]) {
    super(ctx, 'commands', registrations)
  }

  register(definition: CommandDefinition): () => void {
    return this.registerOwned(definition, 'commands.register()')
  }
}

class SkillsService extends RegistrationService<SkillRegistration> {
  constructor(ctx: Context, registrations: SkillRegistration[]) {
    super(ctx, 'skills', registrations)
  }

  register(skill: SkillRegistration): () => void {
    return this.registerOwned(skill, 'skills.register()')
  }
}

class SystemPromptService extends RegistrationService<PromptSection> {
  constructor(ctx: Context, registrations: PromptSection[]) {
    super(ctx, 'systemPrompt', registrations)
  }

  section(section: PromptSection): () => void {
    return this.registerOwned(section, 'systemPrompt.section()')
  }
}

function emptyRegistrations(): Registrations {
  return { tools: [], commands: [], skills: [], sections: [] }
}

class PluginHarness {
  readonly ctx = new Context()
  readonly registrations = emptyRegistrations()
  readonly fiber
  private readonly providers = new Map<RequiredService, ReturnType<Context['plugin']>>()

  constructor(baseUrl: string, scopeId: string) {
    this.fiber = this.ctx.plugin(plugin, {
      baseUrl,
      scopeId,
      requestTimeoutMs: 5000,
      capturePrompts: true,
    })
  }

  async provide(name: RequiredService): Promise<void> {
    if (this.providers.has(name)) throw new Error(`service ${name} is already provided`)
    let provider: ReturnType<Context['plugin']>
    if (name === 'tools') {
      provider = this.ctx.plugin(ToolsService, this.registrations.tools)
    } else if (name === 'agents') {
      provider = this.ctx.plugin(AgentsService)
    } else if (name === 'commands') {
      provider = this.ctx.plugin(CommandsService, this.registrations.commands)
    } else if (name === 'skills') {
      provider = this.ctx.plugin(SkillsService, this.registrations.skills)
    } else {
      provider = this.ctx.plugin(SystemPromptService, this.registrations.sections)
    }
    this.providers.set(name, provider)
    await provider
  }

  async provideAll(): Promise<void> {
    for (const name of REQUIRED_SERVICES) await this.provide(name)
    await this.fiber
  }

  async remove(name: RequiredService): Promise<void> {
    const provider = this.providers.get(name)
    if (!provider) throw new Error(`service ${name} is not provided`)
    this.providers.delete(name)
    await provider.dispose()
    await this.fiber.await()
  }

  commandHandler(): PcHandler {
    const command = this.registrations.commands.find(item => item.name === 'pc')
    if (!command) throw new Error('expected /pc handler')
    return command.handler
  }

  async dispose(): Promise<void> {
    await this.fiber.dispose()
    for (const provider of [...this.providers.values()].reverse()) {
      await provider.dispose()
    }
    this.providers.clear()
  }
}

function registrationCounts(registrations: Registrations) {
  return {
    tools: registrations.tools.length,
    commands: registrations.commands.length,
    skills: registrations.skills.length,
    sections: registrations.sections.length,
  }
}

function invokePc(handler: PcHandler, rawInput: string) {
  return handler({
    rawInput,
    signal: AbortSignal.timeout(5000),
    agent: { session: { header: {} } },
  })
}

function parseCommandPayload(text: string): { ok: boolean; data?: unknown } {
  return JSON.parse(text) as { ok: boolean; data?: unknown }
}

async function withHarness<T>(
  baseUrl: string,
  callback: (harness: PluginHarness) => Promise<T>,
): Promise<T> {
  const harness = new PluginHarness(baseUrl, SCOPE_ID)
  try {
    return await callback(harness)
  } finally {
    await harness.dispose()
  }
}

describe('plugin HTTP call-through without a model', () => {
  let server
  let client

  beforeAll(async () => {
    server = await startPowerContextServer()
    client = new PowerContextClient({
      baseUrl: server.baseUrl,
      requestTimeoutMs: 5000,
    })
  }, 60_000)

  afterAll(async () => {
    await server?.stop()
  })

  it('reaches liveness and readiness without inference', async () => {
    const live = await client.request('get_liveness')
    expect(live.kind).toBe('json')
    expect(live.value).toMatchObject({ status: 'ok' })
    const ready = await client.request('get_readiness')
    expect(ready.kind).toBe('json')
    expect(['ready', 'degraded']).toContain(ready.value.status)
  })

  it('remembers, searches, prepares, and captures over HTTP', async () => {
    const remembered = await client.request('remember_memory', {
      scope_id: SCOPE_ID,
      kind: 'decision',
      text: TEXT,
    })
    expect(remembered.kind).toBe('json')

    const found = await client.request('search_memory', {
      scope_id: SCOPE_ID,
      query: 'DSH plugin HTTP contract',
    })
    expect(found.kind).toBe('json')
    const hits = found.value.hits
    expect(Array.isArray(hits)).toBe(true)
    expect(hits.some((hit) => hit.text === TEXT)).toBe(true)

    const prepared = await client.request('prepare_context', {
      scope_id: SCOPE_ID,
      query: 'DSH plugin HTTP contract',
    })
    expect(prepared.kind).toBe('json')
    expect(typeof prepared.value.content === 'string' || prepared.value.content === null).toBe(true)

    const captured = await client.request('capture_content_source', {
      scope_id: SCOPE_ID,
      source_id: 'dsh-e2e-turn-1',
      content: 'Call through the plugin client without a model.',
      metadata: { origin: 'dsh', event: 'e2e' },
    })
    expect(captured.kind).toBe('json')
  })

  it('declares every DSH service required by apply', () => {
    expect(plugin.inject).toEqual(REQUIRED_SERVICES)
  })

  it('waits for every dependency before mounting all plugin surfaces', async () => {
    await withHarness(server.baseUrl, async (harness) => {
      await Promise.resolve()
      expect(registrationCounts(harness.registrations)).toEqual({
        tools: 0, commands: 0, skills: 0, sections: 0,
      })

      for (const name of REQUIRED_SERVICES.slice(0, -1)) {
        await harness.provide(name)
        expect(registrationCounts(harness.registrations)).toEqual({
          tools: 0, commands: 0, skills: 0, sections: 0,
        })
      }

      await harness.provide(REQUIRED_SERVICES.at(-1)!)
      await harness.fiber
      expect(harness.registrations.tools.length).toBeGreaterThan(0)
      expect(harness.registrations.commands.map(item => item.name)).toEqual(['pc'])
      expect(harness.registrations.skills).toEqual([expect.objectContaining({
        name: 'project-context',
        content: PROJECT_CONTEXT_SKILL,
      })])
      expect(harness.registrations.sections).toEqual([expect.objectContaining({
        name: 'tool:powercontext',
        text: GUIDANCE,
      })])
      const status = await invokePc(harness.commandHandler(), '')
      expect(status.kind).toBe('success')
      expect(status.text).toContain(`scope=${SCOPE_ID}`)
    })
  })

  it('runs /pc doctor, stats, capabilities, and review against the live server', async () => {
    await withHarness(server.baseUrl, async (harness) => {
      await harness.provideAll()
      const handler = harness.commandHandler()
      const doctor = await invokePc(handler, 'doctor')
      expect(doctor.kind).toBe('success')
      expect(parseCommandPayload(doctor.text).ok).toBe(true)

      const stats = await invokePc(handler, 'stats')
      expect(stats.kind).toBe('success')
      expect(parseCommandPayload(stats.text).ok).toBe(true)

      const capabilities = await invokePc(handler, 'capabilities')
      expect(capabilities.kind).toBe('success')
      expect(parseCommandPayload(capabilities.text).ok).toBe(true)

      const review = await invokePc(handler, 'review')
      expect(review.kind).toBe('success')
      expect(parseCommandPayload(review.text).ok).toBe(true)
    })
  })

  it('unmounts and restores every surface when a required service changes', async () => {
    await withHarness(server.baseUrl, async (harness) => {
      await harness.provideAll()
      const activeCounts = registrationCounts(harness.registrations)

      await harness.remove('commands')
      expect(registrationCounts(harness.registrations)).toEqual({
        tools: 0, commands: 0, skills: 0, sections: 0,
      })
      expect(harness.fiber.getEffects()).toEqual([])

      await harness.provide('commands')
      await harness.fiber
      expect(registrationCounts(harness.registrations)).toEqual(activeCounts)
      expect(harness.registrations.commands.map(item => item.name)).toEqual(['pc'])
    })
  })
})
