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

import { cpSync, mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import spawn from 'cross-spawn'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { stampPublishManifest } from './stamp-version.mjs'

const root = process.argv[4] ? resolve(process.argv[4]) : join(dirname(fileURLToPath(import.meta.url)), '..')
const current = JSON.parse(readFileSync(join(root, 'package.json'), 'utf8'))
const version = process.argv[2]?.trim() || current.version
const packed = stampPublishManifest(current, version)
const destination = resolve(process.argv[3] || join(root, 'dist'))
const staging = mkdtempSync(join(tmpdir(), 'powercontext-dsh-pack-'))

try {
  // Copy only published assets; never stamp or sanitize the checkout in place.
  for (const file of [...packed.files, 'README.md', 'LICENSE']) {
    const target = join(staging, file)
    mkdirSync(dirname(target), { recursive: true })
    cpSync(join(root, file), target, { recursive: true })
  }
  writeFileSync(join(staging, 'package.json'), `${JSON.stringify(packed, null, 2)}\n`)
  mkdirSync(destination, { recursive: true })
  const result = spawn.sync('npm', ['pack', '--ignore-scripts', '--json', '--pack-destination', destination], {
    cwd: staging, stdio: 'inherit',
  })
  if (result.error) throw result.error
  process.exitCode = result.status ?? 1
} finally {
  rmSync(staging, { recursive: true, force: true })
}
