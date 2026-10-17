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

import { createHash, randomUUID } from 'node:crypto'
import { mkdir, open, opendir, rename, rm, stat, unlink, writeFile } from 'node:fs/promises'
import { homedir } from 'node:os'
import { isAbsolute, join, resolve } from 'node:path'
import { isScopeId, MAX_CONTEXT_BYTES } from './runtime.mjs'

const SCHEMA = 'powercontext.kimi-code.runtime-observation.v1'
const FILE = /^attempt-([a-f0-9-]{36})\.json$/u
const STAGES = ['scope', 'prepare', 'capture', 'injection']
const STATES = new Set(['not_observed', 'resolved', 'ready', 'empty', 'accepted', 'rejected', 'unknown', 'skipped', 'emitted', 'failed'])
const CODES = new Set(['invalid_settings', 'authentication_failed', 'invalid_credentials', 'server_unavailable',
  'invalid_response', 'forbidden', 'not_found', 'runtime_unavailable', 'workspace_required'])
const REASONS = new Set(['disabled', 'too_large', 'deadline_exceeded', 'sensitive', 'no_context', 'not_reached'])
const STAGE_STATES = { scope: ['resolved', 'failed', 'skipped', 'not_observed'],
  prepare: ['ready', 'empty', 'failed', 'skipped', 'not_observed'],
  capture: ['accepted', 'rejected', 'unknown', 'failed', 'skipped', 'not_observed'],
  injection: ['emitted', 'skipped', 'not_observed'] }
// The Python doctor must also be able to represent persisted timestamps.
const MAX_TIMESTAMP = 253402300800000
// Retention is a soft threshold: concurrent admissions can exceed it. The separate
// directory scan budget leaves bounded room to read and prune that overflow.
const LIMIT = 64
const STALE_MS = 300000
const TRANSIENT_REMOVE_CODES = new Set(['EACCES', 'EBUSY', 'EPERM'])
const hash = value => createHash('sha256').update(value).digest('hex')

function identity(input, settings) {
  if (!isAbsolute(input.cwd ?? '')) throw new Error('workspace_required')
  if (input.session_id !== undefined && (typeof input.session_id !== 'string' ||
      !input.session_id.trim() || input.session_id.length > 256)) throw new Error('invalid_session')
  return { workspace: hash(resolve(input.cwd)), session: hash(input.session_id ?? ''),
    configuration: hash(JSON.stringify([settings.serverUrl, settings.scopeId, settings.capturePrompts, settings.useToken])) }
}

function directory() {
  const configured = process.env.KIMI_CODE_HOME
  return join(resolve(configured?.trim() ? configured : join(homedir(), '.kimi-code')), 'powercontext', 'runtime')
}

function stage(state, details = {}) {
  if (!STATES.has(state)) throw new Error('invalid_state')
  const result = { state }
  for (const name of ['code', 'reason']) {
    if ((name === 'code' ? CODES : REASONS).has(details[name])) result[name] = details[name]
  }
  for (const name of ['content_bytes', 'source_position']) {
    if (Number.isSafeInteger(details[name]) && details[name] >= 0) result[name] = details[name]
  }
  if (Number.isInteger(details.http_status) && details.http_status >= 100 && details.http_status <= 599) result.http_status = details.http_status
  return result
}

function validateStage(name, value) {
  if (!STAGE_STATES[name].includes(value?.state) ||
      value.code !== undefined && !CODES.has(value.code) ||
      value.reason !== undefined && !REASONS.has(value.reason)) throw new Error('invalid_state')
  if (['ready', 'emitted'].includes(value.state) && (!Number.isSafeInteger(value.content_bytes) ||
      value.content_bytes <= 0 || value.content_bytes > MAX_CONTEXT_BYTES) ||
      value.state === 'empty' && value.content_bytes !== 0 ||
      name === 'capture' && value.state === 'accepted' && (!Number.isSafeInteger(value.source_position) ||
      value.source_position <= 0)) throw new Error('invalid_state')
}

async function names() {
  const result = []
  let count = 0
  try {
    for await (const entry of await opendir(directory())) {
      if (++count > LIMIT * 2) throw new Error('runtime_capacity_exceeded')
      if (FILE.test(entry.name)) result.push(entry.name)
    }
  } catch (error) {
    if (error.code !== 'ENOENT') throw error
    const parent = await stat(join(directory(), '..')).catch(error => { if (error.code !== 'ENOENT') throw error })
    if (parent && !parent.isDirectory()) throw new Error('runtime_unavailable')
  }
  return result
}

async function read(name) {
  const file = await open(join(directory(), name), 'r')
  try {
    const bytes = Buffer.alloc(4097)
    const { bytesRead } = await file.read(bytes, 0, bytes.length, 0)
    if (bytesRead > 4096) throw new Error('invalid_state')
    const value = JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes.subarray(0, bytesRead)))
    if (value.schema !== SCHEMA || name !== `attempt-${value.attempt_id}.json` ||
        !Number.isSafeInteger(value.started_at) || value.started_at < 0 || value.started_at >= MAX_TIMESTAMP ||
        !(value.completed_at === null || Number.isSafeInteger(value.completed_at) &&
          value.completed_at >= value.started_at && value.completed_at < MAX_TIMESTAMP) ||
        !['workspace', 'session', 'configuration'].every(key => /^[a-f0-9]{64}$/u.test(value.identity?.[key])) ||
        !(value.scope_id === null || isScopeId(value.scope_id)) ||
        !value.stages) throw new Error('invalid_state')
    for (const name of STAGES) validateStage(name, value.stages[name])
    if (value.stages.scope.state === 'resolved' && !isScopeId(value.scope_id)) throw new Error('invalid_state')
    // Project only known fields, including from manually altered files.
    return { schema: SCHEMA, attempt_id: value.attempt_id, identity: Object.fromEntries(
      ['workspace', 'session', 'configuration'].map(key => [key, value.identity[key]])),
    started_at: value.started_at, completed_at: value.completed_at, scope_id: value.scope_id,
    stages: Object.fromEntries(STAGES.map(key => [key, stage(value.stages[key].state, value.stages[key])])) }
  } finally { await file.close() }
}

async function records() {
  const result = []
  for (const name of await names()) {
    try { result.push(await read(name)) }
    catch (error) { if (error.code !== 'ENOENT') throw error }
  }
  return result.sort((a, b) => b.started_at - a.started_at || b.attempt_id.localeCompare(a.attempt_id))
}

async function publish(record) {
  const path = join(directory(), `attempt-${record.attempt_id}.json`)
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, JSON.stringify(record), { mode: 0o600, flag: 'wx' })
    await rename(temporary, path)
  } finally { await unlink(temporary).catch(() => {}) }
}

async function removeCompleted(attemptId) {
  try { await rm(join(directory(), `attempt-${attemptId}.json`), { force: true }) }
  catch (error) {
    // A concurrent Hook can still be reading this owned record on Windows. Retention is
    // best-effort here; the capacity check below still rejects a persistently full directory.
    if (!TRANSIENT_REMOVE_CODES.has(error.code)) throw error
  }
}

export function startObservation(input, settings, onFailure) {
  const record = { schema: SCHEMA, attempt_id: randomUUID(), identity: identity(input, settings),
    scope_id: settings.scopeId, started_at: Date.now(), completed_at: null,
    stages: Object.fromEntries(STAGES.map(key => [key, stage('not_observed')])) }
  let failed = false
  let queue = (async () => {
    await mkdir(directory(), { recursive: true })
    const existing = await names()
    if (existing.length >= LIMIT) {
      const completed = (await records()).filter(item => item.completed_at !== null)
      for (const item of completed.slice(32)) {
        await removeCompleted(item.attempt_id)
      }
      if ((await names()).length >= LIMIT) throw new Error('runtime_capacity_exceeded')
    }
  })()
  function persist() {
    const snapshot = structuredClone(record)
    queue = queue.then(() => { if (!failed) return publish(snapshot) }).catch(() => {
      if (!failed) { failed = true; onFailure() }
    })
  }
  persist()
  return {
    record(name, state, details = {}) {
      if (name === 'scope' && state === 'resolved') {
        if (!isScopeId(details.scope_id)) throw new Error('invalid_state')
        record.scope_id = details.scope_id
      }
      record.stages[name] = stage(state, details); persist()
    },
    fail(name, error, write = false) {
      const rejected = error.httpStatus >= 400 && error.httpStatus < 500
      this.record(name, write && error.requestSent ? rejected ? 'rejected' : 'unknown' : 'failed',
        { code: error.code ?? 'invalid_response', http_status: error.httpStatus })
    },
    finish() {
      for (const name of STAGES) {
        if (record.stages[name].state === 'not_observed') record.stages[name] = stage('skipped', { reason: 'not_reached' })
      }
      record.completed_at = Date.now(); persist(); return queue
    },
  }
}

export async function queryObservations(input, settings) {
  const expected = identity(input, settings)
  const all = (await records()).filter(item => item.identity.workspace === expected.workspace &&
    (input.session_id === undefined || item.identity.session === expected.session))
  const latest = all[0]
  const result = { schema: 'powercontext.kimi-code.runtime-status.v1',
    selection: input.session_id === undefined ? 'workspace_latest' : 'session' }
  if (!latest) return { ...result, status: 'not_observed' }
  if (latest.identity.configuration !== expected.configuration || settings.scopeId && latest.scope_id !== settings.scopeId) {
    return { ...result, status: 'configuration_mismatch' }
  }
  return { ...result, status: 'observed', attempt_id: latest.attempt_id,
    started_at: latest.started_at, completed_at: latest.completed_at, scope_id: latest.scope_id,
    freshness: latest.completed_at === null ? 'incomplete' : Date.now() < latest.started_at || Date.now() - latest.started_at > STALE_MS ? 'stale' : 'recent',
    stages: latest.stages }
}
