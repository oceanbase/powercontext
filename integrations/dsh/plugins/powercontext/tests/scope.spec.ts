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

import { execFileSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { mkdirSync, mkdtempSync, realpathSync, rmSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { formatScopeRouting, resolveScopeId } from '../src/scope.ts'

type BindingInput = { binding_keys: { external_id: string }[] }

// A checkout and a linked worktree of it, each with the same subdirectory, under a real temporary directory.
function gitLayout(): { root: string; checkout: string; worktree: string } {
  const root = realpathSync(mkdtempSync(join(tmpdir(), 'pc-worktree-')))
  const identity = ['-c', 'user.name=test', '-c', 'user.email=test@example.com']
  const git = (...args: string[]) => execFileSync('git', [...identity, ...args], { stdio: 'ignore' })
  const checkout = join(root, 'checkout')
  const worktree = join(root, 'worktree')
  git('init', '-q', checkout)
  git('-C', checkout, 'commit', '-q', '--allow-empty', '-m', 'init')
  git('-C', checkout, 'worktree', 'add', '-q', worktree)
  mkdirSync(join(checkout, 'sub'))
  mkdirSync(join(worktree, 'sub'))
  return { root, checkout, worktree }
}

const pathHash = (path: string) => createHash('sha256').update(path).digest('hex')

describe('Scope binding', () => {
  it('formats host-resolved routing metadata without exposing the workspace path', () => {
    const text = formatScopeRouting('scope-a', 'C:/workspace/project')
    expect(text).toContain('"scope-a"')
    expect(text).toContain('workspace binding key')
    expect(text).not.toContain('C:/workspace/project')
    expect(text).toContain('Do not call mcp__powercontext__resolve_scope_binding')
  })

  it('delegates explicit, durable, and default precedence to the Server', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'scp_00000000000000000000000000' } })
    const client = { request } as never

    await expect(resolveScopeId(client, '/workspace', 'explicit-scope')).resolves.toBe(
      'scp_00000000000000000000000000',
    )
    const [operation, input] = request.mock.calls[0]
    expect(operation).toBe('resolve_scope_binding')
    expect(input.explicit_scope_id).toBe('explicit-scope')
    expect(input.binding_keys).toHaveLength(1)
    expect(input.binding_keys[0]).toMatchObject({ integration: 'dsh', kind: 'workspace' })
    expect(input.binding_keys[0].external_id).not.toContain('/workspace')
  })

  it('allows companion callers to refuse an implicit default Scope', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'project' } })
    await resolveScopeId({ request } as never, '/workspace', undefined, undefined, false)
    expect(request.mock.calls[0][1].allow_default).toBe(false)
  })

  it('uses the Server default when cwd is absent', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'default-scope' } })
    await expect(resolveScopeId({ request } as never, undefined)).resolves.toBe('default-scope')
    expect(request.mock.calls[0].slice(0, 2)).toEqual(['resolve_scope_binding', {
      explicit_scope_id: undefined,
      binding_keys: [],
    }])
  })

  it('resolves a linked worktree to its checkout binding until the worktree has its own', async () => {
    const { root, checkout, worktree } = gitLayout()
    try {
      const checkoutSub = join(checkout, 'sub')
      const worktreeSub = join(worktree, 'sub')
      const bindings = new Map<string, string>([[pathHash(checkoutSub), 'checkout-scope']])
      const request = vi.fn(async (_operation: string, input: BindingInput) => ({
        value: { scope_id: input.binding_keys.map(key => bindings.get(key.external_id)).find(Boolean) ?? 'default' },
      }))
      const resolveIn = (cwd: string) => resolveScopeId({ request } as never, cwd)

      await expect(resolveIn(worktreeSub)).resolves.toBe('checkout-scope')
      // The first key is still the hash of path.resolve(cwd), so bindings made before keep matching.
      expect(request.mock.calls[0]?.[1].binding_keys).toEqual([
        { integration: 'dsh', kind: 'workspace', external_id: pathHash(worktreeSub) },
        { integration: 'dsh', kind: 'workspace', external_id: pathHash(checkoutSub) },
      ])
      bindings.set(pathHash(worktreeSub), 'worktree-scope')
      await expect(resolveIn(worktreeSub)).resolves.toBe('worktree-scope')
      await expect(resolveIn(checkoutSub)).resolves.toBe('checkout-scope')
      expect(request.mock.calls.at(-1)?.[1].binding_keys).toHaveLength(1)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })

  it('sends only its own key from a checkout whose Git directory is separate', async () => {
    const root = realpathSync(mkdtempSync(join(tmpdir(), 'pc-separate-')))
    try {
      const checkout = join(root, 'checkout')
      mkdirSync(join(root, 'store'))
      execFileSync('git', ['init', '-q', '--separate-git-dir', join(root, 'store', '.git'), checkout], {
        stdio: 'ignore',
      })
      const request = vi.fn().mockResolvedValue({ value: { scope_id: 'default' } })
      await resolveScopeId({ request } as never, checkout)
      expect(request.mock.calls[0]?.[1].binding_keys).toEqual([
        { integration: 'dsh', kind: 'workspace', external_id: pathHash(checkout) },
      ])
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
