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

import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import { existsSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { test } from 'node:test'

test('the pet archive can be installed and imported without scripts or dsh-pet', t => {
  const temp = mkdtempSync(join(tmpdir(), 'pc-dsh-pet-package-'))
  t.after(() => rmSync(temp, { force: true, recursive: true }))
  const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm'
  const run = (args) => execFileSync(npm, args, {
    cwd: temp, encoding: 'utf8', env: { ...process.env, npm_config_audit: 'false' },
  })
  const externalArchive = process.env.POWERCONTEXT_DSH_PET_TARBALL
  const archive = externalArchive || join(temp, JSON.parse(run([
    'pack', resolve(import.meta.dirname, '..'), '--ignore-scripts', '--json', '--pack-destination', temp,
  ]))[0].filename)
  writeFileSync(join(temp, 'package.json'), '{"private":true}')
  run(['install', '--offline', '--ignore-scripts', '--no-package-lock', archive])
  const installed = join(temp, 'node_modules/powercontext-dsh-pet')
  const manifest = JSON.parse(readFileSync(join(installed, 'package.json')))
  assert.ok(existsSync(join(installed, manifest.dsh.bundle.patch)))
  assert.ok(!existsSync(join(temp, 'node_modules/dsh-pet')))
  execFileSync(process.execPath, ['--input-type=module', '--eval', [
    "import {apply, name} from 'powercontext-dsh-pet'",
    "if (typeof apply !== 'function' || name !== 'powercontext-dsh-pet') process.exit(1)",
  ].join(';')], { cwd: temp })
})
