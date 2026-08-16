import { createHash } from 'node:crypto'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import { deriveScopeId, normalizeGitRemote } from '../src/scope.ts'

describe('Pi project scope', () => {
  it('uses the normalized Git remote and never retains credentials', async () => {
    expect(normalizeGitRemote('https://token@github.com/acme/powercontext.git')).toBe('github.com/acme/powercontext')
    const git = async (_cwd: string, args: string[]) => {
      if (args[0] === 'rev-parse') return '/workspace/powercontext'
      if (args[0] === 'config') return 'git@github.com:acme/powercontext.git'
      return undefined
    }
    await expect(deriveScopeId('/workspace/powercontext', { git })).resolves.toBe('git:github.com/acme/powercontext')
  })

  it('uses a bounded explicit scope and falls back to the project path', async () => {
    await expect(deriveScopeId('/workspace/powercontext', { configuredScopeId: 'project:demo' })).resolves.toBe('project:demo')
    const cwd = resolve('/tmp/no-git-project')
    await expect(deriveScopeId(cwd, { git: async () => undefined })).resolves.toBe(
      `local:${createHash('sha256').update(cwd).digest('hex')}`,
    )
  })
})
