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

import type { HandoffLocale } from '../locale.ts'
import { createHash } from 'node:crypto'
import { constants } from 'node:fs'
import { access, lstat, readFile, stat } from 'node:fs/promises'
import { homedir } from 'node:os'
import { delimiter, isAbsolute, join } from 'node:path'
import type { LaunchBundle } from '../contracts.ts'
import { HandoffError } from '../errors.ts'
import type { PresentTarget, TargetAdapter, TargetDetection, TargetEnvironment } from '../targets.ts'
import { claudeConfigurationKey, createClaudeIntegration, type ClaudeIntegration, type ClaudeIntegrationOptions } from './claude-runtime.ts'
import { probeClaudeCodeCli } from './claude-code-cli-probe.ts'

/** Observations must describe the new terminal launched by the registered handler. */
export interface ClaudeCliReceiver {
  handler(executable: string, version: string, signal: AbortSignal): Promise<'ready' | 'needs_user_action' | 'unknown'>
  environment(executable: string, cwd: string, signal: AbortSignal): Promise<TargetEnvironment | undefined>
  canReadPackage(executable: string, bundle: LaunchBundle, signal: AbortSignal): Promise<boolean>
}

export interface ClaudeCodeCliOptions {
  readonly buildReceiverPrompt?: (bundle: LaunchBundle, locale?: HandoffLocale) => string
  readonly integration?: ClaudeIntegration
  readonly receiver?: ClaudeCliReceiver
  /** Actual acceptance evidence for this exact CLI version, supplied by the host. */
  readonly qualification?: { readonly targetVersion: string; readonly evidence: string }
  /** PATH used both by the receiver's installer and CLI probe. */
  readonly path?: string
}

/** Compatible with the Host contract where qualification is optional. */
export type ClaudeCliAdapter = Omit<TargetAdapter, 'qualification'> & {
  readonly qualification?: TargetAdapter['qualification']
}

/** Unqualified targets remain detectable and never expose native launch as ready. */
export function createClaudeCodeCliAdapter(options: ClaudeCodeCliOptions = {}): ClaudeCliAdapter {
  if (!options.integration) return createDefaultClaudeCodeCliAdapter({ path: options.path,
    buildReceiverPrompt: options.buildReceiverPrompt })
  const { integration, receiver, qualification, buildReceiverPrompt } = options
  const qualified = Boolean(qualification?.targetVersion?.trim() && qualification.evidence?.trim()
    && receiver && buildReceiverPrompt)
  const path = options.path ?? process.env.PATH ?? ''
  const detect = async (signal: AbortSignal): Promise<TargetDetection> => {
    const result = await probeClaudeCodeCli(signal, path)
    if (result.state !== 'present') return result
    if (!integration.configurationKey) return { state: 'unknown' }
    if (qualified && result.version !== qualification!.targetVersion) return { state: 'unsupported' }
    return { state: 'present', version: result.version, configurationKey: integration.configurationKey }
  }
  const executableFor = async (target: PresentTarget, signal: AbortSignal) => {
    signal.throwIfAborted()
    const result = await probeClaudeCodeCli(signal, path)
    signal.throwIfAborted()
    if (!qualified || !qualification || result.state !== 'present' || result.version !== target.version
      || result.version !== qualification.targetVersion || target.configurationKey !== integration.configurationKey) {
      throw new HandoffError('binding_mismatch')
    }
    return result.executable
  }

  return {
    id: 'claude-code-cli', version: '1',
    ...(qualified && qualification ? { qualification: { platform: 'darwin' as const, evidence: qualification.evidence } } : {}),
    detect,
    async checkIntegration(target, signal) {
      signal.throwIfAborted()
      if (target.configurationKey !== integration.configurationKey) throw new HandoffError('binding_mismatch')
      if (!qualified || !receiver) return integration.check(signal)
      const executable = await executableFor(target, signal)
      const handler = await receiver.handler(executable, target.version, signal)
      signal.throwIfAborted()
      if (handler === 'needs_user_action') return { state: 'needs_user_action', action: 'configure_target' }
      if (handler !== 'ready') return { state: 'unknown' }
      const result = await integration.check(signal)
      signal.throwIfAborted()
      return result
    },
    async installIntegration(target, signal) {
      if (!qualified) return { state: 'needs_user_action', action: 'configure_target' }
      await executableFor(target, signal)
      signal.throwIfAborted()
      return integration.install(signal)
    },
    async resolveEnvironment(target, sourceCwd, signal) {
      if (!qualified || !receiver) throw new HandoffError('integration_unavailable')
      const executable = await executableFor(target, signal)
      if (!isAbsolute(sourceCwd) || /[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2066-\u2069]/u.test(sourceCwd)
        || sourceCwd.split('/').includes('..') || sourceCwd.startsWith('//')) {
        throw new HandoffError('binding_mismatch')
      }
      const handler = await receiver.handler(executable, target.version, signal)
      if (handler !== 'ready') throw new HandoffError('integration_unavailable')
      const environment = await receiver.environment(executable, sourceCwd, signal)
      signal.throwIfAborted()
      if (!environment || environment.effectiveCwd !== sourceCwd || !environment.serverIdentity || !environment.scopeId
        || !environment.configurationGeneration || !environment.newSession || !environment.unsentPrefill
        || !environment.packageAccess) throw new HandoffError('integration_unavailable')
      return environment
    },
    async canReadPackage(target, bundle, signal) {
      if (!qualified || !receiver) return false
      const executable = await executableFor(target, signal)
      if (!isAbsolute(bundle.packagePath) || !/^[0-9a-f]{64}$/u.test(bundle.packageSha256)) return false
      try {
        const info = await lstat(bundle.packagePath)
        if (!info.isFile() || info.size < 1 || info.size > 50 * 1024 * 1024) return false
        await access(bundle.packagePath, constants.R_OK)
        const bytes = await readFile(bundle.packagePath, { signal })
        if (createHash('sha256').update(bytes).digest('hex') !== bundle.packageSha256) return false
        const readable = await receiver.canReadPackage(executable, bundle, signal)
        signal.throwIfAborted()
        return readable
      } catch {
        if (signal.aborted) signal.throwIfAborted()
        return false
      }
    },
    launchIntent(target, bundle, locale) {
      if (!qualified || !qualification || !buildReceiverPrompt) throw new HandoffError('integration_unavailable')
      if (target.version !== qualification.targetVersion || target.configurationKey !== integration.configurationKey
        || bundle.binding.targetId !== 'claude-code-cli' || bundle.binding.targetVersion !== target.version) {
        throw new HandoffError('binding_mismatch')
      }
      const draft = buildReceiverPrompt(bundle, locale)
      if (typeof draft !== 'string' || !draft.trim()) throw new HandoffError('payload_invalid')
      return { draft }
    },
  }
}

/** Locate the existing PowerContext executable without invoking a shell. */
async function findPowercontext(path: string): Promise<string | undefined> {
  for (const directory of path.split(delimiter)) {
    if (!isAbsolute(directory)) return undefined
    const candidate = join(directory, 'powercontext')
    try {
      await access(candidate, constants.X_OK)
      if ((await stat(candidate)).isFile()) return candidate
    } catch (error) {
      if (!['ENOENT', 'ENOTDIR', 'EACCES'].includes((error as NodeJS.ErrnoException).code ?? '')) return undefined
    }
  }
  return undefined
}

export interface ClaudeCodeCliTargetOptions extends Omit<ClaudeCodeCliOptions, 'integration'> {
  readonly integrationOptions?: Partial<ClaudeIntegrationOptions>
}

/** Await once during registry assembly. Missing CLI tools do not prevent target diagnostics. */
export async function createClaudeCodeCliTarget(options: ClaudeCodeCliTargetOptions = {}): Promise<ClaudeCliAdapter> {
  const { integrationOptions = {}, ...adapterOptions } = options
  const inherited = integrationOptions.environment ?? process.env
  const configDir = integrationOptions.configDir ?? inherited.CLAUDE_CONFIG_DIR ?? join(homedir(), '.claude')
  const path = adapterOptions.path ?? inherited.PATH ?? ''
  const environment = { ...inherited, PATH: path }
  const unavailable: ClaudeIntegration = {
    configurationKey: '',
    async check(signal) { signal.throwIfAborted(); return { state: 'needs_user_action', action: 'configure_target' } },
    async install(signal) { signal.throwIfAborted(); return { state: 'needs_user_action', action: 'configure_target' } },
  }
  try {
    const configurationKey = await claudeConfigurationKey(configDir)
    const executable = integrationOptions.powercontextExecutable ?? await findPowercontext(environment.PATH ?? '')
    let executableAvailable = false
    if (executable && isAbsolute(executable)) {
      try { await access(executable, constants.X_OK); executableAvailable = (await stat(executable)).isFile() } catch { /* unavailable */ }
    }
    if (!executable || !executableAvailable) {
      return createClaudeCodeCliAdapter({ ...adapterOptions, qualification: undefined, path,
        integration: { ...unavailable, configurationKey } })
    }
    const integration = await createClaudeIntegration({ ...integrationOptions, configDir, environment,
      powercontextExecutable: executable })
    return createClaudeCodeCliAdapter({ ...adapterOptions, path, integration })
  } catch {
    return createClaudeCodeCliAdapter({ ...adapterOptions, qualification: undefined, path, integration: unavailable })
  }
}

/** A synchronous, unqualified registry entry; no installer work occurs during registration. */
export function createDefaultClaudeCodeCliAdapter(
  options: Omit<ClaudeCodeCliTargetOptions, 'qualification' | 'receiver'> = {},
): ClaudeCliAdapter {
  // Re-observe PATH on each operation so installing PowerContext can recover without a Host restart.
  const getTarget = () => createClaudeCodeCliTarget(options)
  return {
    id: 'claude-code-cli', version: '1',
    async detect(signal) { signal.throwIfAborted(); return (await getTarget()).detect(signal) },
    async checkIntegration(present, signal) { signal.throwIfAborted(); return (await getTarget()).checkIntegration(present, signal) },
    async installIntegration(_present, signal) {
      signal.throwIfAborted()
      return { state: 'needs_user_action', action: 'configure_target' }
    },
    async resolveEnvironment(_present, _cwd, signal) { signal.throwIfAborted(); throw new HandoffError('integration_unavailable') },
    async canReadPackage(_present, _bundle, signal) { signal.throwIfAborted(); return false },
    launchIntent() { throw new HandoffError('integration_unavailable') },
  }
}
