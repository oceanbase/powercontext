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
import { constants, closeSync, fstatSync, fsyncSync, lstatSync, mkdirSync, openSync, readSync, readdirSync,
  realpathSync, renameSync, unlinkSync, writeFileSync } from 'node:fs'
import type { Stats } from 'node:fs'
import { basename, dirname, isAbsolute, join, resolve } from 'node:path'
import { DeliveryError, isDeliveryErrorCode } from './contracts.ts'
import type { DeliveryErrorCode, PreparationState, ReceiptAttempt, VerifiedPreflight } from './contracts.ts'
import { checksumPattern, deliveryIdPattern, receiptIdentity, verifiedBinding } from './identity.ts'
import { invalid, object, trimmed } from './wire.ts'

export const DELIVERY_SCHEMA = 'powercontext.dsh-delivery.v1'
export const MAX_PACKAGE_BYTES = 1024 * 1024 + 32 * 1024 // Existing client response limit + envelope.
const MAX_RECORD_BYTES = 64 * 1024
export const MAX_REQUEST_KEYS = 64

export interface StoredPackage {
  sha256: string
  bytes: number
}

export interface DeliveryRecord {
  schema: typeof DELIVERY_SCHEMA
  deliveryId: string
  ownerId: string
  requests: { id: string; fingerprint: string }[]
  binding: VerifiedPreflight
  receiptAttempt: ReceiptAttempt
  preparationState: PreparationState
  createdAt: string
  updatedAt: string
  pinned: boolean
  dispatched: boolean
  error?: DeliveryErrorCode
  supersedesDeliveryId?: string
  package?: StoredPackage
  expiredAt?: string
  deletedAt?: string
}

const states = new Set(['preparing', 'needs_input', 'prepared', 'failed', 'cancelled', 'unknown'])

function date(value: unknown): value is string {
  return typeof value === 'string' && Number.isFinite(Date.parse(value))
}

function decodeRecord(value: unknown, id: string): DeliveryRecord {
  if (!object(value) || value.schema !== DELIVERY_SCHEMA || value.deliveryId !== id
    || !trimmed(value.ownerId, 256) || typeof value.preparationState !== 'string' || !states.has(value.preparationState)
    || !date(value.createdAt) || !date(value.updatedAt) || typeof value.pinned !== 'boolean'
    || typeof value.dispatched !== 'boolean' || !Array.isArray(value.requests)
    || value.requests.length < 1 || value.requests.length > MAX_REQUEST_KEYS
    || value.requests.some(key => !object(key) || !trimmed(key.id, 128)
      || typeof key.fingerprint !== 'string' || !checksumPattern.test(key.fingerprint))
    || (value.error !== undefined && !isDeliveryErrorCode(value.error))
    || (value.expiredAt !== undefined && !date(value.expiredAt))
    || (value.deletedAt !== undefined && !date(value.deletedAt))
    || (value.supersedesDeliveryId !== undefined && (typeof value.supersedesDeliveryId !== 'string'
      || !deliveryIdPattern.test(value.supersedesDeliveryId)))) invalid()
  if (value.package !== undefined && (!object(value.package) || typeof value.package.sha256 !== 'string'
    || !checksumPattern.test(value.package.sha256) || typeof value.package.bytes !== 'number'
    || !Number.isSafeInteger(value.package.bytes) || value.package.bytes < 1 || value.package.bytes > MAX_PACKAGE_BYTES)) invalid()
  return { ...value, binding: verifiedBinding(value.binding),
    receiptAttempt: receiptIdentity(value.receiptAttempt) } as unknown as DeliveryRecord
}

function errno(error: unknown, code: string): boolean {
  return object(error) && error.code === code
}

function privateEntry(stat: Stats, directory: boolean): void {
  if ((directory ? !stat.isDirectory() : !stat.isFile() || stat.nlink !== 1)
    || stat.uid !== process.getuid?.() || (stat.mode & 0o077) !== 0) invalid()
}

/**
 * One local Host owns a directory. Every transaction is synchronous and runs to completion
 * between awaits; each delivery's files are separate and normal requests never scan the disk.
 */
export class DeliveryStore {
  readonly records = new Map<string, DeliveryRecord>()
  private readonly root: string
  private readonly rootStat: Stats
  private readonly marker: string

  constructor(directory: string) {
    if (!isAbsolute(directory) || !process.getuid || !constants.O_NOFOLLOW) {
      throw new DeliveryError('integration_unavailable')
    }
    const path = resolve(directory)
    this.root = join(realpathSync(dirname(path)), basename(path))
    try {
      mkdirSync(this.root, { mode: 0o700 })
    } catch (error) {
      if (!errno(error, 'EEXIST')) throw error
    }
    this.rootStat = lstatSync(this.root)
    privateEntry(this.rootStat, true)
    this.marker = join(this.root, '.host-' + process.pid)
    // Publish before scanning. Two racing Hosts can both fail closed but cannot both enter.
    closeSync(openSync(this.marker, 'wx', 0o600))
    try {
      const entries = readdirSync(this.root)
      for (const name of entries) {
        const host = /^\.host-([1-9]\d*)$/.exec(name)
        if (host && name !== basename(this.marker)) {
          try {
            process.kill(Number(host[1]), 0)
          } catch (error) {
            if (!errno(error, 'ESRCH')) throw error
            // A stale marker does not hold the directory. Leave it untouched: its PID could
            // have been recycled and a new Host may be claiming the same filename.
            continue
          }
          throw new DeliveryError('integration_unavailable')
        }
      }
      for (const name of entries) {
        const id = name.slice(0, -'.record.json'.length)
        if (name.endsWith('.record.json') && deliveryIdPattern.test(id)) {
          const record = decodeRecord(JSON.parse(this.read(name, MAX_RECORD_BYTES).toString('utf8')), id)
          this.records.set(id, record)
        } else if (/^\.write-[0-9a-f-]{36}$/.test(name)) {
          this.unlink(name) // An interrupted atomic write was never published.
        }
      }
    } catch (error) {
      this.close()
      throw error
    }
  }

  private checkRoot(): void {
    const stat = lstatSync(this.root)
    privateEntry(stat, true)
    if (stat.ino !== this.rootStat.ino || stat.dev !== this.rootStat.dev) invalid()
  }

  private read(name: string, maximum: number): Buffer {
    this.checkRoot()
    const fd = openSync(join(this.root, name), constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK)
    try {
      const stat = fstatSync(fd)
      privateEntry(stat, false)
      if (stat.size <= 0 || stat.size > maximum) invalid()
      const bytes = Buffer.alloc(stat.size)
      let offset = 0
      while (offset < bytes.length) {
        const size = readSync(fd, bytes, offset, bytes.length - offset, null)
        if (!size) invalid()
        offset += size
      }
      if (readSync(fd, Buffer.alloc(1), 0, 1, null) !== 0) invalid()
      return bytes
    } finally {
      closeSync(fd)
    }
  }

  private syncDirectory(): void {
    const fd = openSync(this.root, constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW)
    try {
      fsyncSync(fd)
    } finally {
      closeSync(fd)
    }
  }

  private unlink(name: string): void {
    this.checkRoot()
    try {
      unlinkSync(join(this.root, name))
    } catch (error) {
      if (!errno(error, 'ENOENT')) throw error
    }
  }

  private write(name: string, value: Buffer, replace: boolean): void {
    this.checkRoot()
    const path = join(this.root, name)
    try {
      const old = lstatSync(path)
      if (!replace) invalid()
      privateEntry(old, false)
    } catch (error) {
      if (!errno(error, 'ENOENT')) throw error
    }
    const temporary = '.write-' + randomUUID()
    const fd = openSync(join(this.root, temporary), 'wx', 0o600)
    try {
      try {
        writeFileSync(fd, value)
        fsyncSync(fd)
      } finally {
        closeSync(fd)
      }
      renameSync(join(this.root, temporary), path)
      this.syncDirectory()
    } finally {
      this.unlink(temporary)
    }
  }

  put(record: DeliveryRecord): DeliveryRecord {
    const bytes = Buffer.from(JSON.stringify(record))
    if (bytes.length > MAX_RECORD_BYTES) throw new DeliveryError('capacity_exceeded')
    this.write(record.deliveryId + '.record.json', bytes, this.records.has(record.deliveryId))
    this.records.set(record.deliveryId, record)
    return record
  }

  packagePath(id: string): string {
    return join(this.root, id + '.package.json')
  }

  readPackage(id: string): Buffer {
    return this.read(id + '.package.json', MAX_PACKAGE_BYTES)
  }

  writePackage(id: string, bytes: Buffer): void {
    if (bytes.length > MAX_PACKAGE_BYTES) invalid()
    this.write(id + '.package.json', bytes, false)
  }

  removePackage(id: string): void {
    this.unlink(id + '.package.json')
    this.syncDirectory()
  }

  forget(id: string): void {
    this.removePackage(id)
    this.unlink(id + '.record.json')
    this.syncDirectory()
    this.records.delete(id)
  }

  close(): void {
    this.unlink(basename(this.marker))
  }
}
