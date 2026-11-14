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


import type { HandoffLocale } from './locale.ts'
import type { LaunchBundle } from './contracts.ts'
import { HandoffError } from './errors.ts'

export const NATIVE_TARGET_IDS = ['codex-desktop', 'claude-desktop', 'claude-code-cli'] as const
export type NativeTargetId = typeof NATIVE_TARGET_IDS[number]

const TARGETS = [
  ['codex-desktop', 'Codex Desktop', 'desktop'],
  ['claude-desktop', 'Claude Desktop (Code)', 'desktop'],
  ['claude-code-cli', 'Claude Code CLI', 'cli'],
  ['dsh', 'DeepSeek Harness', 'host'],
  ['minimax-code', 'MiniMax Code', 'host'],
  ['zcode', 'ZCode', 'host'],
  ['kimi-code', 'Kimi Code', 'host'],
  ['hermes', 'Hermes Agent', 'host'],
  ['pi', 'Pi Coding Agent', 'host'],
  ['openclaw', 'OpenClaw', 'host'],
  ['opencode', 'OpenCode', 'host'],
  ['workbuddy', 'WorkBuddy', 'host'],
  ['bub', 'Bub', 'evaluation'],
  ['pydantic-ai', 'Pydantic AI', 'framework'],
  ['langchain', 'LangChain', 'framework'],
  ['langgraph', 'LangGraph', 'framework'],
] as const

export type TargetDetection =
  | { state: 'present'; version: string; configurationKey: string }
  | { state: 'missing' | 'unsupported' | 'unknown' }
export type PresentTarget = Extract<TargetDetection, { state: 'present' }>
/** Safe to expose to the owner: the installer configuration key stays internal. */
export type TargetDiagnostic = { state: 'present'; version: string } | { state: 'missing' | 'unsupported' | 'unknown' }

export type IntegrationCheck =
  | { state: 'ready' }
  | { state: 'missing' | 'disabled' | 'broken'; repairable: boolean }
  | { state: 'needs_user_action'; action: 'restart_target' | 'sign_in' | 'configure_target' }
  | { state: 'unknown' }

export type InstallationResult =
  | { state: 'completed' | 'failed' | 'unknown' }
  | { state: 'needs_user_action'; action: 'restart_target' | 'sign_in' | 'configure_target' }

export interface ConnectionBinding {
  readonly serverIdentity: string
  readonly connectionRef: string
  readonly connectionGeneration: number
  readonly scopeId: string
}

export interface TargetEnvironment {
  readonly effectiveCwd: string
  readonly serverIdentity: string
  readonly scopeId: string
  /** Opaque observation of the destination's effective routing configuration; no credentials. */
  readonly configurationGeneration: string
  readonly newSession: boolean
  readonly unsentPrefill: boolean
  readonly packageAccess: boolean
}

/** Only in-process, qualified adapters may construct this intent. The launcher owns the executable and argv. */
export interface NativeLaunchIntent {
  readonly draft: string
}

export interface TargetAdapter {
  readonly id: NativeTargetId
  readonly version: string
  readonly qualification?: { readonly platform: 'darwin'; readonly evidence: string }
  detect(signal: AbortSignal): Promise<TargetDetection>
  checkIntegration(target: PresentTarget, signal: AbortSignal): Promise<IntegrationCheck>
  /**
   * Use existing setup/doctor and the exact detected profile; preserve unrelated settings.
   * The configuration key identifies the actual shared installer write domain, including
   * across target IDs that use the same integration settings. Honor cancellation and settle
   * only once the command no longer writes; a lost result is unknown.
   */
  installIntegration(target: PresentTarget, signal: AbortSignal): Promise<InstallationResult>
  /** Observe the actual destination runtime's new-session routing, not manifest defaults. */
  resolveEnvironment(target: PresentTarget, sourceCwd: string, signal: AbortSignal): Promise<TargetEnvironment>
  /** Prove the particular package path is readable by the target, including its sandbox. */
  canReadPackage(target: PresentTarget, bundle: LaunchBundle, signal: AbortSignal): Promise<boolean>
  launchIntent(target: PresentTarget, bundle: LaunchBundle, locale?: HandoffLocale): NativeLaunchIntent
}

export class TargetCatalog {
  private readonly adapters = new Map<string, TargetAdapter>()
  private readonly platform: NodeJS.Platform

  constructor(adapters: readonly TargetAdapter[] = [], platform = process.platform) {
    this.platform = platform
    for (const adapter of adapters) {
      if (!(NATIVE_TARGET_IDS as readonly string[]).includes(adapter.id) || this.adapters.has(adapter.id)) {
        throw new Error('Invalid or duplicate handoff adapter')
      }
      this.adapters.set(adapter.id, adapter)
    }
  }

  list() {
    return TARGETS.map(([targetId, name, kind]) => {
      const adapter = this.adapters.get(targetId)
      const qualification = adapter?.qualification
      const qualified = qualification?.platform === this.platform && Boolean(qualification.evidence.trim())
      return {
        targetId, name, kind,
        qualification: qualified ? qualification : null,
        diagnostics: adapter ? 'available' : 'unavailable',
        availability: qualified ? 'requires_preflight' : 'unavailable',
        action: qualified ? 'preflight' : kind === 'framework' ? 'developer_setup'
          : kind === 'evaluation' ? 'evaluation_only' : 'manual_setup_or_export',
      }
    })
  }

  require(targetId: string): TargetAdapter {
    if (!TARGETS.some(([id]) => id === targetId)) throw new HandoffError('payload_invalid')
    const adapter = this.adapters.get(targetId)
    if (!adapter || adapter.qualification?.platform !== this.platform || !adapter.qualification.evidence.trim()) {
      throw new HandoffError('integration_unavailable')
    }
    return adapter
  }

  /** Detection is read-only and available even before a target has launch qualification. */
  async diagnose(targetId: string, signal: AbortSignal): Promise<TargetDiagnostic> {
    if (!TARGETS.some(([id]) => id === targetId)) throw new HandoffError('payload_invalid')
    const adapter = this.adapters.get(targetId)
    if (!adapter) throw new HandoffError('integration_unavailable')
    const result = await adapter.detect(signal)
    return result.state === 'present' ? { state: 'present', version: result.version } : { state: result.state }
  }
}
