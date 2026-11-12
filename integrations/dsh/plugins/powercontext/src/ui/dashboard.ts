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

import { DASHBOARD_ROUTE, parseDashboardPayload, type DashboardPayload } from '../dashboard-contract.ts'
import type { FetchLike } from './status-controller.ts'
import type { TextKey } from './locale.ts'

export type DashboardRead = DashboardPayload | { state: 'bridge_unavailable' }

export const dashboardTextKey: Record<DashboardRead['state'], TextKey> = {
  available: 'dashboardOpen',
  disabled: 'dashboardDisabled',
  authentication: 'dashboardAuthentication',
  server_unavailable: 'dashboardServerUnavailable',
  load_unavailable: 'dashboardLoadUnavailable',
  session_unavailable: 'dashboardSessionUnavailable',
  scope_unresolved: 'dashboardScopeUnresolved',
  bridge_unavailable: 'dashboardBridgeUnavailable',
}

function isLoopbackWebSite({ protocol, hostname }: Pick<URL, 'protocol' | 'hostname'>): boolean {
  return (protocol === 'http:' || protocol === 'https:')
    && (hostname === 'localhost' || hostname === 'localhost.' || hostname === '[::1]'
      || /^127(?:\.\d+){3}$/.test(hostname))
}

/** Web's iframe needs a same-site context for the Dashboard's Strict session cookie. */
export function hasMismatchedLoopbackSite(dsh: Pick<URL, 'protocol' | 'hostname'>, dashboardUrl: string): boolean {
  const dashboard = new URL(dashboardUrl)
  return isLoopbackWebSite(dsh) && isLoopbackWebSite(dashboard)
    && (dsh.protocol !== dashboard.protocol || dsh.hostname !== dashboard.hostname)
}

/** The browser reads a credential-free URL selected by the authenticated Host. */
export async function readDashboard(
  sessionId: string | undefined,
  fetcher: FetchLike = fetch,
  signal?: AbortSignal,
): Promise<DashboardRead> {
  const url = sessionId === undefined ? DASHBOARD_ROUTE
    : `${DASHBOARD_ROUTE}?${new URLSearchParams({ session_id: sessionId })}`
  try {
    const response = await fetcher(url, {
      method: 'GET', headers: { Accept: 'application/json' }, credentials: 'same-origin', signal,
    })
    if (!response.ok) return { state: 'bridge_unavailable' }
    const payload: unknown = await response.json()
    return parseDashboardPayload(payload) ?? { state: 'bridge_unavailable' }
  } catch {
    return { state: 'bridge_unavailable' }
  }
}
