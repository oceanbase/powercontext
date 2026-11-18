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

import { spawn } from 'node:child_process'
import { createHash } from 'node:crypto'
import { homedir } from 'node:os'
import { realpath } from 'node:fs/promises'
import { dirname, isAbsolute, join, resolve } from 'node:path'
import type { InstallationResult, IntegrationCheck } from '../targets.ts'

/** Resolve symlinks in the existing portion of a configuration directory. */
async function canonicalDirectory(input: string): Promise<string> {
  if (!isAbsolute(input) || input.includes('\0')) throw new Error('Claude config directory must be absolute')
  let path = resolve(input)
  const suffix: string[] = []
  for (;;) {
    try { return join(await realpath(path), ...suffix.reverse()) } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error
      const parent = dirname(path)
      if (parent === path) throw error
      suffix.push(path.slice(parent === '/' ? 1 : parent.length + 1))
      path = parent
    }
  }
}

export async function claudeConfigurationKey(configDir: string): Promise<string> {
  return `claude-code:${createHash('sha256').update(await canonicalDirectory(configDir)).digest('hex')}`
}

export interface ClaudeIntegration {
  readonly configurationKey: string
  check(signal: AbortSignal): Promise<IntegrationCheck>
  install(signal: AbortSignal): Promise<InstallationResult>
}

export interface ClaudeIntegrationOptions {
  readonly configDir: string
  readonly environment: NodeJS.ProcessEnv
  /** Absolute path to the already-installed PowerContext CLI. */
  readonly powercontextExecutable: string
  /** Explicit endpoint selected by the user for installation, never an authorization token. */
  readonly serverUrl?: string
}

type RunResult = { code: number | null; stdout: string; interrupted: boolean }

/** Cancel the entire process group and wait for it to disappear before settling. */
async function runCommand(executable: string, args: string[], env: NodeJS.ProcessEnv, signal: AbortSignal): Promise<RunResult> {
  signal.throwIfAborted()
  return await new Promise((resolvePromise, reject) => {
    const child = spawn(executable, args, { env, shell: false, detached: true, stdio: ['ignore', 'pipe', 'ignore'] })
    let output = ''
    let interrupted = false
    let killTimer: ReturnType<typeof setTimeout> | undefined
    const stop = () => {
      interrupted = true
      if (!child.pid) return
      try { process.kill(-child.pid, 'SIGTERM') } catch { /* process has already exited */ }
      killTimer ??= setTimeout(() => {
        try { process.kill(-child.pid!, 'SIGKILL') } catch { /* process has already exited */ }
      }, 1_000)
    }
    signal.addEventListener('abort', stop, { once: true })
    if (signal.aborted) stop()
    child.stdout.on('data', (chunk: Buffer) => {
      output += chunk.toString('utf8')
      if (Buffer.byteLength(output) > 1024 * 1024) stop()
    })
    child.once('error', error => {
      signal.removeEventListener('abort', stop)
      if (killTimer) clearTimeout(killTimer)
      reject(error)
    })
    child.once('close', code => {
      const finish = () => {
        if (child.pid) {
          try { process.kill(-child.pid, 0); setTimeout(finish, 50); return } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== 'ESRCH') { setTimeout(finish, 50); return }
          }
        }
        signal.removeEventListener('abort', stop)
        if (killTimer) clearTimeout(killTimer)
        resolvePromise({ code, stdout: output, interrupted })
      }
      finish()
    })
  })
}

function hostResult(stdout: string): Record<string, any> | undefined {
  try {
    const value = JSON.parse(stdout)
    const host = value?.hosts?.['claude-code']
    return host && typeof host === 'object' && !Array.isArray(host) ? host : undefined
  } catch { return undefined }
}

export async function createClaudeIntegration(options: ClaudeIntegrationOptions): Promise<ClaudeIntegration> {
  if (!isAbsolute(options.powercontextExecutable) || options.powercontextExecutable.includes('\0')) {
    throw new Error('PowerContext executable must be absolute')
  }
  if (options.serverUrl) {
    const url = new URL(options.serverUrl)
    if (!['http:', 'https:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
      throw new Error('Invalid PowerContext Server URL')
    }
  }
  const configurationKey = await claudeConfigurationKey(options.configDir)
  const configDir = await canonicalDirectory(options.configDir)
  const env = { ...options.environment }
  if (env.CLAUDE_CONFIG_DIR !== undefined) {
    if (await canonicalDirectory(env.CLAUDE_CONFIG_DIR) !== configDir) {
      throw new Error('Claude configuration directory does not match the target environment')
    }
  } else if (await canonicalDirectory(join(env.HOME ?? homedir(), '.claude')) !== configDir) {
    // Claude changes its global configuration file when this variable is set.
    // Preserve the default profile's unset state; select custom profiles explicitly.
    env.CLAUDE_CONFIG_DIR = configDir
  }
  const execute = async (args: string[], signal: AbortSignal) => {
    if (await canonicalDirectory(options.configDir) !== configDir) throw new Error('Claude configuration directory changed')
    return runCommand(options.powercontextExecutable, args, env, signal)
  }
  return {
    configurationKey,
    async check(signal): Promise<IntegrationCheck> {
      try {
        const result = await execute(['doctor', 'claude-code', '--json'], signal)
        signal.throwIfAborted()
        if (result.interrupted) return { state: 'unknown' }
        const host = hostResult(result.stdout)
        const cli = host?.checks?.claude_code
        const plugin = host?.checks?.plugin
        if (!cli || !plugin || typeof cli.status !== 'string' || typeof plugin.status !== 'string') return { state: 'unknown' }
        if (cli.status !== 'ok') return { state: 'needs_user_action', action: 'configure_target' }
        if (plugin.status === 'ok') {
          const readiness = host?.checks?.server_readiness ?? host?.checks?.server
          const liveness = host?.checks?.server_liveness
          if (!readiness || (liveness && liveness.status !== 'ok')) return { state: 'unknown' }
          if (readiness.status !== 'ok') return { state: 'needs_user_action', action: 'configure_target' }
          return { state: 'ready' }
        }
        if (plugin.status !== 'failed' || typeof plugin.detail !== 'string') return { state: 'unknown' }
        if (plugin.detail === 'PowerContext plugin is not installed') return { state: 'missing', repairable: true }
        if (plugin.detail.includes('enabled=False')) return { state: 'disabled', repairable: true }
        return { state: 'broken', repairable: true }
      } catch {
        if (signal.aborted) signal.throwIfAborted()
        return { state: 'unknown' }
      }
    },
    async install(signal): Promise<InstallationResult> {
      signal.throwIfAborted()
      const args = ['setup', 'claude-code', '--json']
      if (options.serverUrl) args.push('--server-url', options.serverUrl)
      try {
        const result = await execute(args, signal)
        if (result.interrupted || signal.aborted) return { state: 'unknown' }
        const host = hostResult(result.stdout)
        return host?.setup === 'complete' ? { state: 'completed' } : { state: 'unknown' }
      } catch { return { state: 'unknown' } }
    },
  }
}
