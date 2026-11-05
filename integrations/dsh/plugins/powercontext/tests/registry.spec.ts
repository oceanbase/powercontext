/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

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
