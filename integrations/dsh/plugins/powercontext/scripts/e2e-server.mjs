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

import { createServer } from 'node:net'
import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const pluginRoot = join(dirname(fileURLToPath(import.meta.url)), '..')

function walkForPyproject(startDir) {
  let dir = resolve(startDir)
  for (let i = 0; i < 8; i += 1) {
    if (existsSync(join(dir, 'pyproject.toml'))) return dir
    const parent = resolve(dir, '..')
    if (parent === dir) break
    dir = parent
  }
  return undefined
}

export function defaultPowerContextRoot() {
  const configured = process.env.POWERCONTEXT_ROOT?.trim()
  if (configured && existsSync(join(configured, 'pyproject.toml'))) return resolve(configured)
  return walkForPyproject(pluginRoot)
}

export function unusedPort() {
  return new Promise((resolve, reject) => {
    const server = createServer()
    server.listen(0, '127.0.0.1', () => {
      const address = server.address()
      if (!address || typeof address === 'string') {
        server.close()
        reject(new Error('could not allocate a TCP port'))
        return
      }
      const { port } = address
      server.close((error) => {
        if (error) reject(error)
        else resolve(port)
      })
    })
    server.on('error', reject)
  })
}

export async function waitForUrl(url, timeoutMs = 30000) {
  const deadline = Date.now() + timeoutMs
  let lastError
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url, { signal: AbortSignal.timeout(1000) })
      if (response.ok || response.status === 503) return
      lastError = new Error(`HTTP ${response.status}`)
    } catch (error) {
      lastError = error
    }
    await new Promise((resolve) => setTimeout(resolve, 200))
  }
  throw new Error(`Server at ${url} did not become ready: ${lastError}`)
}

function spawnServer(root, env) {
  const uv = process.platform === 'win32' ? 'uv.exe' : 'uv'
  return spawn(uv, ['run', 'powercontext', 'server', 'run'], {
    cwd: root,
    env,
    stdio: ['ignore', 'pipe', 'pipe'],
  })
}

export async function startPowerContextServer() {
  const root = defaultPowerContextRoot()
  if (!root) {
    throw new Error('Set POWERCONTEXT_ROOT to a PowerContext checkout that contains pyproject.toml')
  }
  const port = await unusedPort()
  const home = mkdtempSync(join(tmpdir(), 'pc-dsh-e2e-'))
  const env = {
    ...process.env,
    POWERCONTEXT_HOME: home,
    POWERCONTEXT_SERVER_HTTP_HOST: '127.0.0.1',
    POWERCONTEXT_SERVER_HTTP_PORT: String(port),
  }
  const child = spawnServer(root, env)
  const logs = []
  child.stdout?.on('data', (chunk) => logs.push(String(chunk)))
  child.stderr?.on('data', (chunk) => logs.push(String(chunk)))
  const baseUrl = `http://127.0.0.1:${port}`
  try {
    await waitForUrl(`${baseUrl}/health/live`)
  } catch (error) {
    child.kill()
    throw new Error(`${error.message}\n${logs.join('')}`)
  }
  return {
    baseUrl,
    home,
    root,
    async stop() {
      if (!child.killed) child.kill()
      await new Promise((resolve) => {
        if (child.exitCode !== null) {
          resolve()
          return
        }
        child.once('exit', resolve)
        setTimeout(resolve, 3000)
      })
    },
  }
}
