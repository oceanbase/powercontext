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

  it('uses the Server default when cwd is absent', async () => {
    const request = vi.fn().mockResolvedValue({ value: { scope_id: 'default-scope' } })
    await expect(resolveScopeId({ request } as never, undefined)).resolves.toBe('default-scope')
    expect(request).toHaveBeenCalledWith('resolve_scope_binding', {
      explicit_scope_id: undefined,
      binding_keys: [],
    })
  })
})
