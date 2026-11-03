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

import { readFileSync } from 'node:fs'
import { describe, expect, it } from 'vitest'
import { containsSecret, scrubSecrets } from '../src/secrets.ts'

const { cases } = JSON.parse(readFileSync(
  new URL('../../../../../tests/fixtures/automatic_prompt_privacy.json', import.meta.url), 'utf8',
)) as { cases: { id: string; prompt: string; sensitive: boolean; authorization?: string }[] }

describe('shared automatic prompt privacy policy', () => {
  it.each(cases)('$id', ({ prompt, sensitive, authorization }) => {
    expect(containsSecret(prompt, authorization)).toBe(sensitive)
    const query = scrubSecrets(prompt, authorization)
    if (sensitive) {
      expect(query).not.toContain('synthetic-value-2084')
      expect(query).not.toContain('synthetic_value_2084')
    } else {
      expect(query).toBe(prompt)
    }
  })
})
