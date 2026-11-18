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

export const UPDATE_ROUTE = '/powercontext/updates'
export const UPDATE_ACTION_HEADER = 'x-powercontext-update-action'
export const UPDATE_PLAN_HEADER = 'x-powercontext-update-plan'
export const RELEASES_URL = 'https://github.com/oceanbase/powercontext/releases'

export const UPDATE_PHASES = [
  'idle', 'checking', 'current', 'available', 'manual', 'server_first', 'artifact_unavailable',
  'incompatible', 'held', 'check_failed', 'installing', 'restart_required', 'updated', 'restored',
  'install_failed', 'busy', 'unconfirmed',
] as const
export type UpdatePhase = typeof UPDATE_PHASES[number]
export const SERVER_UPDATE_STATES = ['unknown', 'current', 'available', 'artifact_unavailable'] as const

export interface UpdateSnapshot {
  phase: UpdatePhase
  plugin_version: string | null
  plugin_target: string | null
  server_target: string | null
  server_state: typeof SERVER_UPDATE_STATES[number]
  plan: string | null
  release_tag: string | null
}

/** Releases use the same numeric version in Python and npm, with different RC spellings. */
export function releaseVersion(value: unknown) {
  if (typeof value !== 'string' || value.length > 64) return null
  const parts = /^(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})(?:(?:(a|b|rc)|-(alpha|beta|rc)\.)(0|[1-9]\d{0,8}))?$/.exec(value)
  if (!parts) return null
  const stage = parts[4] ?? ({ alpha: 'a', beta: 'b', rc: 'rc' } as Record<string, string>)[parts[5]]
  const core = parts.slice(1, 4).join('.')
  const npmStage = stage === 'a' ? 'alpha' : stage === 'b' ? 'beta' : stage
  return {
    python: stage ? core + stage + parts[6] : core,
    npm: stage ? core + '-' + npmStage + '.' + parts[6] : core,
    prerelease: Boolean(stage),
    order: [...parts.slice(1, 4).map(Number), stage === 'a' ? 0 : stage === 'b' ? 1 : stage === 'rc' ? 2 : 3, Number(parts[6] ?? 0)],
  }
}

export function compareVersions(a: NonNullable<ReturnType<typeof releaseVersion>>, b: NonNullable<ReturnType<typeof releaseVersion>>): number {
  return a.order.map((part, index) => part - b.order[index]).find(part => part !== 0) ?? 0
}

export function parseUpdateSnapshot(value: unknown): UpdateSnapshot | null {
  if (typeof value !== 'object' || value === null) return null
  const item = value as Partial<UpdateSnapshot>
  if (!UPDATE_PHASES.includes(item.phase!) || !SERVER_UPDATE_STATES.includes(item.server_state!)) return null
  const version = (v: unknown): string | null => typeof v === 'string' && /^[\da-zA-Z.+-]{1,64}$/.test(v) ? v : null
  return {
    phase: item.phase!,
    plugin_version: version(item.plugin_version),
    plugin_target: releaseVersion(item.plugin_target)?.npm ?? null,
    server_target: releaseVersion(item.server_target)?.python ?? null,
    server_state: item.server_state!,
    plan: typeof item.plan === 'string' && /^[a-f0-9-]{36}$/.test(item.plan) ? item.plan : null,
    release_tag: typeof item.release_tag === 'string' && /^powercontext-v[\da-z.]{1,64}$/.test(item.release_tag) ? item.release_tag : null,
  }
}
