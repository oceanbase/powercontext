/* Copyright (c) 2026 OceanBase. Licensed under the Apache License, Version 2.0. */

import { createHash } from 'node:crypto'
import { appendFileSync, readFileSync } from 'node:fs'
import { pathToFileURL } from 'node:url'
import { stampPublishManifest } from './stamp-version.mjs'

export async function verifyPublishedVersion(version, integrity, fetcher = fetch) {
  stampPublishManifest({}, version)
  const response = await fetcher(`https://registry.npmjs.org/powercontext-dsh/${encodeURIComponent(version)}`, {
    signal: AbortSignal.timeout(30000),
  })
  if (response.status === 404) return false
  if (!response.ok) throw new Error(`npm registry returned HTTP ${response.status}`)
  const published = await response.json()
  if (published.name !== 'powercontext-dsh' || published.version !== version || published.dist?.integrity !== integrity) {
    throw new Error(`powercontext-dsh@${version} already exists with a different artifact`)
  }
  return true
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const [, , archive, version, required] = process.argv
  const integrity = 'sha512-' + createHash('sha512').update(readFileSync(archive)).digest('base64')
  const published = await verifyPublishedVersion(version, integrity)
  if (required === '--required' && !published) throw new Error(`powercontext-dsh@${version} is not visible on npm`)
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, `published=${published}\n`)
  console.log(published ? `Verified powercontext-dsh@${version} integrity` : `powercontext-dsh@${version} is unpublished`)
}
