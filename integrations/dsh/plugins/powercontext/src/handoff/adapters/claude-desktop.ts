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
import { execFile } from 'node:child_process'
import { access, readFile, realpath, stat } from 'node:fs/promises'
import { constants } from 'node:fs'
import { join } from 'node:path'
import { promisify } from 'node:util'

const run = promisify(execFile)
const BUNDLE_ID = 'com.anthropic.claudefordesktop'

/** Bundle metadata alone does not establish Code-session or handoff readiness. */
export type ClaudeDesktopBundle =
  | { state: 'present'; version: string }
  | { state: 'missing' | 'invalid' | 'unknown' | 'unsupported' }

/** Inspect one explicit app location without starting the app or accessing its private data. */
export async function inspectClaudeDesktopBundle(appPath: string, signal: AbortSignal): Promise<ClaudeDesktopBundle> {
  signal.throwIfAborted()
  if (process.platform !== 'darwin') return { state: 'unsupported' }
  try {
    const info = await stat(appPath)
    signal.throwIfAborted()
    if (!info.isDirectory()) return { state: 'invalid' }
  } catch (error) {
    signal.throwIfAborted()
    return { state: (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'unknown' }
  }

  try {
    const { stdout } = await run('/usr/bin/plutil', ['-convert', 'json', '-o', '-', '--', join(appPath, 'Contents', 'Info.plist')], {
      timeout: 3_000, maxBuffer: 256 * 1024, signal, env: { PATH: '/usr/bin:/bin', LANG: 'C' },
    })
    const info: unknown = JSON.parse(stdout)
    if (!info || typeof info !== 'object' || Array.isArray(info)) return { state: 'invalid' }
    const bundle = info as Record<string, unknown>
    const version = bundle.CFBundleShortVersionString
    const executable = bundle.CFBundleExecutable
    const types = bundle.CFBundleURLTypes
    if (bundle.CFBundleIdentifier !== BUNDLE_ID || typeof version !== 'string' || !version.trim()
      || typeof executable !== 'string' || !executable || executable === '.' || executable === '..'
      || executable.includes('/') || executable.includes('\\')
      || !Array.isArray(types) || !types.some(type => type && typeof type === 'object'
        && Array.isArray(type.CFBundleURLSchemes) && type.CFBundleURLSchemes.includes('claude'))) {
      return { state: 'invalid' }
    }
    await access(join(appPath, 'Contents', 'MacOS', executable), constants.X_OK)
    signal.throwIfAborted()
    return { state: 'present', version }
  } catch {
    signal.throwIfAborted()
    return { state: 'unknown' }
  }
}

import type { LaunchBundle } from '../contracts.ts'
import { createClaudeIntegration, type ClaudeIntegration, type ClaudeIntegrationOptions } from './claude-runtime.ts'
import { HandoffError } from '../errors.ts'
import type { IntegrationCheck, PresentTarget, TargetAdapter, TargetDetection, TargetEnvironment } from '../targets.ts'

/** Read the macOS registered URL handler without opening the application. */
export async function inspectClaudeDesktopHandler(appPath: string, signal: AbortSignal): Promise<
  'match' | 'different' | 'missing' | 'unknown'
> {
  signal.throwIfAborted()
  if (process.platform !== 'darwin') return 'unknown'
  try {
    const script = 'ObjC.import("AppKit"); const url=$.NSURL.URLWithString("claude://code/new"); '
      + 'const app=$.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL(url); '
      + 'app.isNil() ? "__NO_HANDLER__" : ObjC.unwrap(app.path);'
    const { stdout } = await run('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {
      timeout: 3_000, maxBuffer: 16 * 1024, signal, env: { PATH: '/usr/bin:/bin', LANG: 'C' },
    })
    signal.throwIfAborted()
    const path = stdout.trim()
    if (path === '__NO_HANDLER__') return 'missing'
    if (!path.startsWith('/')) return 'unknown'
    return await realpath(path) === await realpath(appPath) ? 'match' : 'different'
  } catch {
    signal.throwIfAborted()
    return 'unknown'
  }
}

/** Advisory user-scope records, not proof of the configuration a Desktop session will use. */
export async function inspectClaudePluginRegistration(configDir: string, signal: AbortSignal): Promise<
  'registered' | 'disabled' | 'missing' | 'unknown'
> {
  async function json(path: string): Promise<unknown> {
    const file = await stat(path)
    if (!file.isFile() || file.size > 1024 * 1024) throw new Error('Invalid settings')
    return JSON.parse(await readFile(path, { encoding: 'utf8', signal })) as unknown
  }
  try {
    signal.throwIfAborted()
    const settings = await json(join(configDir, 'settings.json'))
    const inventory = await json(join(configDir, 'plugins', 'installed_plugins.json'))
    signal.throwIfAborted()
    if (!settings || typeof settings !== 'object' || Array.isArray(settings)
      || !inventory || typeof inventory !== 'object' || Array.isArray(inventory)) return 'unknown'
    const enabled = (settings as { enabledPlugins?: Record<string, unknown> }).enabledPlugins
    const plugins = (inventory as { plugins?: Record<string, unknown> }).plugins
    if (!plugins || typeof plugins !== 'object' || Array.isArray(plugins)
      || (enabled !== undefined && (!enabled || typeof enabled !== 'object' || Array.isArray(enabled)))) return 'unknown'
    const item = plugins['powercontext@powercontext']
    if (item === undefined) return 'missing'
    if (!Array.isArray(item)) return 'unknown'
    if (!item.some(entry => entry && typeof entry === 'object' && entry.scope === 'user')) return 'missing'
    const state = enabled?.['powercontext@powercontext']
    if (state === false) return 'disabled'
    if (state === true) return 'registered'
    return 'unknown'
  } catch (error) {
    signal.throwIfAborted()
    return (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'unknown'
  }
}

/**
 * A trusted, version-qualified host probe. Implementations must observe the actual Desktop Code
 * runtime, not just CLI settings; package access must include the destination sandbox.
 */
export interface ClaudeDesktopRuntimeProbe {
  readonly configurationKey: string
  checkIntegration(target: PresentTarget, signal: AbortSignal): Promise<IntegrationCheck>
  resolveEnvironment(target: PresentTarget, cwd: string, signal: AbortSignal): Promise<TargetEnvironment>
  canReadPackage(target: PresentTarget, bundle: LaunchBundle, signal: AbortSignal): Promise<boolean>
}

export interface ClaudeDesktopAdapterOptions {
  buildReceiverPrompt?: (bundle: LaunchBundle, locale?: HandoffLocale) => string
  /** Evidence must come from real Desktop Code qualification for this exact app version. */
  qualification?: { readonly appVersion: string; readonly evidence: string }
  /** Supply only when the host can actually inspect the destination runtime. */
  runtime?: ClaudeDesktopRuntimeProbe
  /** A candidate shared profile; doctor results are advisory until Desktop runtime is verified. */
  integration?: ClaudeIntegration
  /** Explicit bundle selected by the host; defaults to the normal system installation. */
  appPath?: string
  /** Advisory user settings location, which may differ from Desktop's effective runtime. */
  configDir?: string
  /** Override only with a genuine platform handler observation; useful for isolated tests. */
  inspectHandler?: typeof inspectClaudeDesktopHandler
}

/** A qualified runtime must also provide the receiving PowerContext prompt hook and MCP tools. */
export function createClaudeDesktopAdapter(options: ClaudeDesktopAdapterOptions = {}): TargetAdapter {
  const { qualification, runtime, integration, buildReceiverPrompt } = options
  const appPath = options.appPath ?? '/Applications/Claude.app'
  const configurationKey = runtime?.configurationKey ?? integration?.configurationKey ?? 'claude-desktop:manual-configuration'
  const qualified = Boolean(qualification?.appVersion.trim() && qualification.evidence.trim()
    && runtime?.configurationKey.trim() && typeof buildReceiverPrompt === 'function'
    && (!integration || (integration.configurationKey.trim() && runtime.configurationKey === integration.configurationKey)))
  const detect = async (signal: AbortSignal): Promise<TargetDetection> => {
    const bundle = await inspectClaudeDesktopBundle(appPath, signal)
    if (bundle.state === 'present') return !qualified || bundle.version === qualification!.appVersion
      ? { state: 'present', version: bundle.version, configurationKey }
      : { state: 'unsupported' }
    return { state: bundle.state === 'invalid' ? 'unknown' : bundle.state }
  }
  const requireTarget = (target: PresentTarget) => {
    if (!target.version.trim() || (qualified && target.version !== qualification!.appVersion)
      || target.configurationKey !== configurationKey) {
      throw new HandoffError('binding_mismatch')
    }
  }
  return {
    id: 'claude-desktop', version: '1',
    ...(qualified ? { qualification: { platform: 'darwin' as const, evidence: qualification!.evidence } } : {}),
    detect,
    async checkIntegration(target, signal) {
      signal.throwIfAborted()
      requireTarget(target)
      if (!qualified || !runtime) return { state: 'needs_user_action', action: 'configure_target' }
      const handler = await (options.inspectHandler ?? inspectClaudeDesktopHandler)(appPath, signal)
      signal.throwIfAborted()
      if (handler === 'unknown') return { state: 'unknown' }
      if (handler !== 'match') return { state: 'needs_user_action', action: 'configure_target' }
      if (integration) {
        const shared = await integration.check(signal)
        signal.throwIfAborted()
        if (shared.state !== 'ready') return shared
      }
      const result = await runtime.checkIntegration(target, signal)
      signal.throwIfAborted()
      return result
    },
    async installIntegration(target, signal) {
      signal.throwIfAborted()
      requireTarget(target)
      // Desktop's supported plugin UI is interactive. Do not silently install CLI or write config.
      return { state: 'needs_user_action', action: 'configure_target' }
    },
    async resolveEnvironment(target, cwd, signal) {
      signal.throwIfAborted()
      requireTarget(target)
      if (!qualified || !runtime) throw new HandoffError('integration_unavailable')
      const environment = await runtime.resolveEnvironment(target, cwd, signal)
      signal.throwIfAborted()
      return environment
    },
    async canReadPackage(target, bundle, signal) {
      signal.throwIfAborted()
      requireTarget(target)
      if (!qualified || !runtime) return false
      const readable = await runtime.canReadPackage(target, bundle, signal)
      signal.throwIfAborted()
      return readable
    },
    launchIntent(target, bundle, locale) {
      requireTarget(target)
      if (!qualified || !buildReceiverPrompt) throw new HandoffError('integration_unavailable')
      if (bundle.binding.targetId !== 'claude-desktop'
        || bundle.binding.targetVersion !== target.version) throw new HandoffError('binding_mismatch')
      return { draft: buildReceiverPrompt(bundle, locale) }
    },
  }
}


/** Build Desktop's real shared doctor service for an explicitly selected candidate profile. */
export async function createClaudeDesktopAdapterWithIntegration(
  options: Omit<ClaudeDesktopAdapterOptions, 'integration' | 'configDir'> & { integrationOptions: ClaudeIntegrationOptions },
): Promise<TargetAdapter> {
  const integration = await createClaudeIntegration(options.integrationOptions)
  return createClaudeDesktopAdapter({ ...options, configDir: options.integrationOptions.configDir, integration })
}
