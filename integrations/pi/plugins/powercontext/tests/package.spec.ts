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
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { loadSkillsFromDir, formatSkillsForPrompt } from '@earendil-works/pi-coding-agent'

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

describe('PowerContext Pi package', () => {
  it('advertises a powercontext-project-context skill for Memory and Handoff work', () => {
    const manifest = JSON.parse(readFileSync(join(packageRoot, 'package.json'), 'utf8')) as {
      pi?: { skills?: string[] }
    }
    const skill = readFileSync(join(packageRoot, 'skills', 'powercontext-project-context', 'SKILL.md'), 'utf8')

    expect(manifest.pi?.skills).toContain('./skills')
    const discovered = loadSkillsFromDir({ dir: join(packageRoot, 'skills'), source: 'powercontext-test' })
    expect(discovered.diagnostics).toEqual([])
    const entry = discovered.skills.find(skill => skill.name === 'powercontext-project-context')!
    expect(entry).toBeDefined()
    expect(formatSkillsForPrompt([entry])).toContain(entry.description)
    for (const match of skill.matchAll(/\[[^\]]*\]\((references\/[^)]+)\)/g)) {
      expect(readFileSync(join(entry.baseDir, match[1]), 'utf8').trim()).not.toBe('')
    }
  })
})
