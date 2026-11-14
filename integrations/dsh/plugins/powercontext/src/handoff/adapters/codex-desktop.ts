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
import { execFile, spawn } from 'node:child_process'
import { access, realpath } from 'node:fs/promises'
import { constants } from 'node:fs'
import { homedir } from 'node:os'
import { dirname, join, isAbsolute } from 'node:path'
import { promisify } from 'node:util'
import type { LaunchBundle } from '../contracts.ts'
import { HandoffError } from '../errors.ts'
import type { InstallationResult, IntegrationCheck, PresentTarget, TargetAdapter, TargetEnvironment } from '../targets.ts'

const execFileAsync = promisify(execFile)
const DEFAULT_APP = '/Applications/ChatGPT.app'
const CLI_PATH = 'Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex'

/** These operations must observe the Desktop runtime, not just an independent CLI process. */
export interface CodexDesktopRuntime {
  /** Shared installer write domain, including other adapters that modify the same Codex configuration. */
  readonly configurationKey: string
  checkIntegration(target: PresentTarget, signal: AbortSignal): Promise<IntegrationCheck>
  installIntegration(target: PresentTarget, signal: AbortSignal): Promise<InstallationResult>
  resolveEnvironment(target: PresentTarget, cwd: string, signal: AbortSignal): Promise<TargetEnvironment>
  canReadPackage(target: PresentTarget, bundle: LaunchBundle, signal: AbortSignal): Promise<boolean>
}

export interface CodexDesktopAdapterOptions {
  readonly buildReceiverPrompt?: (bundle: LaunchBundle, locale?: HandoffLocale) => string
  /** Qualification must be established by real host acceptance, for this exact version. */
  readonly qualification?: { readonly evidence: string; readonly targetVersion: string }
  /** Optional verified Desktop runtime; without one the built-in probe fails closed. */
  readonly runtime?: CodexDesktopRuntime
  readonly appPath?: string
  readonly inspectHandler?: typeof inspectCodexDesktopHandler
  /** Explicit setup inputs supplied by the host after resolving the intended connection. */
  readonly setup?: { readonly executable: string; readonly source?: string; readonly serverUrl: string }
}

/** Inspect the current macOS URL handler without opening a Desktop window. */
export async function inspectCodexDesktopHandler(appPath: string, signal: AbortSignal): Promise<
  'match' | 'different' | 'missing' | 'unknown'
> {
  signal.throwIfAborted()
  if (process.platform !== 'darwin') return 'unknown'
  try {
    const script = 'ObjC.import("AppKit"); const url=$.NSURL.URLWithString("codex://threads/new"); '
      + 'const app=$.NSWorkspace.sharedWorkspace.URLForApplicationToOpenURL(url); '
      + 'app.isNil() ? "__NO_HANDLER__" : ObjC.unwrap(app.path);'
    const { stdout } = await execFileAsync('/usr/bin/osascript', ['-l', 'JavaScript', '-e', script], {
      signal, timeout: 3_000, maxBuffer: 16 * 1024, encoding: 'utf8', env: { PATH: '/usr/bin:/bin', LANG: 'C' },
    })
    signal.throwIfAborted()
    const path = stdout.trim()
    if (path === '__NO_HANDLER__') return 'missing'
    if (!isAbsolute(path)) return 'unknown'
    return await realpath(path) === await realpath(appPath) ? 'match' : 'different'
  } catch {
    signal.throwIfAborted()
    return 'unknown'
  }
}

async function run(executable: string, args: string[], signal: AbortSignal): Promise<string> {
  const { stdout } = await execFileAsync(executable, args, {
    signal, timeout: 5_000, maxBuffer: 1024 * 1024, encoding: 'utf8',
  })
  return stdout
}

function bundleVersion(value: unknown): string | undefined {
  if (!value || typeof value !== 'object') return undefined
  const info = value as Record<string, unknown>
  const urls = info.CFBundleURLTypes
  const hasScheme = Array.isArray(urls) && urls.some(item => item && typeof item === 'object'
    && Array.isArray(item.CFBundleURLSchemes) && item.CFBundleURLSchemes.includes('codex'))
  if (info.CFBundleIdentifier !== 'com.openai.codex' || !hasScheme
    || typeof info.CFBundleShortVersionString !== 'string' || !info.CFBundleShortVersionString.trim()
    || typeof info.CFBundleVersion !== 'string' || !info.CFBundleVersion.trim()) return undefined
  return `${info.CFBundleShortVersionString} (${info.CFBundleVersion})`
}

function validServerUrl(value: string): boolean {
  try {
    const url = new URL(value)
    return (url.protocol === 'http:' || url.protocol === 'https:') && !!url.hostname
      && !url.username && !url.password && !url.search && !url.hash
  } catch {
    return false
  }
}

/** Qualification is supplied by the integrator; local probing alone is never qualification. */
export function createCodexDesktopAdapter(options: CodexDesktopAdapterOptions): TargetAdapter {
  const app = options.appPath ?? DEFAULT_APP
  const codexHome = process.env.CODEX_HOME || join(homedir(), '.codex')
  const qualification = options.qualification && { ...options.qualification }
  const buildReceiverPrompt = options.buildReceiverPrompt
  if (!isAbsolute(app) || (options.qualification && (!options.qualification.evidence.trim()
    || !options.qualification.targetVersion.trim() || typeof options.buildReceiverPrompt !== 'function'))
    || (!options.runtime && !isAbsolute(codexHome)) || (options.runtime && !options.runtime.configurationKey.trim())
    || (options.buildReceiverPrompt !== undefined && typeof options.buildReceiverPrompt !== 'function')
    || (options.setup && (!isAbsolute(options.setup.executable) || (options.setup.source !== undefined && !isAbsolute(options.setup.source))
      || !validServerUrl(options.setup.serverUrl)))) {
    throw new Error('Codex Desktop requires valid application, qualification, configuration and receiver prompt')
  }
  const cli = join(app, CLI_PATH)
  const configurationKey = options.runtime?.configurationKey ?? `codex:${codexHome}`
  const env = { ...process.env, CODEX_HOME: codexHome, PATH: `${dirname(cli)}:${process.env.PATH ?? '/usr/bin:/bin'}` }
  const requireTarget = (target: PresentTarget) => {
    if (!target.version.trim() || (qualification && target.version !== qualification.targetVersion)
      || target.configurationKey !== configurationKey) throw new HandoffError('binding_mismatch')
  }
  const inspectHandler = options.inspectHandler ?? inspectCodexDesktopHandler

  const adapter: TargetAdapter = {
    id: 'codex-desktop',
    version: '1',
    ...(qualification ? { qualification: { platform: 'darwin' as const, evidence: qualification.evidence } } : {}),
    async detect(signal) {
      signal.throwIfAborted()
      if (process.platform !== 'darwin') return { state: 'unsupported' }
      try {
        await access(app, constants.F_OK)
      } catch (error) {
        signal.throwIfAborted()
        return { state: (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'missing' : 'unknown' }
      }
      try {
        const info = JSON.parse(await run('/usr/bin/plutil', ['-convert', 'json', '-o', '-', join(app, 'Contents/Info.plist')], signal))
        const appVersion = bundleVersion(info)
        if (!appVersion) return { state: 'unsupported' }
        await access(cli, constants.X_OK)
        const cliVersion = (await run(cli, ['--version'], signal)).trim()
        if (!/^codex-cli [^\s]+$/u.test(cliVersion)) return { state: 'unknown' }
        const version = `${appVersion}; ${cliVersion}`
        if (qualification && version !== qualification.targetVersion) return { state: 'unsupported' }
        return { state: 'present', version, configurationKey }
      } catch {
        signal.throwIfAborted()
        return { state: 'unknown' }
      }
    },
    async checkIntegration(target, signal) {
      signal.throwIfAborted()
      if (!qualification) return { state: 'needs_user_action', action: 'configure_target' }
      requireTarget(target)
      const handler = await inspectHandler(app, signal)
      signal.throwIfAborted()
      if (handler === 'unknown') return { state: 'unknown' }
      if (handler !== 'match') return { state: 'needs_user_action', action: 'configure_target' }
      if (options.runtime) return options.runtime.checkIntegration(target, signal)
      try {
        const { stdout } = await execFileAsync(cli, ['plugin', 'list', '--json'], {
          signal, timeout: 10_000, maxBuffer: 1024 * 1024, encoding: 'utf8', env,
        })
        const result: unknown = JSON.parse(stdout)
        if (!result || typeof result !== 'object' || !Array.isArray((result as { installed?: unknown }).installed)) {
          return { state: 'unknown' }
        }
        // This CLI list is not the Desktop session's effective profile or hook trust.
        return { state: 'needs_user_action', action: 'configure_target' }
      } catch {
        signal.throwIfAborted()
        return { state: 'unknown' }
      }
    },
    async installIntegration(target, signal) {
      signal.throwIfAborted()
      if (!qualification) return { state: 'needs_user_action', action: 'configure_target' }
      requireTarget(target)
      if (options.runtime) return options.runtime.installIntegration(target, signal)
      const setup = options.setup
      if (!setup) return { state: 'needs_user_action', action: 'configure_target' }
      // The caller has already obtained installation consent. Use fixed argv and a
      // dedicated process group; a partial or interrupted setup is indeterminate.
      return new Promise(resolve => {
        const child = spawn(setup.executable, ['setup', 'codex', ...(setup.source ? ['--source', setup.source] : []),
          '--server-url', setup.serverUrl, '--json'], { env, stdio: 'ignore', shell: false, detached: true })
        let started = false
        let interrupted = false
        const stop = () => {
          interrupted = true
          if (child.pid) {
            try { process.kill(-child.pid, 'SIGKILL') } catch { /* process already exited */ }
          }
        }
        signal.addEventListener('abort', stop, { once: true })
        const timer = setTimeout(stop, 120_000)
        child.once('spawn', () => { started = true; if (signal.aborted) stop() })
        child.once('error', () => { clearTimeout(timer); signal.removeEventListener('abort', stop); resolve({ state: started ? 'unknown' : 'failed' }) })
        child.once('close', code => {
          clearTimeout(timer)
          signal.removeEventListener('abort', stop)
          resolve(!interrupted && code === 0
            ? { state: 'needs_user_action', action: 'configure_target' } : { state: 'unknown' })
        })
      })
    },
    async resolveEnvironment(target, cwd, signal) {
      signal.throwIfAborted()
      requireTarget(target)
      if (!qualification || !options.runtime) throw new HandoffError('integration_unavailable')
      return options.runtime.resolveEnvironment(target, cwd, signal)
    },
    async canReadPackage(target, bundle, signal) {
      signal.throwIfAborted()
      requireTarget(target)
      if (!qualification || !options.runtime) return false
      if (await inspectHandler(app, signal) !== 'match') { signal.throwIfAborted(); return false }
      signal.throwIfAborted()
      return options.runtime.canReadPackage(target, bundle, signal)
    },
    launchIntent(target, bundle, locale) {
      requireTarget(target)
      if (!qualification || target.version !== qualification.targetVersion || !buildReceiverPrompt) {
        throw new HandoffError('integration_unavailable')
      }
      if (bundle.binding.targetId !== 'codex-desktop' || bundle.binding.targetVersion !== target.version) {
        throw new HandoffError('binding_mismatch')
      }
      const draft = buildReceiverPrompt(bundle, locale)
      if (typeof draft !== 'string' || !draft.trim()) throw new HandoffError('payload_invalid')
      return { draft }
    },
  }
  return adapter
}
