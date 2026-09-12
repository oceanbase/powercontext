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
import { resolveConfig } from '../src/config.ts'

describe('Pi configuration', () => {
  it('reads PowerContext Pi environment overrides', () => {
    expect(resolveConfig({
      POWERCONTEXT_PI_BASE_URL: 'https://memory.example.test/',
      POWERCONTEXT_PI_SCOPE_ID: 'project:demo',
      POWERCONTEXT_PI_AUTHORIZATION: 'Bearer token',
      POWERCONTEXT_PI_CAPTURE_PROMPTS: 'false',
      POWERCONTEXT_PI_REQUEST_TIMEOUT_MS: '1200',
      POWERCONTEXT_PI_HTTP_BUDGET_MS: '5000',
      POWERCONTEXT_PI_MAX_BYTES: '12000',
    })).toEqual({
      baseUrl: 'https://memory.example.test',
      allowInsecureHttp: false,
      scopeId: 'project:demo',
      authorization: 'Bearer token',
      capturePrompts: false,
      requestTimeoutMs: 1200,
      httpBudgetMs: 5000,
      maxBytes: 12000,
      flushOnCapture: false,
      flushMaxCalls: 4,
      diagnostics: 'off',
    })
  })

  it('keeps diagnostics silent unless a sink is configured', () => {
    expect(resolveConfig({}).diagnostics).toBe('off')
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: 'stderr' }).diagnostics).toBe('stderr')
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: '/tmp/pc.log' }).diagnostics).toBe('/tmp/pc.log')
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: '~/pc.log', HOME: '/home/demo' }).diagnostics).toBe(
      join('/home/demo', 'pc.log'),
    )
  })

  it('accepts the sink keywords in any case, like the boolean flags', () => {
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: 'STDERR' }).diagnostics).toBe('stderr')
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: 'Off' }).diagnostics).toBe('off')
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: ' stderr ' }).diagnostics).toBe('stderr')
  })

  it('treats only absolute or ~/ paths as a file sink and stays silent for anything else', () => {
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: 'pc.log' }).diagnostics).toBe('off')
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: 'STDER' }).diagnostics).toBe('off')
    expect(resolveConfig({ POWERCONTEXT_PI_DIAGNOSTICS: './logs/pc.log' }).diagnostics).toBe('off')
  })
})


it('opts into standard text only for explicit assembly configuration', () => {
  expect(resolveConfig({}).contextAssembly).toBeUndefined()
  for (const assembly of [
    {},
    { sections: [] },
    { sections: [{ family: 'experience', limit: 2 }] },
    { sections: [
      { family: 'profile', limit: 1 },
      { family: 'topic-memory', limit: 2 },
      { family: 'memory', limit: 3 },
      { family: 'experience', limit: 2 },
    ] },
  ]) {
    expect(resolveConfig({ POWERCONTEXT_PI_CONTEXT_ASSEMBLY: JSON.stringify(assembly) }).contextAssembly).toEqual(assembly)
  }
  for (const value of ['null', '[]', 'private-invalid-input']) {
    expect(() => resolveConfig({ POWERCONTEXT_PI_CONTEXT_ASSEMBLY: value })).toThrow('PowerContext context assembly must be a JSON object')
  }
})
