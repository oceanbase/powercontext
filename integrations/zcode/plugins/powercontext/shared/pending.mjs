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
import { link, mkdir, open, opendir, rename, unlink, writeFile } from 'node:fs/promises'
import { join } from 'node:path'
import { observationIdentity, stageResult } from './observations.mjs'
import { isScopeId, sessionIdentity, sha256 } from './scope.mjs'
import { failureCode, request } from './transport.mjs'

const SCHEMA = 'powercontext.zcode.pending.v1'
const RECORD_LIMIT = 256
const ENTRY_LIMIT = 512
const LEASE_MS = 5000
const UUID = '[a-f0-9-]{36}'
const RECORD = new RegExp(`^(receipt|track)-${UUID}\\.json$`, 'u')

async function atomic(path, value) {
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, JSON.stringify(value), { mode: 0o600 })
    await rename(temporary, path)
  } finally { await unlink(temporary).catch(() => {}) }
}

async function read(path) {
  const file = await open(path, 'r')
  try {
    const buffer = Buffer.alloc(16_385)
    const { bytesRead } = await file.read(buffer, 0, buffer.length, 0)
    if (bytesRead > 16_384) throw new Error('invalid_pending')
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, bytesRead)))
    if (value.schema !== SCHEMA) throw new Error('unsupported_pending')
    return value
  } finally { await file.close() }
}

function identityMatches(a, b) {
  return ['endpoint', 'profile', 'session'].every(key => a?.[key] === b[key])
}

function sessionKey(identity) {
  return Object.fromEntries(['endpoint', 'profile', 'session'].map(key => [key, identity[key]]))
}

function scopeKey(identity, scopeId) {
  return sha256(JSON.stringify([sessionKey(identity), scopeId]))
}

function valid(value) {
  return value && ['endpoint', 'profile', 'session', 'workspace'].every(key => /^[a-f0-9]{64}$/u.test(value.identity?.[key])) &&
    isScopeId(value.scope_id) &&
    Number.isSafeInteger(value.created_at) && (value.kind === 'track' || value.kind === 'receipt' &&
      Number.isSafeInteger(value.position) && value.position > 0)
}

async function snapshot(directory) {
  const records = []
  let count = 0
  try {
    for await (const entry of await opendir(directory)) {
      if (++count > ENTRY_LIMIT) throw new Error('pending_capacity_exceeded')
      const claim = /^claim-v1-[a-f0-9]{64}-([0-9]+)\.lock$/u.exec(entry.name)
      if (claim && Number(claim[1]) < Math.floor(Date.now() / LEASE_MS) - 1) {
        if (!entry.isFile()) throw new Error('invalid_pending')
        await unlink(join(directory, entry.name)).catch(error => { if (error.code !== 'ENOENT') throw error })
        continue
      }
      if (!RECORD.test(entry.name)) continue
      const path = join(directory, entry.name)
      let value
      try { value = await read(path) } catch (error) { if (error.code === 'ENOENT') continue; throw error }
      if (!valid(value) || !entry.name.startsWith(value.kind + '-')) throw new Error('invalid_pending')
      value.identity = Object.fromEntries(['endpoint', 'profile', 'session', 'workspace'].map(key => [key, value.identity[key]]))
      records.push({ path, value })
    }
  } catch (error) { if (error.code !== 'ENOENT') throw error }
  const receipts = new Map(records.filter(entry => entry.value.kind === 'receipt')
    .map(entry => [entry.path.replace(/receipt-([a-f0-9-]{36})\.json$/u, '$1'), entry]))
  const retained = []
  for (const entry of records) {
    const receipt = entry.value.kind === 'track'
      ? receipts.get(entry.path.replace(/track-([a-f0-9-]{36})\.json$/u, '$1')) : undefined
    if (receipt) {
      if (!['endpoint', 'profile', 'session', 'workspace'].every(key => entry.value.identity[key] === receipt.value.identity[key]) ||
          entry.value.scope_id !== receipt.value.scope_id || entry.value.created_at !== receipt.value.created_at) throw new Error('invalid_pending')
      // Acceptance was durably recorded before a crash interrupted guard cleanup.
      await unlink(entry.path).catch(error => { if (error.code !== 'ENOENT') throw error })
    } else retained.push(entry)
  }
  if (retained.length > RECORD_LIMIT) throw new Error('pending_capacity_exceeded')
  retained.sort((a, b) => a.value.created_at - b.value.created_at || a.path.localeCompare(b.path))
  return retained
}

async function exists(path) {
  try { await read(path); return true } catch (error) { if (error.code === 'ENOENT') return false; throw error }
}

async function pauseExists(path) {
  let file
  try {
    file = await open(path, 'r')
    if (!(await file.stat()).isFile()) throw new Error('invalid_pending')
    return true
  } catch (error) { if (error.code === 'ENOENT') return false; throw error }
  finally { await file?.close() }
}

function incompletePause(error) {
  return error instanceof SyntaxError || error.code === 'ERR_ENCODING_INVALID_ENCODED_DATA'
}

export async function beginCaptureTracking(input, settings, keys, scopeId, dataDir = process.env.ZCODE_PLUGIN_DATA) {
  if (!sessionIdentity(input)) throw new Error('session_required')
  if (!isScopeId(scopeId)) throw new Error('invalid_pending')
  const identity = observationIdentity(input, settings, dataDir, keys)
  const directory = join(dataDir, 'pending')
  await mkdir(directory, { recursive: true })
  const records = await snapshot(directory)
  if (records.length >= RECORD_LIMIT) {
    await atomic(join(directory, 'tracking-incomplete.json'), { schema: SCHEMA, kind: 'tracking_incomplete' })
    throw new Error('pending_capacity_exceeded')
  }
  const id = randomUUID(), guard = join(directory, `track-${id}.json`)
  const value = { schema: SCHEMA, kind: 'track', identity, scope_id: scopeId, created_at: Date.now() }
  await atomic(guard, value)
  return {
    async accepted(position) {
      if (!Number.isSafeInteger(position) || position < 1) throw new Error('invalid_receipt')
      await atomic(join(directory, `receipt-${id}.json`), { ...value, kind: 'receipt', position })
      await unlink(guard).catch(error => { if (error.code !== 'ENOENT') throw error })
    },
    async rejected() { await unlink(guard).catch(error => { if (error.code !== 'ENOENT') throw error }) },
  }
}

async function lease(directory, identity) {
  const now = Date.now(), slot = Math.floor(now / LEASE_MS)
  if ((slot + 1) * LEASE_MS - now < 1000) return false
  const key = sha256(JSON.stringify(sessionKey(identity)))
  // Immutable time-slot files avoid unsafe compare/unlink/recreate of an expired lock.
  // A durable pause below protects any write whose outcome outlives its lease.
  try {
    const file = await open(join(directory, `claim-v1-${key}-${slot}.lock`), 'wx', 0o600)
    await file.close()
    return true
  } catch (error) { if (error.code === 'EEXIST') return false; throw error }
}

export async function flushBoundary(input, settings, keys, resolveCurrent, signal, dataDir = process.env.ZCODE_PLUGIN_DATA) {
  if (!settings.boundaryFlush) return stageResult('skipped', { reason: 'boundary_flush_disabled' })
  if (!sessionIdentity(input)) return stageResult('skipped', { reason: 'session_required' })
  const identity = observationIdentity(input, settings, dataDir, keys)
  const directory = join(dataDir, 'pending')
  if (await exists(join(directory, 'tracking-incomplete.json'))) return stageResult('skipped', { reason: 'tracking_incomplete' })
  const records = (await snapshot(directory)).filter(({ value }) => identityMatches(value.identity, identity))
  if (!records.length) return stageResult('skipped', { reason: 'no_pending' })
  signal.throwIfAborted()
  const scope = await resolveCurrent()
  const eligible = records.filter(({ value }) => value.scope_id === scope.scopeId)
  if (!eligible.length) return stageResult('skipped', { reason: 'scope_changed' })
  if (eligible.some(({ value }) => value.kind === 'track')) return stageResult('skipped', { reason: 'tracking_incomplete' })
  const pause = join(directory, `pause-${scopeKey(identity, scope.scopeId)}.json`)
  if (await pauseExists(pause)) return stageResult('unknown', { reason: 'unknown_flush_paused' })
  if (!await lease(directory, identity)) return stageResult('skipped', { reason: 'claim_busy' })
  const target = Math.max(...eligible.map(({ value }) => value.position))
  signal.throwIfAborted()
  // Persist uncertainty BEFORE sending. A crash/timeout cannot permit an automatic retry.
  const temporary = `${pause}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, JSON.stringify({ schema: SCHEMA, kind: 'pause', identity, scope_id: scope.scopeId,
      target_position: target, created_at: Date.now() }), { mode: 0o600, flag: 'wx' })
    // A hard link publishes complete bytes atomically and cannot overwrite another writer's pause.
    await link(temporary, pause)
  } catch (error) {
    if (error.code === 'EEXIST') return stageResult('unknown', { reason: 'unknown_flush_paused' })
    throw error
  } finally { await unlink(temporary).catch(() => {}) }
  let value
  try {
    const result = await request(settings, 'POST', '/v1/memory/flush', { scope_id: scope.scopeId }, signal)
    value = result.body
    if (result.status !== 200 || !['idle', 'processed'].includes(value.status) ||
        !['previous_cursor', 'current_cursor', 'high_watermark', 'processed_source_count']
          .every(key => Number.isSafeInteger(value[key]) && value[key] >= 0) ||
        value.current_cursor < value.previous_cursor || value.current_cursor > value.high_watermark) {
      throw Object.assign(new Error('invalid_flush'), { requestSent: true, httpStatus: result.status })
    }
  } catch (error) {
    const rejected = error.httpStatus >= 400 && error.httpStatus < 500
    if (!error.requestSent || rejected) await unlink(pause)
    return stageResult(error.requestSent && !rejected ? 'unknown' : 'failed', {
      code: failureCode(error), source_position: target, http_status: error.httpStatus,
    })
  }
  try {
    for (const entry of eligible) {
      if (entry.value.position <= value.current_cursor) await unlink(entry.path).catch(error => {
        if (error.code !== 'ENOENT') throw error
      })
    }
    await unlink(pause)

  } catch {
    return stageResult(value.current_cursor >= target ? 'cursor_reached' : 'pending', {
      current_cursor: value.current_cursor, source_position: target, reason: 'pending_cleanup_failed',
    })
  }
  return stageResult(value.current_cursor >= target ? 'cursor_reached' : 'pending', {
    current_cursor: value.current_cursor, source_position: target,
  })
}

export async function queryPending(input, settings, dataDir, latest = false) {
  const identity = observationIdentity(input, settings, dataDir)
  const directory = join(dataDir, 'pending')
  const snapshotRecords = await snapshot(directory)
  const pauseOwners = new Set(snapshotRecords.filter(({ value }) => value.kind === 'receipt')
    .map(({ value }) => scopeKey(value.identity, value.scope_id)))
  const records = snapshotRecords.filter(({ value }) =>
    ['endpoint', 'profile', latest ? 'workspace' : 'session'].every(key => value.identity[key] === identity[key]))
  const groups = new Map()
  for (const { value } of records) {
    const key = scopeKey(value.identity, value.scope_id)
    const group = groups.get(key) ?? { scope_id: value.scope_id, receipt_count: 0, target_position: null,
      tracking_incomplete: false, unknown_flush_paused: await pauseExists(join(directory, `pause-${key}.json`)) }
    if (value.kind === 'receipt') {
      group.receipt_count++
      group.target_position = Math.max(group.target_position ?? 0, value.position)
    } else group.tracking_incomplete = true
    groups.set(key, group)
  }
  // A cleanup failure may leave a pause after every covered receipt was removed.
  // Expose it too, so it cannot disappear from status before the next capture.
  let scanned = 0
  try {
    for await (const entry of await opendir(directory)) {
      if (++scanned > ENTRY_LIMIT) throw new Error('pending_capacity_exceeded')
      if (!/^pause-[a-f0-9]{64}\.json$/u.test(entry.name)) continue
      let value
      try { value = await read(join(directory, entry.name)) }
      catch (error) {
        if (error.code === 'ENOENT') continue
        // Legacy interrupted writes have no readable identity. Only matching receipts can identify their pause.
        if (incompletePause(error) && pauseOwners.has(entry.name.slice(6, -5))) continue
        throw error
      }
      if (value.kind !== 'pause' || !valid({ ...value, kind: 'track' }) ||
          !Number.isSafeInteger(value.target_position) || value.target_position < 1) throw new Error('invalid_pending')
      const key = scopeKey(value.identity, value.scope_id)
      if (entry.name !== `pause-${key}.json`) throw new Error('invalid_pending')
      if (!['endpoint', 'profile', latest ? 'workspace' : 'session'].every(name => value.identity[name] === identity[name])) continue
      const group = groups.get(key) ?? { scope_id: value.scope_id, receipt_count: 0,
        target_position: value.target_position, tracking_incomplete: false }
      group.unknown_flush_paused = true
      groups.set(key, group)
    }
  } catch (error) { if (error.code !== 'ENOENT') throw error }
  return { schema: 'powercontext.zcode.pending-status.v1', selection: latest ? 'workspace_history' : 'session_exact',
    tracking_incomplete: await exists(join(directory, 'tracking-incomplete.json')), scopes: [...groups.values()] }
}

export async function authorizeUnknownFlushRetry(input, settings, keys, scopeId, dataDir) {
  if (!sessionIdentity(input)) throw new Error('session_required')
  const identity = observationIdentity(input, settings, dataDir, keys)
  const directory = join(dataDir, 'pending')
  const pause = join(directory, `pause-${scopeKey(identity, scopeId)}.json`)
  let value
  try { value = await read(pause) } catch (error) {
    if (error.code === 'ENOENT') return 'not_paused'
    if (!incompletePause(error) || !await pauseExists(pause)) throw error
  }
  const records = await snapshot(directory)
  if (value === undefined) {
    // Explicit recovery of a legacy partial pause requires persisted evidence for this exact owner and Scope.
    if (!records.some(({ value: record }) => record.kind === 'receipt' &&
        identityMatches(record.identity, identity) && record.scope_id === scopeId)) throw new Error('invalid_pending')
  } else if (value.kind !== 'pause' || !valid({ ...value, kind: 'track' }) ||
      !identityMatches(value.identity, identity) || value.scope_id !== scopeId ||
      !Number.isSafeInteger(value.target_position) || value.target_position < 1) throw new Error('invalid_pending')
  if (!await lease(directory, identity)) throw new Error('claim_busy')
  await unlink(pause)
  return 'retry_authorized'
}
