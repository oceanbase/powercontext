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
import { mkdirSync, readFileSync, renameSync, rmSync, statSync, writeFileSync } from 'node:fs'
import { dirname, join } from 'node:path'
import { normalizeServerUrl } from '../../powercontext/src/transport.ts'

export type RecordValue = Record<string, unknown>
export const record = (value: unknown): RecordValue | undefined =>
  value && typeof value === 'object' && !Array.isArray(value) ? value as RecordValue : undefined

interface Identity {
  server_id: string
  authentication: 'disabled' | 'authenticated'
  access_mode: 'disabled' | 'enforced'
  principal: { type: 'user' | 'service'; id: string } | null
}

export function identityOf(value: unknown): Identity | undefined {
  const data = record(value)
  if (!data || typeof data.server_id !== 'string' || !data.server_id || data.server_id.length > 128
    || (data.access_mode !== 'disabled' && data.access_mode !== 'enforced')) return
  const { server_id, access_mode, authentication } = data
  if (authentication === 'disabled' && access_mode === 'disabled' && data.principal === null) {
    return { server_id, authentication, access_mode, principal: null }
  }
  const principal = record(data.principal)
  if (authentication === 'authenticated' && principal && (principal.type === 'user' || principal.type === 'service')
    && typeof principal.id === 'string' && principal.id.length > 0 && principal.id.length <= 255) {
    return { server_id, authentication, access_mode, principal: { type: principal.type, id: principal.id } }
  }
}

export interface Binding {
  pet_id: string
  scope_id: string
  generation: string
}

export interface Settings {
  enabled: boolean
  server_url: string
  identity: Identity
  bindings: Binding[]
}

export function decodeSettings(raw: unknown): Settings | undefined {
  const data = record(raw)
  if (data?.version !== 1 || typeof data.enabled !== 'boolean' || typeof data.server_url !== 'string'
    || !Array.isArray(data.bindings) || !data.bindings.length || data.bindings.length > 16) return
  const identity = identityOf(data.identity)
  if (!identity) return
  const server_url = normalizeServerUrl(data.server_url)
  // Do not send dialogue to an unauthenticated non-loopback destination.
  if (identity.authentication === 'disabled' && !['localhost', '127.0.0.1', '[::1]'].includes(new URL(server_url).hostname)) return
  const bindings: Binding[] = []
  for (const row of data.bindings) {
    const binding = record(row)
    if (!binding || typeof binding.pet_id !== 'string' || !binding.pet_id.trim() || binding.pet_id.length > 256
      || typeof binding.scope_id !== 'string' || !binding.scope_id.trim() || binding.scope_id.length > 256
      || typeof binding.generation !== 'string' || !/^[a-f0-9]{32}$/.test(binding.generation)
      || bindings.some(b => b.pet_id === binding.pet_id || b.scope_id === binding.scope_id || b.generation === binding.generation)) return
    bindings.push({ pet_id: binding.pet_id, scope_id: binding.scope_id, generation: binding.generation })
  }
  return { enabled: data.enabled, server_url, identity, bindings }
}

export function readSettings(path: string): { settings?: Settings; state: 'off' | 'invalid' | 'enabled' } {
  try {
    if (process.platform !== 'win32' && (statSync(path).mode & 0o077) !== 0) return { state: 'invalid' }
    const settings = decodeSettings(JSON.parse(readFileSync(path, 'utf8')))
    return settings ? { settings, state: settings.enabled ? 'enabled' : 'off' } : { state: 'invalid' }
  } catch (error) {
    return { state: (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'off' : 'invalid' }
  }
}

export function privateDirectory(path: string): void {
  mkdirSync(path, { recursive: true, mode: 0o700 })
  if (process.platform !== 'win32' && (statSync(path).mode & 0o077) !== 0) throw new Error('private_directory_required')
}

export function writeStatus(path: string, state: RecordValue): void {
  const temp = join(dirname(path), '.status-' + randomUUID())
  try {
    writeFileSync(temp, JSON.stringify(state), { mode: 0o600, flag: 'wx' })
    renameSync(temp, path)
  } finally {
    rmSync(temp, { force: true })
  }
}
