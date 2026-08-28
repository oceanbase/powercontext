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

import { spawnSync } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const packageRoot = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

function piBinary(): string {
  return join(packageRoot, 'node_modules', '.bin', process.platform === 'win32' ? 'pi.cmd' : 'pi')
}

function piEnvironment(agentDirectory: string): NodeJS.ProcessEnv {
  const environment: NodeJS.ProcessEnv = {
    ...process.env,
    FORCE_COLOR: '0',
    NO_COLOR: '1',
    PI_CODING_AGENT_DIR: agentDirectory,
    PI_OFFLINE: '1',
  }
  for (const name of Object.keys(environment)) {
    if (name.startsWith('POWERCONTEXT_PI_')) delete environment[name]
  }
  return environment
}

function runPi(agentDirectory: string, arguments_: string[], input?: string) {
  return spawnSync(piBinary(), arguments_, {
    cwd: packageRoot,
    encoding: 'utf8',
    env: piEnvironment(agentDirectory),
    input,
    shell: process.platform === 'win32',
    timeout: 30_000,
  })
}

describe('PowerContext Pi package e2e', () => {
  it('loads as a native package and exposes /pc through the real Pi CLI', async () => {
    const agentDirectory = await mkdtemp(join(tmpdir(), 'powercontext-pi-smoke-'))
    try {
      const installed = runPi(agentDirectory, ['install', packageRoot])
      expect(installed.error).toBeUndefined()
      expect(installed.status).toBe(0)

      const response = runPi(
        agentDirectory,
        [
          '--mode', 'rpc',
          '--no-context-files',
          '--no-session',
          '--no-skills',
          '--provider', 'openai',
          '--model', 'gpt-4o',
          '--api-key', 'smoke-test-key',
        ],
        '{"id":"commands","type":"get_commands"}\n',
      )
      expect(response.error).toBeUndefined()
      expect(response.status).toBe(0)

      const records = String(response.stdout)
        .split('\n')
        .filter(Boolean)
        .map((line) => JSON.parse(line) as { id?: string; data?: { commands?: unknown[] } })
      const commandResponse = records.find((record) => record.id === 'commands')
      expect(commandResponse?.data?.commands).toContainEqual(expect.objectContaining({
        name: 'pc',
        source: 'extension',
        sourceInfo: expect.objectContaining({ origin: 'package' }),
      }))
    } finally {
      await rm(agentDirectory, { force: true, recursive: true })
    }
  })
})
