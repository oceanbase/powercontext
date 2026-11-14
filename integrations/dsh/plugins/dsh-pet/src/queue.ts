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

import { randomUUID } from 'node:crypto'
import { open, readdir, readFile, rename, rm, stat } from 'node:fs/promises'
import { join } from 'node:path'
import { containsSecret } from '../../powercontext/src/prompt-privacy.ts'
import { privateDirectory, record, type Binding } from './state.ts'

export const MAX_TURN_BYTES = 24_000
const MAX_ENTRIES = 128
const MAX_AGE = 7 * 24 * 60 * 60 * 1000
const ITEM = /^(\d{13})-([0-9a-f-]{36})\.(pending|conflict|rejected)$/

export class CaptureQueue {
  private readonly root: string
  private readonly binding: Binding

  constructor(root: string, binding: Binding) {
    this.root = root
    this.binding = binding
  }

  private get directory(): string { return join(this.root, this.binding.generation) }

  async entries(): Promise<string[]> {
    let names: string[]
    try {
      names = await readdir(this.directory)
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return []
      throw error
    }
    const retained: string[] = []
    for (const name of names.sort()) {
      const match = ITEM.exec(name)
      if (!match) continue
      if (Date.now() - Number(match[1]) >= MAX_AGE) await this.remove(name)
      else retained.push(name)
    }
    return retained
  }

  async add(content: string, ts: number, current: () => boolean): Promise<boolean> {
    const names = await this.entries()
    if (names.length >= MAX_ENTRIES || !current()) return false
    privateDirectory(this.root)
    privateDirectory(this.directory)
    const id = randomUUID()
    // Keep replay order even if two completed exchanges share a clock tick.
    const position = Math.max(Date.now(), Number(names.at(-1)?.slice(0, 13) ?? 0) + 1)
    const name = position + '-' + id + '.pending'
    const temp = join(this.directory, '.' + id)
    const payload = {
      scope_id: this.binding.scope_id,
      source_id: 'dsh-pet:' + id,
      content,
      metadata: { origin: 'dsh-pet', event: 'pet_dialogue', occurred_at: new Date(ts).toISOString() },
    }
    const file = await open(temp, 'wx', 0o600)
    try {
      try {
        await file.writeFile(JSON.stringify(payload))
        await file.sync()
      } finally {
        await file.close()
      }
      if (!current()) return false
      await rename(temp, join(this.directory, name))
      return true
    } finally {
      await rm(temp, { force: true })
    }
  }

  async payload(name: string) {
    const data = record(JSON.parse(await readFile(join(this.directory, name), 'utf8')))
    if (!data || data.scope_id !== this.binding.scope_id || data.source_id !== 'dsh-pet:' + ITEM.exec(name)?.[2]
      || typeof data.content !== 'string' || !data.content || Buffer.byteLength(data.content) > MAX_TURN_BYTES
      || containsSecret(data.content) || record(data.metadata)?.origin !== 'dsh-pet') throw new Error('invalid_queue_item')
    return data
  }

  async block(name: string, reason: 'conflict' | 'rejected') {
    await rename(join(this.directory, name), join(this.directory, name.replace(/\.pending$/, '.' + reason)))
  }

  async remove(name: string) { await rm(join(this.directory, name), { force: true }) }
}


// Retired bindings cannot deliver. Prune their data, and interrupted temporary writes,
// without rewriting a journal or changing the identity of any retained exchange.
export async function pruneExpired(root: string): Promise<void> {
  let dirs
  try {
    dirs = await readdir(root, { withFileTypes: true })
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code === 'ENOENT') return
    throw error
  }
  const expired = Date.now() - MAX_AGE
  for (const dir of dirs) {
    if (!dir.isDirectory() || !/^[a-f0-9]{32}$/.test(dir.name)) continue
    const path = join(root, dir.name)
    for (const name of await readdir(path)) {
      const match = ITEM.exec(name)
      const file = join(path, name)
      if (match ? Number(match[1]) < expired : /^\.[a-f0-9-]{36}$/.test(name) && (await stat(file)).mtimeMs < expired) {
        await rm(file, { force: true })
      }
    }
  }
}
