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

import { describe, expect, it, vi } from 'vitest'
import { resolveScopeId } from '../src/scope.ts'

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
})
