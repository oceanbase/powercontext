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


import { constants, closeSync, fsyncSync, fstatSync, lstatSync, mkdirSync, openSync,
  opendirSync, readFileSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import { createHash, randomUUID } from 'node:crypto'
import { isAbsolute, join } from 'node:path'
import { HandoffError } from './errors.ts'

export function keyFor(...parts: string[]): string {
  return createHash('sha256').update(JSON.stringify(parts)).digest('hex')
}

export function privateDirectory(path: string): void {
  if (!isAbsolute(path)) throw new HandoffError('integration_unavailable')
  try { mkdirSync(path, { mode: 0o700 }) } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') throw error
  }
  const info = lstatSync(path)
  if (!info.isDirectory() || (info.mode & 0o077) !== 0 || info.uid !== process.getuid?.()) {
    throw new HandoffError('integration_unavailable')
  }
}

/** A store is used under the Host's single-writer lease. Each record is published by atomic rename. */
export class HandoffRecords<T extends { schema: string }> {
  private readonly root: string
  private readonly schema: T['schema']
  private readonly maxRecords: number
  private readonly maxRecordBytes: number

  constructor(root: string, schema: T['schema'], maxRecords = 512, maxRecordBytes = 256 * 1024) {
    this.root = root
    this.schema = schema
    this.maxRecords = maxRecords
    this.maxRecordBytes = maxRecordBytes
  }

  read(key: string): T | undefined {
    const path = this.path(key)
    let fd: number
    try { fd = openSync(path, constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK) } catch (error) {
      if ((error as NodeJS.ErrnoException).code === 'ENOENT') return undefined
      throw error
    }
    try {
      const info = fstatSync(fd)
      if (!info.isFile() || info.nlink !== 1 || (info.mode & 0o077) !== 0
        || info.uid !== process.getuid?.() || info.size > this.maxRecordBytes) {
        throw new HandoffError('integration_unavailable')
      }
      const value: unknown = JSON.parse(readFileSync(fd, 'utf8'))
      if (value === null || typeof value !== 'object' || !('schema' in value) || value.schema !== this.schema) {
        throw new HandoffError('integration_unavailable')
      }
      return value as T
    } finally { closeSync(fd) }
  }

  save(key: string, value: T, create = false): void {
    const path = this.path(key)
    const body = JSON.stringify(value)
    if (Buffer.byteLength(body) > this.maxRecordBytes) throw new HandoffError('capacity_exceeded')
    if (create) {
      const directory = opendirSync(this.root)
      try {
        let count = 0
        for (let item = directory.readSync(); item !== null; item = directory.readSync()) {
          if (/^[0-9a-f]{64}\.json$/.test(item.name) && ++count >= this.maxRecords) {
            throw new HandoffError('capacity_exceeded')
          }
        }
      } finally { directory.closeSync() }
    }
    const temporary = join(this.root, `.${randomUUID()}.tmp`)
    const fd = openSync(temporary, 'wx', 0o600)
    try {
      try {
        writeFileSync(fd, body)
        fsyncSync(fd)
      } finally { closeSync(fd) }
      renameSync(temporary, path)
      const directory = openSync(this.root, constants.O_RDONLY)
      try { fsyncSync(directory) } finally { closeSync(directory) }
    } finally {
      try { unlinkSync(temporary) } catch (error) {
        if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      }
    }
  }

  private path(key: string): string {
    if (!/^[0-9a-f]{64}$/.test(key)) throw new HandoffError('not_found')
    privateDirectory(this.root)
    return join(this.root, `${key}.json`)
  }
}
