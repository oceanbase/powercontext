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
import { boundedRecallQuery, MAX_QUERY_BYTES, MAX_QUERY_CHARACTERS } from '../src/prepared-context.ts'

interface Segment { text: string; repeat?: number }

const fixture = JSON.parse(readFileSync(
  new URL('../../../../../tests/fixtures/recall_query_bound.json', import.meta.url), 'utf8',
)) as { max_characters: number; max_bytes: number; cases: { id: string; prompt: Segment[]; query: Segment[] }[] }

function expand(segments: Segment[]): string {
  return segments.map(({ text, repeat = 1 }) => text.repeat(repeat)).join('')
}

describe('shared recall query bound', () => {
  it('uses the shared bounds', () => {
    expect([MAX_QUERY_CHARACTERS, MAX_QUERY_BYTES]).toEqual([fixture.max_characters, fixture.max_bytes])
  })

  it.each(fixture.cases)('$id', ({ prompt, query }) => {
    expect(boundedRecallQuery(expand(prompt))).toBe(expand(query))
  })
})
