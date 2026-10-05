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
import { mkdir, open, opendir, rename, unlink, writeFile } from 'node:fs/promises'
import { isAbsolute, join, resolve } from 'node:path'
import { bindingKeys, isScopeId, sessionIdentity, sha256 } from './scope.mjs'
import { failureCode, HOOK_BUDGET_MS, REQUEST_TIMEOUT_MS, serverOrigin } from './transport.mjs'

const SCHEMA = 'powercontext.zcode.runtime-observation.v1'
const FILE = /^attempt-[a-f0-9-]{36}\.json$/u
const STATES = new Set(['not_observed', 'running', 'resolved', 'ready', 'empty', 'accepted', 'rejected', 'unknown',
  'skipped', 'emitted', 'failed', 'cursor_reached', 'pending'])
const STAGES = ['scope', 'prepare', 'capture', 'context_output', 'flush']
const HARD_LIMIT = 256
const RETAIN = 64
const STALE_MS = 300_000

export function observationIdentity(input, settings, dataDir, keys = bindingKeys(input, settings)) {
  if (!dataDir || !isAbsolute(dataDir)) throw new Error('runtime_data_unavailable')
  return {
    endpoint: sha256(serverOrigin(settings)), profile: sha256(resolve(dataDir)),
    session: sha256(sessionIdentity(input) ?? ''),
    workspace: keys.find(key => key.kind === 'workspace')?.external_id ?? sha256(`remote:${settings.explicitScopeId ?? ''}`),
  }
}

export function stageResult(state, details = {}) {
  const result = { state, observed_at: Date.now() }
  if (!STATES.has(state)) throw new Error('invalid_state')
  for (const name of ['code', 'reason', 'query_source']) {
    if (typeof details[name] === 'string' && /^[a-z][a-z0-9_]{0,63}$/u.test(details[name])) result[name] = details[name]
  }
  for (const name of ['content_bytes', 'source_position', 'current_cursor']) {
    if (Number.isSafeInteger(details[name]) && details[name] >= 0) result[name] = details[name]
  }
  if (Number.isInteger(details.http_status) && details.http_status >= 100 && details.http_status <= 599) result.http_status = details.http_status
  return result
}

export function failureResult(error, write = false) {
  const rejected = error.httpStatus >= 400 && error.httpStatus < 500
  return stageResult(write ? rejected ? 'rejected' : error.requestSent ? 'unknown' : 'failed' : 'failed', {
    code: failureCode(error), http_status: error.httpStatus,
  })
}

async function atomicJson(path, value) {
  const temporary = `${path}.${randomUUID()}.tmp`
  try {
    await writeFile(temporary, JSON.stringify(value), { mode: 0o600 })
    await rename(temporary, path)
  } finally { await unlink(temporary).catch(() => {}) }
}

function validateRecord(record) {
  if (record?.schema !== SCHEMA) throw new Error(record?.schema ? 'unsupported_state' : 'invalid_state')
  if (!/^[a-f0-9-]{36}$/u.test(record.attempt_id) || !Number.isSafeInteger(record.started_at) ||
      !(record.completed_at === null || Number.isSafeInteger(record.completed_at)) ||
      !['UserPromptSubmit', 'SessionStart', 'Stop'].includes(record.event) ||
      Object.values(record.identity ?? {}).length !== 4 ||
      !['endpoint', 'profile', 'session', 'workspace'].every(name => /^[a-f0-9]{64}$/u.test(record.identity[name]))) {
    throw new Error('invalid_state')
  }
  // Never echo additional fields from a tampered state file.
  const config = record.config
  if (!config || !['installed', 'environment', 'default'].includes(config.server_url_source) ||
      typeof config.capture_prompts !== 'boolean' || typeof config.authorization_configured !== 'boolean') throw new Error('invalid_state')
  const endpoint = serverOrigin({ serverUrl: config.server_url, allowInsecureHttp: config.allow_insecure_http === true })
  if (sha256(endpoint) !== record.identity.endpoint) throw new Error('invalid_state')
  const stages = {}
  for (const stage of STAGES) {
    const value = record.stages?.[stage]
    if (!value || !Number.isSafeInteger(value.observed_at)) throw new Error('invalid_state')
    stages[stage] = { ...stageResult(value.state, value), observed_at: value.observed_at }
  }
  return {
    schema: SCHEMA, attempt_id: record.attempt_id, event: record.event,
    event_source: ['startup', 'clear', 'resume', 'compact'].includes(record.event_source) ? record.event_source : null,
    identity: Object.fromEntries(['endpoint', 'profile', 'session', 'workspace'].map(name => [name, record.identity[name]])),
    started_at: record.started_at, completed_at: record.completed_at,
    scope_id: isScopeId(record.scope_id) ? record.scope_id : null,
    config: { server_url: endpoint, server_url_source: config.server_url_source, capture_prompts: config.capture_prompts,
      allow_insecure_http: config.allow_insecure_http === true, authorization_configured: config.authorization_configured,
      boundary_flush: config.boundary_flush === true,
      hook_budget_ms: record.event === 'Stop' ? 1000 : HOOK_BUDGET_MS,
      request_timeout_ms: record.event === 'Stop' ? 800 : REQUEST_TIMEOUT_MS },
    stages,
  }
}

async function records(directory) {
  let names
  try { names = await recordNames(directory) }
  catch (error) { if (error.code === 'ENOENT') return { entries: [], issues: [] }; throw error }
  if (names.length > HARD_LIMIT) throw new Error('runtime_capacity_exceeded')
  const entries = [], issues = []
  for (const name of names) {
    const path = join(directory, name)
    let file
    try {
      file = await open(path, 'r')
      const buffer = Buffer.alloc(65_537)
      const { bytesRead } = await file.read(buffer, 0, buffer.length, 0)
      if (bytesRead > 65_536) throw new Error('invalid_state')
      const record = validateRecord(JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(buffer.subarray(0, bytesRead))))
      if (name !== `attempt-${record.attempt_id}.json`) throw new Error('invalid_state')
      entries.push({ path, record })
    } catch (error) {
      if (error.code !== 'ENOENT') issues.push(error.message === 'unsupported_state' ? 'unsupported_state' : 'invalid_state')
    }
    finally { await file?.close().catch(() => {}) }
  }
  entries.sort((a, b) => b.record.started_at - a.record.started_at || b.record.attempt_id.localeCompare(a.record.attempt_id))
  return { entries, issues: [...new Set(issues)] }
}

async function recordNames(directory) {
  const names = []
  let scanned = 0
  for await (const entry of await opendir(directory)) {
    // Bound enumeration too, including orphan temporaries and unowned files.
    if (++scanned > HARD_LIMIT * 2) throw new Error('runtime_capacity_exceeded')
    if (FILE.test(entry.name)) names.push(entry.name)
    if (names.length > HARD_LIMIT) throw new Error('runtime_capacity_exceeded')
  }
  return names
}

async function maintain(directory) {
  const page = await records(directory)
  let completed = 0
  const retained = []
  for (const entry of page.entries) {
    if (entry.record.completed_at !== null && ++completed > RETAIN) {
      try { await unlink(entry.path) }
      catch (error) {
        if (error.code !== 'ENOENT') { page.issues.push('maintenance_failed'); retained.push(entry) }
      }
    } else retained.push(entry)
  }
  return { entries: retained, issues: [...new Set(page.issues)] }
}

export async function startObservation(input, settings, keys, dataDir = process.env.ZCODE_PLUGIN_DATA) {
  const identity = observationIdentity(input, settings, dataDir, keys)
  const directory = join(dataDir, 'runtime')
  await mkdir(directory, { recursive: true })
  const names = await recordNames(directory)
  // One metadata listing per attempt; full maintenance runs only on query or saturation.
  if (names.length >= HARD_LIMIT) {
    await maintain(directory)
    if ((await recordNames(directory)).length >= HARD_LIMIT) throw new Error('runtime_capacity_exceeded')
  }
  const id = randomUUID(), path = join(directory, `attempt-${id}.json`)
  const record = {
    schema: SCHEMA, attempt_id: id, event: input.hookEventName ?? input.hook_event_name,
    event_source: ['startup', 'clear', 'resume', 'compact'].includes(input.source) ? input.source : null,
    started_at: Date.now(), completed_at: null, identity, scope_id: null,
    config: { server_url: serverOrigin(settings), server_url_source: settings.serverUrlSource,
      capture_prompts: settings.capturePrompts, authorization_configured: Boolean(settings.authorization),
      allow_insecure_http: settings.allowInsecureHttp, boundary_flush: settings.boundaryFlush === true,
      hook_budget_ms: (input.hookEventName ?? input.hook_event_name) === 'Stop' ? 1000 : HOOK_BUDGET_MS,
      request_timeout_ms: (input.hookEventName ?? input.hook_event_name) === 'Stop' ? 800 : REQUEST_TIMEOUT_MS },
    stages: Object.fromEntries(STAGES.map(stage => [stage, stageResult('not_observed')])),
  }
  await atomicJson(path, record)
  return {
    async stage(name, result, scopeId) {
      if (!STAGES.includes(name)) throw new Error('invalid_state')
      record.stages[name] = result
      if (scopeId !== undefined) record.scope_id = scopeId
      await atomicJson(path, record)
    },
    async finish() { record.completed_at = Date.now(); await atomicJson(path, record) },
  }
}

export async function queryObservations(input, settings, dataDir, latest = false) {
  const identity = observationIdentity(input, settings, dataDir)
  const { entries, issues } = await maintain(join(dataDir, 'runtime'))
  let match = entries.find(({ record }) => ['endpoint', 'profile', 'workspace', ...(latest ? [] : ['session'])]
    .every(name => record.identity[name] === identity[name]))?.record
  let mismatch = Boolean(match && (match.config.capture_prompts !== settings.capturePrompts ||
    match.config.boundary_flush !== (settings.boundaryFlush === true) ||
    match.config.authorization_configured !== Boolean(settings.authorization) ||
    settings.explicitScopeId && match.scope_id && match.scope_id !== settings.explicitScopeId))
  if (!match) {
    match = entries.find(({ record }) => ['profile', 'workspace', ...(latest ? [] : ['session'])]
      .every(name => record.identity[name] === identity[name]))?.record
    mismatch = Boolean(match)
  }
  return { schema: 'powercontext.zcode.runtime-status.v1', status: mismatch ? 'configuration_mismatch' : match ? 'observed' : issues.length ? 'invalid_state' : 'not_observed',
    selection: latest ? 'workspace_latest' : 'session_exact', issues,
    ...(match ? { stale: Date.now() - match.started_at > STALE_MS, incomplete: match.completed_at === null, observation: match } : {}),
  }
}
