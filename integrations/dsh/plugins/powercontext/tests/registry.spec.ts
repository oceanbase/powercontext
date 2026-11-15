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
import { verifyPublishedVersion } from '../scripts/check-registry.mjs'

describe('published release verification', () => {
  const integrity = 'sha512-test-artifact'
  it('accepts an identical published version for reruns', async () => {
    const fetcher = async () => Response.json({ name: 'powercontext-dsh', version: '1.5.0', dist: { integrity } })
    expect(await verifyPublishedVersion('1.5.0', integrity, fetcher)).toBe(true)
  })
  it('refuses a version occupied by a different artifact', async () => {
    const fetcher = async () => Response.json({ name: 'powercontext-dsh', version: '1.5.0', dist: { integrity: 'other' } })
    await expect(verifyPublishedVersion('1.5.0', integrity, fetcher)).rejects.toThrow(/different artifact/)
  })
  it('distinguishes an unpublished version from an unavailable registry', async () => {
    expect(await verifyPublishedVersion('1.5.0', integrity, async () => new Response('', { status: 404 }))).toBe(false)
    await expect(verifyPublishedVersion('1.5.0', integrity, async () => new Response('', { status: 503 })))
      .rejects.toThrow(/503/)
  })
})
