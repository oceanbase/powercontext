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

import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { deriveScopeId, normalizeGitRemote, sessionCwd } from '../src/scope.ts'

describe('normalizeGitRemote', () => {
  it('normalizes https, ssh, and scp remotes without credentials', () => {
    expect(normalizeGitRemote('https://user:token@github.com/org/repo.git')).toBe('github.com/org/repo')
    expect(normalizeGitRemote('ssh://git@github.com/org/repo.git')).toBe('github.com/org/repo')
    expect(normalizeGitRemote('git@github.com:org/repo.git')).toBe('github.com/org/repo')
  })

  it('returns undefined for unsupported remotes', () => {
    expect(normalizeGitRemote('')).toBeUndefined()
    expect(normalizeGitRemote('file:///tmp/repo')).toBeUndefined()
  })
})

describe('deriveScopeId', () => {
  it('uses an explicit configured id and hashes when it exceeds 256 characters', async () => {
    expect(await deriveScopeId('/tmp/project', { configuredScopeId: 'project:demo' })).toBe('project:demo')
    const long = `x`.repeat(300)
    expect(await deriveScopeId('/tmp/project', { configuredScopeId: long })).toBe(
      `sha256:${createHash('sha256').update(long).digest('hex')}`,
    )
  })

  it('derives git:host/path from origin', async () => {
    const git = async (_cwd: string, args: string[]) => {
      if (args[0] === 'rev-parse') return '/repo'
      if (args[0] === 'config') return 'https://github.com/acme/power.git'
      return undefined
    }
    expect(await deriveScopeId('/workspace', { git })).toBe('git:github.com/acme/power')
  })

  it('falls back to local hash when git is unavailable', async () => {
    const cwd = resolve('/tmp/no-git-project')
    const scope = await deriveScopeId(cwd, { git: async () => undefined })
    expect(scope).toBe(`local:${createHash('sha256').update(cwd).digest('hex')}`)
  })

  it('uses a configured scopeId even when session cwd is missing or blank', async () => {
    const git = async () => {
      throw new Error('git must not run when scopeId is configured')
    }
    expect(await deriveScopeId(undefined, { configuredScopeId: 'project:demo', git })).toBe('project:demo')
    expect(await deriveScopeId('', { configuredScopeId: 'project:demo', git })).toBe('project:demo')
    expect(await deriveScopeId('   ', { configuredScopeId: 'project:demo', git })).toBe('project:demo')
  })

  it('does not treat a missing or blank cwd as the process directory', async () => {
    const git = async () => undefined
    await expect(deriveScopeId(undefined, { git })).resolves.toBeUndefined()
    await expect(deriveScopeId('', { git })).resolves.toBeUndefined()
    await expect(deriveScopeId('   ', { git })).resolves.toBeUndefined()
  })
})

describe('sessionCwd', () => {
  it('treats missing and blank values as absent', () => {
    expect(sessionCwd(undefined)).toBeUndefined()
    expect(sessionCwd('')).toBeUndefined()
    expect(sessionCwd('   ')).toBeUndefined()
    expect(sessionCwd('/repo')).toBe('/repo')
  })
})
