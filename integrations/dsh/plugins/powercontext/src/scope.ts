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
  if (!['http:', 'https:', 'ssh:', 'git:'].includes(parsed.protocol) || !parsed.hostname) {
    return undefined
  }
  const host = parsed.port ? `${parsed.hostname.toLowerCase()}:${parsed.port}` : parsed.hostname.toLowerCase()
  const path = normalizePath(parsed.pathname)
  return path ? `${host}/${path}` : undefined
}

export function spawnGit(cwd: string, args: string[]): Promise<string | undefined> {
  return new Promise((resolveResult) => {
    const child = spawn('git', args, { cwd, windowsHide: true })
    const chunks: Buffer[] = []
    const timer = setTimeout(() => {
      child.kill()
      resolveResult(undefined)
    }, 2000)
    child.stdout.on('data', (chunk: Buffer) => chunks.push(chunk))
    child.on('error', () => {
      clearTimeout(timer)
      resolveResult(undefined)
    })
    child.on('close', (code) => {
      clearTimeout(timer)
      if (code !== 0) {
        resolveResult(undefined)
        return
      }
      const text = Buffer.concat(chunks).toString('utf8').trim()
      resolveResult(text || undefined)
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
