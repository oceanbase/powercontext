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

export const DASHBOARD_ROUTE = '/powercontext/dashboard'
export const DASHBOARD_SCHEMA = 'powercontext.dsh-dashboard.v1'

export type DashboardState =
  | 'available' | 'disabled' | 'authentication' | 'server_unavailable' | 'load_unavailable'
  | 'session_unavailable' | 'scope_unresolved'

export interface DashboardPayload {
  schema: typeof DASHBOARD_SCHEMA
  state: DashboardState
  /** Present only when a verified local Dashboard route can be opened. No credentials. */
  url?: string
  session_id: string | null
  scope_id: string | null
}

const STATES = new Set<DashboardState>([
  'available', 'disabled', 'authentication', 'server_unavailable', 'load_unavailable',
  'session_unavailable', 'scope_unresolved',
])

export function parseDashboardPayload(value: unknown): DashboardPayload | null {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return null
  const item = value as Record<string, unknown>
  if (item.schema !== DASHBOARD_SCHEMA || !STATES.has(item.state as DashboardState)) return null
  const sessionId = item.session_id === null || typeof item.session_id === 'string' ? item.session_id : undefined
  const scopeId = item.scope_id === null || typeof item.scope_id === 'string' ? item.scope_id : undefined
  if (sessionId === undefined || scopeId === undefined) return null
  const url = item.url
  if (item.state === 'available') {
    if (typeof url !== 'string') return null
    try {
      const parsed = new URL(url)
      if (!['http:', 'https:'].includes(parsed.protocol) || parsed.username || parsed.password
        || !parsed.pathname.endsWith('/dashboard/home') || parsed.hash) return null
      if ([...parsed.searchParams.keys()].some(key => key !== 'scope')) return null
      const scopes = parsed.searchParams.getAll('scope')
      if (scopes.length > 1 || (scopes.length === 1 && !/^[a-zA-Z0-9_-]{1,128}$/.test(scopes[0]!))) return null
      if (scopes.length === 0 && scopeId !== null) return null
      if (scopes.length === 1 && scopes[0] !== scopeId) return null
    } catch { return null }
  } else if (url !== undefined) return null
  return {
    schema: DASHBOARD_SCHEMA, state: item.state as DashboardState,
    ...(typeof url === 'string' ? { url } : {}),
    session_id: sessionId, scope_id: scopeId,
  }
}
