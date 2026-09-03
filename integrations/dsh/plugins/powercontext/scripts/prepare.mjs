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

import { existsSync } from 'node:fs'
import { spawnSync } from 'node:child_process'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const root = join(dirname(fileURLToPath(import.meta.url)), '..')
const built = join(root, 'lib', 'index.js')
const force = process.env.POWERCONTEXT_DSH_FORCE_BUILD === '1'

function run(file) {
  return spawnSync(process.execPath, [join(root, 'scripts', file)], {
    cwd: root,
    stdio: 'inherit',
  })
}

if (existsSync(built) && !force) {
  process.exit(0)
}

const tsdown = spawnSync('tsdown', { cwd: root, stdio: 'inherit', shell: true })
if (tsdown.status === 0) {
  const normalize = run('normalize-build-output.mjs')
  process.exit(normalize.status ?? 1)
}
if (existsSync(built)) {
  console.warn('powercontext-dsh: tsdown unavailable; using prebuilt lib/')
  process.exit(0)
}
process.exit(tsdown.status ?? 1)
