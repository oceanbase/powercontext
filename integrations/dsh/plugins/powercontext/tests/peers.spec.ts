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

import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { profileNodeModulesDir } from '../src/peers.ts'

describe('profileNodeModulesDir', () => {
  it('resolves peer modules under the web profile by default', () => {
    expect(profileNodeModulesDir({ DSH_HOME: '/tmp/dsh-home' } as NodeJS.ProcessEnv)).toBe(
      join('/tmp/dsh-home', 'profiles', 'web', 'node_modules'),
    )
  })

  it('honors DSH_PROFILE when the host uses a non-default profile', () => {
    expect(profileNodeModulesDir({
      DSH_HOME: '/tmp/dsh-home',
      DSH_PROFILE: 'desktop',
    } as NodeJS.ProcessEnv)).toBe(join('/tmp/dsh-home', 'profiles', 'desktop', 'node_modules'))
  })
})
