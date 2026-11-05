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
