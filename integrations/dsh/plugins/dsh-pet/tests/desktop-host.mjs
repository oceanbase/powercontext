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

import { spawn } from 'node:child_process'
import { once } from 'node:events'
import { join } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { unusedPort } from '../../powercontext/scripts/e2e-server.mjs'

async function rendererCommand(target, method, params, timeout) {
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  try {
    await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Desktop renderer connection timed out')), 5000)
      socket.addEventListener('open', () => { clearTimeout(timer); resolve() }, { once: true })
      socket.addEventListener('error', () => {
        clearTimeout(timer)
        reject(new Error('Desktop renderer connection failed'))
      }, { once: true })
    })
    return await new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(new Error('Desktop renderer command timed out: ' + method)), timeout)
      socket.addEventListener('message', event => {
        const data = JSON.parse(event.data)
        if (data.id !== 1) return
        clearTimeout(timer)
        if (data.error) reject(new Error(data.error.message))
        else resolve(data.result)
      })
      socket.send(JSON.stringify({ id: 1, method, params }))
    })
  } finally {
    socket.close()
  }
}

export async function startDesktop(app, root, env) {
  const port = await unusedPort()
  const binary = join(app, 'Contents/MacOS/DeepSeek Harness')
  const child = spawn(binary, [
    '--user-data-dir=' + join(root, 'electron'),
    '--remote-debugging-address=127.0.0.1',
    '--remote-debugging-port=' + port,
  ], {
    cwd: root,
    env: { ...env, CFFIXED_USER_HOME: root },
    stdio: ['ignore', 'ignore', 'pipe'],
  })
  let diagnostic = ''
  let failure
  child.once('error', error => { failure = error })
  child.stderr.on('data', part => { diagnostic = (diagnostic + part).slice(-4096) })
  const targets = async () => {
    if (failure || child.exitCode !== null) {
      throw new Error('Packaged Desktop exited before its renderer was ready. ' + (failure?.code ?? child.exitCode)
        + '\n' + diagnostic.replace(/token=\S+/g, 'token=[redacted]'))
    }
    try {
      const response = await fetch('http://127.0.0.1:' + port + '/json/list', { signal: AbortSignal.timeout(2000) })
      return response.ok ? await response.json() : []
    } catch {
      return []
    }
  }
  const appRenderer = async () => (await targets()).find(target => target.url.startsWith('dsh-app://app/'))
  const stop = async () => {
    if (failure || child.exitCode !== null || child.signalCode !== null) return
    const exited = once(child, 'exit')
    child.kill('SIGTERM')
    await Promise.race([exited, delay(5000, undefined, { ref: false })])
    if (child.exitCode === null && child.signalCode === null) {
      child.kill('SIGKILL')
      await exited
    }
  }
  const evaluate = async (expression) => {
    const target = await appRenderer()
    if (!target) throw new Error('DSH Desktop renderer is not ready')
    const result = await rendererCommand(target, 'Runtime.evaluate', {
      expression, awaitPromise: true, returnByValue: true,
    }, 25_000)
    if (result.exceptionDetails) throw new Error(result.exceptionDetails.exception?.description ?? result.exceptionDetails.text)
    return result.result.value
  }
  return { stop, appRenderer, evaluate }
}
