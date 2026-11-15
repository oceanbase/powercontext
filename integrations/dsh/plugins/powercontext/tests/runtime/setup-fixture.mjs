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
import assert from 'node:assert/strict'
import { chmodSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { delimiter, join } from 'node:path'
import { promisify } from 'node:util'
import { defaultPowerContextRoot } from '../../scripts/e2e-server.mjs'
import { dshBin } from './fixture.mjs'

export function setupFixture(home, { dshHome = join(home, 'installed-dsh') } = {}) {
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
  const inherited = Object.fromEntries(Object.entries(process.env)
    .filter(([key]) => !key.toUpperCase().startsWith('POWERCONTEXT_') && !key.toUpperCase().startsWith('DSH_')))
  const env = {
    ...inherited, CI: 'true', DSH_HOME: dshHome,
    POWERCONTEXT_HOME: join(home, 'installed-powercontext'),
    POWERCONTEXT_CLIENT_CONFIG_FILE: join(home, 'clients.json'),
    PATH: bin + delimiter + process.env.PATH, DSH_TELEMETRY_DISABLED: '1',
  }
  const options = { cwd: home, env, windowsHide: true, timeout: 150000, maxBuffer: 4 * 1024 * 1024 }
  const cli = async args => {
    try {
      return (await promisify(execFile)(windows ? 'uv.exe' : 'uv',
        ['run', '--project', root, '--no-sync', 'powercontext', ...args], options)).stdout
    } catch (error) {
      console.error('DIAG_ARGS', JSON.stringify(args))
      console.error('DIAG_STDOUT', error.stdout)
      console.error('DIAG_STDERR', error.stderr)
      throw error
    }
  }
  const native = async args => (await promisify(execFile)(process.execPath, [dshBin, ...args], options)).stdout
  const profile = join(env.DSH_HOME, 'profiles/web')
  return { root, cli, native, env, profile, patch: join(profile, 'cordis.patch.yml'),
    homePatch: join(env.DSH_HOME, 'cordis.patch.yml'), clients: env.POWERCONTEXT_CLIENT_CONFIG_FILE }
}

export async function installIntoHome(home, { customized = false, serverUrl } = {}) {
  const { root, cli, native, profile, patch } = setupFixture(home)
  const customizations = '- id: agent-default-model\n  name: "@deepseek-ai/dsh-agent-default-model"\n'
  if (customized) {
    await native(['--profile', 'web', '--dump-default-config'])
    writeFileSync(patch, customizations)
  }
  const args = ['setup', 'dsh', '--source', root, '--server-url', serverUrl, '--json']
  const setup = JSON.parse(await cli(args)).hosts.dsh
  if (customized) {
    await cli(args)
    assert.equal(readFileSync(patch, 'utf8'), customizations)
  }
  const doctor = await cli(['doctor', 'dsh', '--json'])
  return { setup, doctor: JSON.parse(doctor).hosts.dsh, plugin: join(profile, 'node_modules/powercontext-dsh') }
}
