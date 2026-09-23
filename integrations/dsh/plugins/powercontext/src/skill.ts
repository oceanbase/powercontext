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

import { requireService } from './dsh-service.ts'
import { DOMAIN_SKILLS } from './domain-skills.ts'

import { GUIDANCE } from './guidance.ts'
export { GUIDANCE } from './guidance.ts'

export function registerGuidance(ctx: { get: (name: string) => unknown }): void {
  const systemPrompt = requireService<{
    section: (section: { name: string; order: number; text: string }) => unknown
  }>(ctx, 'systemPrompt')
  systemPrompt.section({
    name: 'tool:powercontext',
    order: 120,
    text: GUIDANCE,
  })
}

export function registerSkill(ctx: { get: (name: string) => unknown }): void {
  const skills = requireService<{
    register: (skill: {
      name: string
      description: string
      source: string
      content: string
      whenToUse?: string
    }) => unknown
  }>(ctx, 'skills')
  skills.register({
    name: 'powercontext-project-context',
    description: 'PowerContext Memory search/save, inventory, handoff and candidate review. Route to focused workflows when needed; ordinary coding and current-context summaries need no Skill detour.',
    source: 'runtime',
    whenToUse: 'Use when continuing work across sessions, recalling prior decisions, preparing a handoff, or maintaining durable memory.',
    content: GUIDANCE,
  })
  for (const skill of DOMAIN_SKILLS) skills.register(skill)
}
