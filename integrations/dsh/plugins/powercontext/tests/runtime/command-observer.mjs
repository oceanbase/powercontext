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

import { createServer } from 'node:http'
import { writeFileSync } from 'node:fs'

export const inject = ['agents', 'commands']

// The SDK only exposes prompts. Exercise the real command service through a local test adapter.
export async function apply(ctx, config) {
  const server = createServer(async (req, res) => {
    try {
      const sessionId = new URL(req.url, 'http://localhost').searchParams.get('session')
      const agent = ctx.agents.get(sessionId)
      if (!agent) throw new Error('test session is unavailable')
      const execution = await ctx.commands.execute(agent, '/pc doctor', [], AbortSignal.timeout(30000))
      if (!execution) throw new Error('installed /pc command is unavailable')
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify(execution.result))
    } catch (error) {
      res.writeHead(500)
      res.end(String(error))
    }
  })
  await new Promise((resolve, reject) => {
    server.once('error', reject)
    server.listen(0, '127.0.0.1', resolve)
  })
  writeFileSync(config.addressFile, 'http://127.0.0.1:' + server.address().port)
  ctx.effect(() => () => new Promise((resolve, reject) => {
    server.closeAllConnections()
    server.close(error => error ? reject(error) : resolve())
  }))
}
