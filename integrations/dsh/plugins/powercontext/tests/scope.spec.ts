import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { deriveScopeId, normalizeGitRemote } from '../src/scope.ts'

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
})
