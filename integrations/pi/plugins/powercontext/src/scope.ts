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

import { createHash } from 'node:crypto'
import { spawn } from 'node:child_process'
import { resolve } from 'node:path'

const MAX_SCOPE_LENGTH = 256
const SCP_REMOTE = /^(?:[^@/\s]+@)?(?<host>[^:/\s]+):(?<path>.+)$/

export type GitRunner = (cwd: string, args: string[]) => Promise<string | undefined>

function bounded(prefix: string, value: string): string {
  const candidate = `${prefix}:${value}`
  if (candidate.length <= MAX_SCOPE_LENGTH) return candidate
  return `${prefix}:sha256:${createHash('sha256').update(value).digest('hex')}`
}

function boundedExplicit(value: string): string {
  if (value.length <= MAX_SCOPE_LENGTH) return value
  return `sha256:${createHash('sha256').update(value).digest('hex')}`
}

function normalizePath(path: string): string {
  let normalized = path.replaceAll('\\', '/').split('/').filter(Boolean).join('/')
  if (normalized.endsWith('.git')) normalized = normalized.slice(0, -4)
  return normalized.replace(/\/+$/, '')
}

export function normalizeGitRemote(remote: string): string | undefined {
  const value = remote.trim()
  if (!value) return undefined
  const scpMatch = !value.includes('://') ? value.match(SCP_REMOTE) : null
  if (scpMatch?.groups) {
    const host = scpMatch.groups.host.toLowerCase()
    const path = normalizePath(scpMatch.groups.path)
    return path ? `${host}/${path}` : undefined
  }
  let parsed: URL
  try {
    parsed = new URL(value)
  } catch {
    return undefined
  }
  if (!['http:', 'https:', 'ssh:', 'git:'].includes(parsed.protocol) || !parsed.hostname) return undefined
  const host = parsed.port ? `${parsed.hostname.toLowerCase()}:${parsed.port}` : parsed.hostname.toLowerCase()
  const path = normalizePath(parsed.pathname)
  return path ? `${host}/${path}` : undefined
}

export function spawnGit(cwd: string, args: string[]): Promise<string | undefined> {
  return new Promise((finish) => {
    const child = spawn('git', args, { cwd, windowsHide: true })
    const chunks: Buffer[] = []
    let settled = false
    const resolveOnce = (result: string | undefined) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      finish(result)
    }
    const timer = setTimeout(() => {
      child.kill()
      resolveOnce(undefined)
    }, 2000)
    timer.unref()
    child.stdout.on('data', (chunk: Buffer) => chunks.push(chunk))
    child.on('error', () => resolveOnce(undefined))
    child.on('close', (code) => {
      if (code !== 0) {
        resolveOnce(undefined)
        return
      }
      const output = Buffer.concat(chunks).toString('utf8').trim()
      resolveOnce(output || undefined)
    })
  })
}

export async function deriveScopeId(
  cwd: string,
  options: { configuredScopeId?: string; git?: GitRunner } = {},
): Promise<string> {
  if (options.configuredScopeId) return boundedExplicit(options.configuredScopeId)
  const git = options.git ?? spawnGit
  const rootValue = await git(cwd, ['rev-parse', '--show-toplevel'])
  const projectRoot = resolve(rootValue || cwd)
  const remote = await git(projectRoot, ['config', '--get', 'remote.origin.url'])
  const normalized = remote ? normalizeGitRemote(remote) : undefined
  if (normalized) return bounded('git', normalized)
  return `local:${createHash('sha256').update(projectRoot).digest('hex')}`
}
