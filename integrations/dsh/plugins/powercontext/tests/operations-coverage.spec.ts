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
import { parse } from 'yaml'
import { describe, expect, it } from 'vitest'
import { parseOperations } from '../scripts/openapi-ops.mjs'
import { resolveOpenApiPath } from '../scripts/sync-openapi.mjs'
import { OPERATION_IDS, OPERATIONS } from '../src/operations.generated.ts'

function loadYamlDoc() {
  return parse(readFileSync(resolveOpenApiPath(), 'utf8'))
}

describe('operations coverage', () => {
  it('matches every OpenAPI operationId exactly', () => {
    const fromYaml = parseOperations(loadYamlDoc()).map((row) => row.operationId).sort()
    const generated = [...OPERATION_IDS].sort()
    expect(generated).toEqual(fromYaml)
  })

  it('records method, path, and location for each operation', () => {
    expect(OPERATIONS.get_liveness).toEqual({
      method: 'GET',
      path: '/health/live',
      location: null,
      scope: false,
    })
    expect(OPERATIONS.get_stats).toEqual({
      method: 'GET',
      path: '/v1/stats',
      location: 'query',
      scope: true,
    })
    expect(OPERATIONS.remember_memory).toEqual({
      method: 'POST',
      path: '/v1/memory/remember',
      location: 'body',
      scope: true,
    })
    expect(OPERATIONS.get_handoff_report.scope).toBe(false)
    expect(OPERATIONS.get_capabilities.location).toBeNull()
  })

  it('matches generated method, path, location, and scope for every operation', () => {
    for (const row of parseOperations(loadYamlDoc())) {
      expect(OPERATIONS[row.operationId]).toEqual({
        method: row.method,
        path: row.path,
        location: row.location,
        scope: row.scope,
      })
    }
  })
})
