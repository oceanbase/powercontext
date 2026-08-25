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
import { deriveScopeId, normalizeGitRemote } from '../src/scope.ts'

describe('scope', () => {
  it('normalizes HTTPS and SCP remotes identically', () => {
    expect(normalizeGitRemote('https://github.com/oceanbase/powercontext.git')).toBe('github.com/oceanbase/powercontext')
    expect(normalizeGitRemote('git@github.com:oceanbase/powercontext.git')).toBe('github.com/oceanbase/powercontext')
  })

  it('prefers an explicit scope', async () => {
    await expect(deriveScopeId('/tmp/project', { configuredScopeId: 'project:test' })).resolves.toBe('project:test')
  })

  it('derives a git scope from the project remote', async () => {
    const git = async (_cwd: string, args: string[]) => args.includes('--show-toplevel')
      ? '/tmp/project'
      : 'git@github.com:oceanbase/powercontext.git'
    await expect(deriveScopeId('/tmp/project/subdir', { git })).resolves.toBe('git:github.com/oceanbase/powercontext')
  })
})
