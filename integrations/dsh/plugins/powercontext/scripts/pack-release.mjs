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
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stampPublishManifest } from './stamp-version.mjs'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const manifestPath = join(root, 'package.json')
const original = readFileSync(manifestPath, 'utf8')
const current = JSON.parse(original)
const version = process.argv[2]?.trim() || current.version

try {
  const packed = stampPublishManifest(current, version)
  writeFileSync(manifestPath, `${JSON.stringify(packed, null, 2)}\n`)
  const result = spawnSync('pnpm', ['pack'], { cwd: root, stdio: 'inherit', shell: true })
  if (result.status !== 0) process.exit(result.status ?? 1)
} finally {
  writeFileSync(manifestPath, original)
}
