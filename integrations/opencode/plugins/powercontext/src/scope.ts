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
  return candidate.length <= MAX_SCOPE_LENGTH
    ? candidate
    : `${prefix}:sha256:${createHash('sha256').update(value).digest('hex')}`
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
  if (scpMatch?.groups?.host && scpMatch.groups.path) {
    const path = normalizePath(scpMatch.groups.path)
    return path ? `${scpMatch.groups.host.toLowerCase()}/${path}` : undefined
  }
  try {
    const parsed = new URL(value)
    if (!['http:', 'https:', 'ssh:', 'git:'].includes(parsed.protocol) || !parsed.hostname) return undefined
    const host = parsed.port ? `${parsed.hostname.toLowerCase()}:${parsed.port}` : parsed.hostname.toLowerCase()
    const path = normalizePath(parsed.pathname)
    return path ? `${host}/${path}` : undefined
  } catch {
    return undefined
  }
}

export function spawnGit(cwd: string, args: string[]): Promise<string | undefined> {
  return new Promise((finish) => {
    const child = spawn('git', args, { cwd, windowsHide: true })
    const chunks: Buffer[] = []
    let settled = false
    const done = (value: string | undefined) => {
      if (settled) return
      settled = true
      clearTimeout(timer)
      finish(value)
    }
    const timer = setTimeout(() => {
      child.kill()
      done(undefined)
    }, 2000)
    timer.unref()
    child.stdout.on('data', (chunk: Buffer) => chunks.push(chunk))
    child.on('error', () => done(undefined))
    child.on('close', (code) => done(code === 0 ? Buffer.concat(chunks).toString('utf8').trim() || undefined : undefined))
  })
}

export async function deriveScopeId(
  cwd: string,
  options: { configuredScopeId?: string; git?: GitRunner } = {},
): Promise<string> {
  if (options.configuredScopeId) {
    const explicit = options.configuredScopeId
    return explicit.length <= MAX_SCOPE_LENGTH ? explicit : `sha256:${createHash('sha256').update(explicit).digest('hex')}`
  }
  const git = options.git ?? spawnGit
  const root = resolve(await git(cwd, ['rev-parse', '--show-toplevel']) || cwd)
  const remote = await git(root, ['config', '--get', 'remote.origin.url'])
  const normalized = remote ? normalizeGitRemote(remote) : undefined
  return normalized ? bounded('git', normalized) : `local:${createHash('sha256').update(root).digest('hex')}`
}
