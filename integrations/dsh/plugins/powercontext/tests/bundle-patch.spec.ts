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
import { resolveConfig } from '../src/config.ts'
import { shippedConfig } from './shipped-config.ts'

describe('bundle patch', () => {
  // The patch's values take precedence over the defaults in src/config.ts, so the two must not drift apart.
  it('ships the same settings as the plugin defaults', () => {
    const shipped = shippedConfig()
    expect(Object.keys(shipped)).toEqual(expect.arrayContaining(['timeoutMs', 'requestTimeoutMs', 'maxBytes']))
    const resolved = resolveConfig(shipped, {}) as unknown as Record<string, unknown>
    const defaults = resolveConfig({}, {}) as unknown as Record<string, unknown>
    for (const key of Object.keys(shipped)) expect([key, resolved[key]]).toEqual([key, defaults[key]])
  })
})
