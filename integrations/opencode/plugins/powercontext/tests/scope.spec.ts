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
import { resolveScopeId } from '../src/scope.ts'

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
  it('resolves session, workspace, and default precedence on the Server', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'scp_00000000000000000000000000' } })
    const client = { request } as never

    await expect(resolveScopeId(client, {
      cwd: '/workspace/powercontext',
      sessionID: 'session-a',
      configuredScopeId: 'explicit-scope',
    })).resolves.toBe('scp_00000000000000000000000000')
    const firstCall = request.mock.calls[0]
    expect(firstCall).toBeDefined()
    if (!firstCall) throw new Error('resolve_scope_binding was not called')
    const [operation, input] = firstCall
    expect(operation).toBe('resolve_scope_binding')
    expect(input.explicit_scope_id).toBe('explicit-scope')
    expect(input.binding_keys[0]).toEqual({ integration: 'opencode', kind: 'session', external_id: 'session-a' })
    expect(input.binding_keys[1]).toMatchObject({ integration: 'opencode', kind: 'workspace' })
    expect(input.binding_keys[1].external_id).not.toContain('/workspace/powercontext')
  })

  it('fixes a new session to its resolved Scope', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'scope-a' } })
    await resolveScopeId({ request } as never, {
      cwd: '/workspace',
      sessionID: 'session-a',
      persistSession: true,
    })
    expect(request.mock.calls[1]).toEqual([
      'set_scope_binding',
      {
        key: { integration: 'opencode', kind: 'session', external_id: 'session-a' },
        scope_id: 'scope-a',
      },
      undefined,
    ])
  })

  it('omits empty binding keys instead of sending blank identifiers', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'scope-a' } })

    await expect(resolveScopeId({ request } as never, {
      cwd: '',
      sessionID: '',
      configuredScopeId: 'explicit-scope',
      persistSession: true,
    })).resolves.toBe('scope-a')

    expect(request.mock.calls).toEqual([
      ['resolve_scope_binding', { explicit_scope_id: 'explicit-scope', binding_keys: [] }, undefined],
    ])
  })

  it('sends only the workspace key on a route without a session', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'scope-a' } })

    await resolveScopeId({ request } as never, {
      cwd: '/workspace/powercontext',
      configuredScopeId: 'explicit-scope',
    })

    const firstCall = request.mock.calls[0]
    expect(firstCall).toBeDefined()
    if (!firstCall) throw new Error('resolve_scope_binding was not called')
    const input = firstCall[1]
    expect(input.binding_keys).toHaveLength(1)
    expect(input.binding_keys[0]).toMatchObject({ integration: 'opencode', kind: 'workspace' })
    expect(input.binding_keys[0].external_id).toHaveLength(64)
  })

  it('forwards the abort signal and trims the resolved Scope', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: '  scope-a  ' } })
    const controller = new AbortController()

    await expect(resolveScopeId({ request } as never, {
      cwd: '/workspace/powercontext',
      sessionID: 'session-a',
      persistSession: true,
    }, controller.signal)).resolves.toBe('scope-a')

    expect(request.mock.calls[0]?.[2]).toBe(controller.signal)
    expect(request.mock.calls[1]?.[2]).toBe(controller.signal)
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
      const resolveIn = (cwd: string) => resolveScopeId({ request } as never, { cwd })

      await expect(resolveIn(worktreeSub)).resolves.toBe('checkout-scope')
      // The first key is still the hash of path.resolve(cwd), so bindings made before keep matching.
      expect(request.mock.calls[0]?.[1].binding_keys).toEqual([
        { integration: 'opencode', kind: 'workspace', external_id: pathHash(worktreeSub) },
        { integration: 'opencode', kind: 'workspace', external_id: pathHash(checkoutSub) },
      ])
      bindings.set(pathHash(worktreeSub), 'worktree-scope')
      await expect(resolveIn(worktreeSub)).resolves.toBe('worktree-scope')
      await expect(resolveIn(checkoutSub)).resolves.toBe('checkout-scope')
      expect(request.mock.calls.at(-1)?.[1].binding_keys).toHaveLength(1)
    } finally {
      rmSync(root, { recursive: true, force: true })
    }
  })
})
