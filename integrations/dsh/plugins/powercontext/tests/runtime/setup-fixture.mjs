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

import { execFile } from 'node:child_process'
import { chmodSync, mkdirSync, writeFileSync } from 'node:fs'
import { delimiter, join } from 'node:path'
import { promisify } from 'node:util'
import { defaultPowerContextRoot } from '../../scripts/e2e-server.mjs'
import { dshBin } from './fixture.mjs'

export async function installIntoCleanHome(home) {
  const bin = join(home, 'bin')
  mkdirSync(bin)
  const windows = process.platform === 'win32'
  const launcher = join(bin, windows ? 'dsh.cmd' : 'dsh')
  const quote = value => "'" + value.replaceAll("'", "'\"'\"'") + "'"
  writeFileSync(launcher, windows
    ? '@echo off\r\n"' + process.execPath + '" "' + dshBin + '" %*\r\n'
    : '#!/bin/sh\nexec ' + quote(process.execPath) + ' ' + quote(dshBin) + ' "$@"\n')
  if (!windows) chmodSync(launcher, 0o755)
  const root = defaultPowerContextRoot()
  const env = {
    ...process.env, CI: 'true', DSH_HOME: join(home, 'installed-dsh'),
    POWERCONTEXT_HOME: join(home, 'installed-powercontext'),
    PATH: bin + delimiter + process.env.PATH, DSH_TELEMETRY_DISABLED: '1',
  }
  const cli = async args => (await promisify(execFile)(windows ? 'uv.exe' : 'uv',
    ['run', '--no-sync', 'powercontext', ...args],
    { cwd: root, env, windowsHide: true, timeout: 150000, maxBuffer: 4 * 1024 * 1024 })).stdout
  const setup = await cli(['setup', 'dsh', '--source', root])
  const doctor = await cli(['doctor', 'dsh', '--json'])
  return { setup, doctor: JSON.parse(doctor), plugin: join(env.DSH_HOME, 'profiles/web/node_modules/powercontext-dsh') }
}
