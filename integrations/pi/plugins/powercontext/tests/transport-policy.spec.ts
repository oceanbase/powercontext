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
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'
import { resolveConfig } from '../src/config.ts'

// Single source of truth shared with the Python transport drift guard (tests/test_transport.py).
// Both suites drive the SAME host vectors through their production entry points, so a plugin that
// drifts from the shared 127.0.0.0/8 loopback contract fails in at least one language.
const vectorsPath = fileURLToPath(
  new URL('../../../../../tests/fixtures/transport_loopback_vectors.json', import.meta.url),
)
const vectors = JSON.parse(readFileSync(vectorsPath, 'utf8')) as {
  loopback: string[]
  non_loopback: string[]
}

describe('Pi transport policy', () => {
  it.each(vectors.loopback)('trusts a plaintext loopback bind for %s', (host) => {
    // WHATWG URL lowercases the host, so the normalized baseUrl echoes the lowercased authority.
    const config = resolveConfig({ POWERCONTEXT_PI_BASE_URL: `http://${host}:8000` })
    expect(config.baseUrl).toBe(`http://${host.toLowerCase()}:8000`)
  })

  it.each(vectors.non_loopback)('refuses a plaintext non-loopback bind for %s', (host) => {
    expect(() => resolveConfig({ POWERCONTEXT_PI_BASE_URL: `http://${host}:8000` })).toThrow(
      /must use HTTPS outside loopback/,
    )
  })
})
