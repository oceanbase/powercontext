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

import { readFileSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { fileURLToPath, pathToFileURL } from 'node:url'

const SEMVER = /^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$/

export function sanitizePublishManifest(manifest) {
  const packed = { ...manifest }
  delete packed.devDependencies
  delete packed.scripts
  return packed
}

export function stampPublishManifest(manifest, version) {
  const trimmed = version?.trim()
  if (!trimmed || !SEMVER.test(trimmed)) {
    throw new Error('usage: node scripts/stamp-version.mjs <semver>')
  }
  return { ...sanitizePublishManifest(manifest), version: trimmed }
}

function isMain() {
  const entry = process.argv[1]
  return Boolean(entry) && import.meta.url === pathToFileURL(entry).href
}

if (isMain()) {
  const version = process.argv[2]?.trim()
  const manifestPath = join(dirname(fileURLToPath(import.meta.url)), '..', 'package.json')
  const manifest = JSON.parse(readFileSync(manifestPath, 'utf8'))
  manifest.version = stampPublishManifest(manifest, version).version
  writeFileSync(manifestPath, `${JSON.stringify(manifest, null, 2)}\n`)
  console.log(`stamped version ${manifest.version}`)
}
